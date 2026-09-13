"""
memory_experiment.py — Engine: real controlled memory pressure.

TWO IMPORTANT FIXES vs. the first version:

1. SAFETY VALIDATION (this is literally the "Safety Validation Layer"
   described in the design doc, section 27, done for real): before
   allocating, we check real available memory via /proc/meminfo and
   refuse/clamp the request if it would push the system too close to
   its limit. This is what actually stops the crash.

2. PROCESS ISOLATION: the allocation happens in a separate child
   process (multiprocessing), not inside the API server itself. If the
   kernel's OOM killer ever does step in, it kills the small child
   process — your FastAPI server (the parent) keeps running and the
   dashboard stays online. This is standard practice for any tool that
   deliberately stresses a resource (this is exactly how `stress-ng`
   isolates its workers).
"""
import multiprocessing
import time

import monitor
import trace as tracelog

_current_process = None
_current_target_mb = None


def _hold_memory_until_killed(target_mb):
    """Runs ONLY in the child process. Touches every page so the kernel
    commits real physical memory. Exiting/being killed frees it all."""
    block = bytearray(target_mb * 1024 * 1024)
    for i in range(0, len(block), 4096):
        block[i] = 1
    while True:
        time.sleep(1)  # hold until the parent terminates us


def safety_check(target_mb):
    """Real safety validation against real /proc/meminfo state — never
    allow an allocation that would consume more than half of what's
    currently actually available."""
    stats = monitor.memory_stats()
    safe_max_mb = int(stats["available_mb"] * 0.5)
    return {
        "requested_mb": target_mb,
        "available_mb": stats["available_mb"],
        "safe_max_mb": safe_max_mb,
        "allowed": target_mb <= safe_max_mb and safe_max_mb > 32,
    }


def run_experiment(target_mb=256, hold_seconds=3):
    global _current_process, _current_target_mb

    if _current_process and _current_process.is_alive():
        return {"error": "An experiment is already holding memory — release it first."}

    before = monitor.memory_stats()
    tracelog.log("MEASUREMENT", "Memory Lab", "Before snapshot", before)

    check = safety_check(target_mb)
    tracelog.log("SAFETY", "Memory Lab", "Safety validation against /proc/meminfo", check)

    if not check["allowed"]:
        clamped = max(32, check["safe_max_mb"])
        tracelog.log("SAFETY", "Memory Lab",
                     f"Requested {target_mb}MB exceeds safe limit ({check['safe_max_mb']}MB) "
                     f"— clamped to {clamped}MB to protect the system")
        target_mb = clamped

    _current_target_mb = target_mb
    _current_process = multiprocessing.Process(
        target=_hold_memory_until_killed, args=(target_mb,), daemon=True)
    _current_process.start()
    tracelog.log("OPERATION", "Memory Lab",
                 f"Allocating {target_mb}MB in isolated child process (PID {_current_process.pid})")

    # give the child a moment to actually fault in the pages, then measure
    settle = min(hold_seconds, 3)
    time.sleep(settle)
    during = monitor.memory_stats()
    tracelog.log("MEASUREMENT", "Memory Lab", "During-pressure snapshot", during)

    incident = None
    if during["used_percent"] > 85:
        incident = {"type": "memory_pressure", "severity": "HIGH",
                    "detail": f"Memory usage {during['used_percent']}% after allocating {target_mb}MB"}
        tracelog.log("RESULT", "Memory Lab", "Pressure incident detected", incident)

    return {
        "target_mb": target_mb,
        "safety_check": check,
        "before": before,
        "during_pressure": during,
        "delta_used_mb": round(during["used_mb"] - before["used_mb"], 1),
        "incident": incident,
        "process_pid": _current_process.pid,
        "note": "Memory stays held until you click Release — the allocation "
                "lives in an isolated process so it can't crash the server.",
    }


def release():
    """Controlled intervention: terminate the isolated child process,
    which instantly frees all the memory it held."""
    global _current_process, _current_target_mb
    tracelog.log("USER_ACTION", "Memory Lab", "Release allocated memory")
    before = monitor.memory_stats()

    if _current_process and _current_process.is_alive():
        _current_process.terminate()
        _current_process.join(timeout=2)
    _current_process = None
    _current_target_mb = None

    time.sleep(0.5)
    after = monitor.memory_stats()
    tracelog.log("RESULT", "Memory Lab", "Memory released (isolated process terminated)", after)
    return {"before_release": before, "after_release": after}


def status():
    global _current_process, _current_target_mb
    holding = bool(_current_process and _current_process.is_alive())
    if not holding and _current_target_mb is not None:
        _current_target_mb = None  # process died externally (e.g. real OOM kill) - clear stale state
    return {"holding": holding, "target_mb": _current_target_mb}


if __name__ == "__main__":
    import json
    print(json.dumps(run_experiment(target_mb=128, hold_seconds=2), indent=2))
    print(json.dumps(release(), indent=2))
