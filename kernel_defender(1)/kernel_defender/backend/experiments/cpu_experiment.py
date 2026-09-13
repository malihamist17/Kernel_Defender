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
