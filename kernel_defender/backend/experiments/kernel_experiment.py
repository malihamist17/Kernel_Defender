"""
kernel_experiment.py — the "RUN KERNEL TEST" experiment: verifies real
communication with the kernel module, step by step, exactly as described
in the design doc's flow (check module -> check /proc -> read -> change
mode -> read again -> compare).
"""
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "kernel"))
import kernel_interface as ki
import trace as tracelog


def run_kernel_test():
    steps = []

    def step(name, passed, detail):
        steps.append({"name": name, "passed": passed, "detail": detail})
        tracelog.log("KERNEL_EVENT" if passed else "SAFETY", "Kernel Test", f"{name}: {'PASS' if passed else 'FAIL'} - {detail}")
        return passed

    if not step("Module detected", ki.is_module_loaded(),
                "Checked for /proc/kernel_defender" if ki.is_module_loaded()
                else "Module not loaded - build/insmod it first (see STEPS.md)"):
        return {"pass": False, "steps": steps}

    status1 = ki.read_status()
    if not step("Interface readable", status1.get("loaded", False) and "mode" in status1,
                "Read /proc/kernel_defender successfully" if "mode" in status1
                else "Could not parse module output"):
        return {"pass": False, "steps": steps}

    step("State read", True, f"Current mode: {status1.get('mode')}")

    # pick a mode different from the current one so the change is provable
    target_mode = "MONITOR" if status1.get("mode") != "MONITOR" else "PROTECT"
    change_result = ki.set_mode(target_mode)
    if not step("Mode changed", change_result.get("success", False),
                f"Requested mode -> {target_mode}"):
        return {"pass": False, "steps": steps}

    status2 = ki.read_status()
    verified = status2.get("mode") == target_mode
    step("State verified", verified,
         f"Re-read module: mode is now '{status2.get('mode')}' (expected '{target_mode}')")

    overall = all(s["passed"] for s in steps)
    return {"pass": overall, "steps": steps, "before": status1, "after": status2}


def test_with_cpu():
    """Bridges the kernel module to the EXISTING CPU experiment — reuses
    cpu_experiment.py rather than building a second CPU system."""
    from experiments import cpu_experiment
    result = cpu_experiment.run_experiment(seconds=3)
    if result["after"]["governor"] != "unavailable" or result["runtime_sec"]:
        event = f"CPU integration test ran ({result['runtime_sec']}s workload)"
        ki.record_event(event)
        tracelog.log("KERNEL_EVENT", "Kernel Integration", event)
    return {"cpu_result": result, "kernel_status": ki.read_status()}


def test_with_memory():
    """Bridges the kernel module to the EXISTING memory experiment."""
    from experiments import memory_experiment
    result = memory_experiment.run_experiment(target_mb=128, hold_seconds=2)
    event = f"Memory integration test ran ({result.get('target_mb')}MB)"
    ki.record_event(event)
    tracelog.log("KERNEL_EVENT", "Kernel Integration", event)
    memory_experiment.release()
    return {"memory_result": result, "kernel_status": ki.read_status()}


if __name__ == "__main__":
    import json
    print(json.dumps(run_kernel_test(), indent=2))
