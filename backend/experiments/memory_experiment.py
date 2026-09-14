"""
memory_experiment.py — Engine: real controlled memory pressure.

Allocates real memory (a bytearray held in the process, actually touched
so the kernel commits real pages — not just reserved virtual address
space) and measures the REAL system-wide effect via /proc/meminfo
before/after. This is genuinely how memory-pressure testing tools
(e.g. `stress-ng --vm`) work under the hood.
"""
import time

import monitor
import trace as tracelog

_held_blocks = []  # keeps allocated memory alive between start/release calls


def run_experiment(target_mb=512, hold_seconds=5):
    before = monitor.memory_stats()
    tracelog.log("MEASUREMENT", "Memory Lab", "Before snapshot", before)

    tracelog.log("USER_ACTION", "Memory Lab", f"Allocate {target_mb} MB")
    block = bytearray(target_mb * 1024 * 1024)
    # touch every page (4KB) so the kernel actually commits real physical
    # memory instead of leaving it as unfaulted virtual address space
    for i in range(0, len(block), 4096):
        block[i] = 1
    _held_blocks.append(block)
    tracelog.log("INTERFACE", "Memory Lab", "Pages touched -> real RSS growth")

    time.sleep(hold_seconds)
    during = monitor.memory_stats()
    tracelog.log("MEASUREMENT", "Memory Lab", "During-pressure snapshot", during)

    incident = None
    if during["used_percent"] > 85:
        incident = {"type": "memory_pressure", "severity": "HIGH",
                    "detail": f"Memory usage {during['used_percent']}% after allocating {target_mb}MB"}
        tracelog.log("RESULT", "Memory Lab", "Pressure incident detected", incident)

    return {
        "target_mb": target_mb,
        "before": before,
        "during_pressure": during,
        "delta_used_mb": round(during["used_mb"] - before["used_mb"], 1),
        "incident": incident,
    }


def release():
    """Controlled intervention: free the held memory and measure recovery."""
    tracelog.log("USER_ACTION", "Memory Lab", "Release allocated memory")
    before = monitor.memory_stats()
    _held_blocks.clear()
    time.sleep(0.5)
    after = monitor.memory_stats()
    tracelog.log("RESULT", "Memory Lab", "Memory released", after)
    return {"before_release": before, "after_release": after}


if __name__ == "__main__":
    import json
    print(json.dumps(run_experiment(target_mb=256, hold_seconds=2), indent=2))
    print(json.dumps(release(), indent=2))
