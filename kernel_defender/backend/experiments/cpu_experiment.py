"""
cpu_experiment.py — Engine 1 controlled experiment.

Runs a real CPU-bound workload (pure Python busy-loop -> real 100% core
usage, visible in `top`/`/proc/stat`) and lets the user change the real
CPUFreq governor via /sys, then measures before/after runtime, frequency
and temperature.

IMPORTANT: writing to scaling_governor requires root
(sudo) and a cpufreq driver that supports userspace-selectable governors.
On a VM/container this file may not exist at all — the code degrades
gracefully and reports "unavailable" instead of crashing, which you
should mention as a hardware-dependency limitation in your report.
"""
import glob
import multiprocessing
import time

import monitor
import trace as tracelog


GOVERNOR_PATH_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"


def _busy_loop(seconds, result_queue=None):
    end = time.time() + seconds
    x = 0
    while time.time() < end:
        x = (x * 1234567 + 1) % 1_000_000_007
    if result_queue:
        result_queue.put(x)


def run_cpu_bound_workload(seconds=5, cores=None):
    """Actually saturates `cores` CPUs for `seconds` — a real, measurable
    workload, not a mock number."""
    cores = cores or multiprocessing.cpu_count()
    procs = [multiprocessing.Process(target=_busy_loop, args=(seconds,))
             for _ in range(cores)]
    t0 = time.time()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    return round(time.time() - t0, 3)


def set_governor(governor_name):
    """Attempts to write the real cpufreq governor for every core.
    Returns per-core success/failure so the Safety layer can report exactly
    what happened (never silently pretend it worked)."""
    available = monitor.cpu_available_governors()
    if available and governor_name not in available:
        return {"applied": False, "reason": f"'{governor_name}' not in {available}"}

    results = {}
    for path in sorted(glob.glob(GOVERNOR_PATH_GLOB)):
        core = path.split("/")[5]
        try:
            with open(path, "w") as f:
                f.write(governor_name)
            results[core] = "ok"
        except PermissionError:
            results[core] = "permission_denied (needs sudo)"
        except (IOError, FileNotFoundError):
            results[core] = "unavailable"
    applied = any(v == "ok" for v in results.values())
    return {"applied": applied, "per_core": results}


def run_experiment(seconds=5, target_governor=None):
    """Full before/after pipeline matching the faculty doc's Step 1-11 trace."""
    trace = []

    before = {
        "governor": monitor.cpu_governor(),
        "freq_mhz": monitor.cpu_frequencies_mhz(),
        "temp_c": monitor.cpu_temperature_c(),
    }
    trace.append({"step": "BEFORE_SNAPSHOT", "data": before})

    governor_change = None
    if target_governor:
        governor_change = set_governor(target_governor)
        trace.append({"step": "SAFETY_AND_APPLY_GOVERNOR", "data": governor_change})

    trace.append({"step": "RUN_WORKLOAD", "data": f"{seconds}s CPU-bound on all cores"})
    runtime = run_cpu_bound_workload(seconds=seconds)

    after = {
        "governor": monitor.cpu_governor(),
        "freq_mhz": monitor.cpu_frequencies_mhz(),
        "temp_c": monitor.cpu_temperature_c(),
    }
    trace.append({"step": "AFTER_SNAPSHOT", "data": after})

    return {
        "runtime_sec": runtime,
        "before": before,
        "after": after,
        "governor_change": governor_change,
        "trace": trace,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_experiment(seconds=3, target_governor="performance"), indent=2))


# =========================== CONTINUOUS STRESS TEST ===========================
# Matches the doc's CPU Performance & Safety Module: an indefinite stress test
# (not a fixed duration) with Start/Stop control and an independent safety
# monitor thread that can auto-stop the workload if things get too hot.

import threading

_stress_state = {
    "running": False,
    "baseline": None,
    "started_at": None,
    "processes": [],
    "stop_flag": None,
    "safety_stopped": False,
    "safety_reason": None,
    "temp_limit_c": 80,  # a PROJECT safety threshold, not a hardware spec -
                          # real thermal limits vary by CPU; see doc section 8.
}


def record_baseline():
    return {
        "cpu_percent": monitor.cpu_utilization_percent(0.15),
        "per_core_percent": monitor.per_core_utilization_percent(0.1),
        "freq_mhz": monitor.cpu_frequencies_mhz(),
        "governor": monitor.cpu_governor(),
        "temp_c": monitor.cpu_temperature_c(),
    }


def _safety_monitor_loop(stop_flag):
    """Runs in a background thread for the whole duration of the stress
    test. This is the 'guardian' behavior from doc section 8 — it doesn't
    just record data, it can actually terminate the workload."""
    while not stop_flag["stop"]:
        temp = monitor.cpu_temperature_c()
        if temp is not None and temp >= _stress_state["temp_limit_c"]:
            tracelog.log("SAFETY", "CPU Safety Monitor",
                         f"Temperature {temp}°C reached project safety threshold "
                         f"({_stress_state['temp_limit_c']}°C) — auto-stopping stress test")
            _stress_state["safety_stopped"] = True
            _stress_state["safety_reason"] = f"Temperature reached {temp}°C (limit: {_stress_state['temp_limit_c']}°C)"
            stop_workload()
            break
        time.sleep(0.5)


def start_stress_test(cores=None, temp_limit_c=80):
    if _stress_state["running"]:
        return {"error": "A stress test is already running."}

    _stress_state["baseline"] = record_baseline()
    _stress_state["temp_limit_c"] = temp_limit_c
    _stress_state["safety_stopped"] = False
    _stress_state["safety_reason"] = None
    tracelog.log("MEASUREMENT", "CPU Safety Module", "Baseline recorded", _stress_state["baseline"])

    cores = cores or multiprocessing.cpu_count()
    stop_flag = multiprocessing.Value("b", False)
    procs = [multiprocessing.Process(target=_stress_worker, args=(stop_flag,)) for _ in range(cores)]
    for p in procs:
        p.start()

    _stress_state["processes"] = procs
    _stress_state["stop_flag"] = stop_flag
    _stress_state["running"] = True
    _stress_state["started_at"] = time.time()

    safety_stop_flag = {"stop": False}
    _stress_state["_safety_thread_flag"] = safety_stop_flag
    threading.Thread(target=_safety_monitor_loop, args=(safety_stop_flag,), daemon=True).start()

    tracelog.log("OPERATION", "CPU Safety Module",
                 f"Started continuous stress test on {cores} core(s), safety limit {temp_limit_c}°C")
    return {"started": True, "cores": cores, "baseline": _stress_state["baseline"], "temp_limit_c": temp_limit_c}


def _stress_worker(stop_flag):
    x = 0
    while not stop_flag.value:
        x = (x * 1234567 + 1) % 1_000_000_007


def stop_workload():
    """Internal: stops the actual workload processes (called by the user's
    STOP button, or automatically by the safety monitor)."""
    if _stress_state["stop_flag"] is not None:
        _stress_state["stop_flag"].value = True
    for p in _stress_state["processes"]:
        p.join(timeout=2)
        if p.is_alive():
            p.terminate()
    if "_safety_thread_flag" in _stress_state and _stress_state["_safety_thread_flag"]:
        _stress_state["_safety_thread_flag"]["stop"] = True
    _stress_state["running"] = False
    _stress_state["processes"] = []
    _stress_state["stop_flag"] = None


def stop_stress_test():
    if not _stress_state["running"]:
        return {"error": "No stress test is currently running."}
    duration = round(time.time() - _stress_state["started_at"], 1)
    tracelog.log("USER_ACTION", "CPU Safety Module", "STOP button pressed")
    stop_workload()
    after = record_baseline()
    tracelog.log("RESULT", "CPU Safety Module", "Stress test stopped", after)
    return {
        "stopped": True,
        "duration_sec": duration,
        "baseline": _stress_state["baseline"],
        "after": after,
        "status": "Stopped safely by user" if not _stress_state["safety_stopped"]
                  else f"Auto-stopped by safety monitor: {_stress_state['safety_reason']}",
    }


def stress_live_state():
    """Polled by the frontend while the stress test runs."""
    live = record_baseline()
    running = _stress_state["running"]
    return {
        "running": running,
        "baseline": _stress_state["baseline"],
        "live": live,
        "duration_sec": round(time.time() - _stress_state["started_at"], 1) if running and _stress_state["started_at"] else 0,
        "safety_stopped": _stress_state["safety_stopped"],
        "safety_reason": _stress_state["safety_reason"],
        "temp_limit_c": _stress_state["temp_limit_c"],
    }
