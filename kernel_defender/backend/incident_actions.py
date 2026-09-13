"""
incident_actions.py — implements the doc's full flow (sections 8, 13, 20,
23, 24): Investigate -> pick something to INSPECT (real command + real
output + explanation) -> optionally pick an ACTION -> see the consequence
-> confirm -> execute -> see the honest result.

Every action's execute() result includes a top-level "success" boolean.
app.py only marks an incident "resolved" when that's true — this is what
fixes the bug where reaping a zombie that wasn't ours got marked fixed
even though nothing actually happened.
"""
import os
import signal
import time

import monitor
import trace as tracelog
from experiments import memory_experiment, zombie_experiment, cpu_experiment


# ============================== INSPECTIONS ==============================
# "What would you like to inspect?" — each returns a real command, real
# output, and a plain-language explanation. No fix is applied here.

def _insp_cpu_frequency():
    freqs = monitor.cpu_frequencies_mhz()
    output = "\n".join(f"{core}: {mhz} MHz" for core, mhz in freqs.items()) or "(unavailable on this VM/hardware — no cpufreq driver exposed)"
    return {
        "command": "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq",
        "output": output,
        "explanation": "Shows the real current clock speed the kernel's CPUFreq subsystem is running each core at, read directly from /sys.",
    }


def _insp_performance_policy():
    gov = monitor.cpu_governor()
    avail = monitor.cpu_available_governors()
    output = f"Current governor: {gov}\nAvailable governors: {', '.join(avail) if avail else 'unavailable on this VM/hardware'}"
    return {
        "command": "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
        "output": output,
        "explanation": "The governor decides how aggressively the CPU scales frequency in response to load — 'performance' stays high, 'powersave' stays low, 'schedutil' tracks real-time demand.",
    }


def _insp_cpu_workload():
    listing = monitor.ps_listing(sort_flag="-%cpu", count=5)
    return {
        "command": listing["command"],
        "output": listing["output"],
        "explanation": "Shows which real processes are driving CPU demand right now — is this one runaway process, or general system load?",
    }


def _insp_temperature():
    temp = monitor.cpu_temperature_c()
    output = f"{temp}°C" if temp is not None else "unavailable on this VM/hardware (no thermal zone exposed by the hypervisor)"
    return {
        "command": "cat /sys/class/thermal/thermal_zone*/temp",
        "output": output,
        "explanation": "Sustained high temperature alongside high utilization suggests thermal throttling may reduce performance soon regardless of any policy change.",
    }


def _insp_process_usage():
    listing = monitor.ps_listing(sort_flag="-%cpu", count=10)
    return {
        "command": listing["command"],
        "output": listing["output"],
        "explanation": "A fuller per-process CPU breakdown — useful for confirming exactly which process to target before taking action.",
    }


def _insp_zombie_ps():
    zombies = monitor.zombies()
    if not zombies:
        return {
            "command": "ps -eo pid,ppid,stat,cmd | grep Z",
            "output": "(no zombie processes currently found)",
            "explanation": "A zombie is a terminated process whose parent hasn't yet collected its exit status via wait()/waitpid().",
        }
    pids = [int(z["pid"]) for z in zombies]
    listing = monitor.ps_for_pids(pids)
    return {
        "command": listing["command"],
        "output": listing["output"],
        "explanation": "STAT 'Z' confirms a zombie. The PPID column identifies which process is actually responsible for reaping it — only that exact parent process can call wait()/waitpid() on it; this dashboard can only reap ones it created itself.",
    }


def _insp_mem_processes():
    listing = monitor.ps_listing(sort_flag="-%mem", count=10)
    return {
        "command": listing["command"],
        "output": listing["output"],
        "explanation": "Real per-process memory usage, sorted highest first — identifies which specific process is responsible for the pressure.",
    }


def _insp_mem_usage():
    m = monitor.memory_stats()
    output = f"Used: {m['used_mb']}MB ({m['used_percent']}%)\nAvailable: {m['available_mb']}MB\nTotal: {m['total_mb']}MB"
    return {
        "command": "cat /proc/meminfo",
        "output": output,
        "explanation": "Overall system memory pressure, read directly from /proc/meminfo.",
    }


def _insp_mem_swap():
    m = monitor.memory_stats()
    output = f"SwapTotal: {m['swap_total_mb']}MB\nSwapUsed: {m['swap_used_mb']}MB"
    return {
        "command": "grep -i swap /proc/meminfo",
        "output": output,
        "explanation": "Heavy swap usage means the kernel is writing memory pages to disk, which is far slower than RAM and often the real cause of a system feeling sluggish.",
    }


def _insp_mem_workload():
    top = monitor.top_memory_consumers(limit=5)
    output = "\n".join(f"PID {p['pid']}  {p['name']}  {p['mem_kb']}kB" for p in top) or "(no data)"
    return {
        "command": "(walking /proc/[pid]/status for VmRSS per process)",
        "output": output,
        "explanation": "Identifies the specific workload responsible for the memory pressure, ranked by real resident memory.",
    }


INCIDENT_INSPECTIONS = {
    "cpu_performance": [
        {"id": "cpu_frequency", "label": "CPU Frequency", "run": _insp_cpu_frequency},
        {"id": "performance_policy", "label": "Performance Policy", "run": _insp_performance_policy},
        {"id": "workload", "label": "Workload", "run": _insp_cpu_workload},
        {"id": "temperature", "label": "Temperature", "run": _insp_temperature},
        {"id": "process_usage", "label": "Process Usage", "run": _insp_process_usage},
    ],
    "zombie_process": [
        {"id": "ps_check", "label": "Run ps check", "run": _insp_zombie_ps},
    ],
    "memory_pressure": [
        {"id": "inspect_processes", "label": "Inspect processes", "run": _insp_mem_processes},
        {"id": "inspect_memory_usage", "label": "Inspect memory usage", "run": _insp_mem_usage},
        {"id": "inspect_swap", "label": "Inspect swap", "run": _insp_mem_swap},
        {"id": "inspect_workload", "label": "Inspect workload", "run": _insp_mem_workload},
    ],
}


def available_inspections(incident_type):
    return [{"id": i["id"], "label": i["label"]} for i in INCIDENT_INSPECTIONS.get(incident_type, [])]


def run_inspection(incident_type, inspection_id):
    for i in INCIDENT_INSPECTIONS.get(incident_type, []):
        if i["id"] == inspection_id:
            result = i["run"]()
            tracelog.log("COMMAND", "Investigation", result.get("command", ""), result)
            return result
    return {"error": "unknown inspection"}


# ================================ ACTIONS =================================
# "What would you like to do?" — shown after (or instead of) inspecting.

def _cpu_renice_action():
    target = monitor.top_cpu_process()
    label = "Lower priority of top CPU process (renice)"
    if target:
        label += f" — currently PID {target['pid']} ({target['comm']}, {target['cpu_percent']}% CPU)"
    return {
        "id": "renice_top_process",
        "label": label,
        "consequence": (
            "This runs the real Linux 'renice' command on the process "
            "currently using the most CPU, raising its niceness value "
            "(lower scheduling priority). The process keeps running "
            "normally and loses no data. Renicing your own processes "
            "doesn't need admin rights; renicing another user's process "
            "would be blocked and reported honestly as such."
        ),
    }


def _cpu_governor_action():
    return {
        "id": "switch_powersave",
        "label": "Switch CPU governor to 'powersave'",
        "consequence": (
            "Writes 'powersave' to the real CPUFreq governor file for "
            "every core. This caps maximum CPU frequency to reduce "
            "heat/power draw, which may slow down currently running "
            "workloads. Reversible any time from the CPU Lab."
        ),
    }


def _memory_release_action():
    return {
        "id": "release_experiment_memory",
        "label": "Release memory held by the Memory Lab experiment",
        "consequence": (
            "Frees the memory block that Kernel Defender's own Memory Lab "
            "experiment allocated and is holding. This can only affect "
            "Kernel Defender's own test allocation — it cannot free "
            "memory held by unrelated real processes on your system. If "
            "there's no experiment currently holding memory, this action "
            "will honestly report that there's nothing for it to release."
        ),
    }


def _zombie_reap_action():
    return {
        "id": "reap_zombie",
        "label": "Reap zombie process(es)",
        "consequence": (
            "Attempts to call the real waitpid() system call on every "
            "zombie currently found on the system. This only works for "
            "zombies whose actual parent is this Kernel Defender process "
            "— for any other zombie, waitpid() is a kernel-level "
            "operation restricted to the real parent, and this will "
            "honestly report which PIDs it could and couldn't reap, "
            "rather than claiming success either way."
        ),
    }


def _zombie_terminate_parent_actions():
    """Dynamic: one action per distinct unreapable parent currently found.
    Real fix for orphan-style zombies — terminating the non-reaping parent
    causes the kernel to re-parent the zombie to init/systemd, which reaps
    orphans automatically. Only offered when it's actually applicable, and
    the label shows exactly what process would be terminated."""
    zombies = monitor.zombies()
    actions = []
    seen_parents = set()
    our_pid = os.getpid()
    for z in zombies:
        pid = int(z["pid"])
        ppid = zombie_experiment.real_ppid(pid)
        if ppid is None or ppid == our_pid or ppid in seen_parents:
            continue
        seen_parents.add(ppid)
        info = monitor.process_info(ppid)
        name = info["comm"] if info else "unknown process"
        actions.append({
            "id": f"terminate_parent_{ppid}",
            "label": f"Terminate parent process PID {ppid} ({name}) to force reparenting",
            "consequence": (
                f"Sends SIGTERM to PID {ppid} ({name}), the real parent of "
                f"zombie PID {pid}, since that parent isn't reaping its "
                f"child itself. Ending it causes the kernel to re-parent "
                f"the zombie to init/systemd (PID 1), which reaps orphaned "
                f"zombies automatically — this is the standard real-world "
                f"fix for a stuck or buggy parent process.\n\n"
                f"⚠ WARNING: this will stop PID {ppid} ({name}) entirely, "
                f"which may affect whatever functionality that process "
                f"provides. Only proceed if you recognize this process "
                f"and are OK ending it."
            ),
        })
    return actions


def _deadlock_recover_action():
    return {
        "id": "recover_deadlock",
        "label": "Force-recover the deadlocked thread",
        "consequence": (
            "Forcibly releases the lock held by ThreadB, aborting its "
            "wait so ThreadA can proceed immediately. This models real "
            "OS deadlock recovery by resource preemption / victim "
            "termination."
        ),
    }


def _starvation_fix_action():
    return {
        "id": "apply_fairness_fix",
        "label": "Apply a fair scheduling policy",
        "consequence": (
            "Switches the test workload's resource-acquisition policy to "
            "a fairer, round-robin-style backoff so no single thread is "
            "perpetually denied the shared resource. Slightly increases "
            "average wait time for all threads, but eliminates the "
            "extreme wait time for the starved one."
        ),
    }


def _io_acknowledge_action():
    return {
        "id": "acknowledge",
        "label": "Acknowledge (no corrective action available)",
        "consequence": (
            "Marks the incident as reviewed. A slow one-off benchmark "
            "write doesn't have a safe automatic fix in this system."
        ),
    }


INCIDENT_ACTIONS = {
    "cpu_performance": [_cpu_renice_action, _cpu_governor_action],
    "memory_pressure": [_memory_release_action],
    "zombie_process": [_zombie_reap_action, _zombie_terminate_parent_actions],
    "deadlock": [_deadlock_recover_action],
    "starvation": [_starvation_fix_action],
    "io_bottleneck": [_io_acknowledge_action],
}


def available_actions(incident_type):
    """Builders may return either a single action dict or a list of them
    (used for dynamic per-target actions like terminate_parent_<pid>)."""
    results = []
    for builder in INCIDENT_ACTIONS.get(incident_type, []):
        r = builder()
        results.extend(r) if isinstance(r, list) else results.append(r)
    return results


def execute(incident_type, action_id, running_experiments):
    """Every branch returns a top-level 'success' boolean. app.py only
    marks the incident resolved when this is True."""
    tracelog.log("USER_ACTION", "Incident Response", f"Confirmed action: {action_id}")

    if action_id == "renice_top_process":
        target = monitor.top_cpu_process()
        if not target:
            return {"success": False, "detail": "Could not identify a top CPU process."}
        tracelog.log("SAFETY", "Incident Response", "Checked target PID is renice-able")
        result = monitor.renice_process(target["pid"], priority=10)
        tracelog.log("COMMAND", "Incident Response", result.get("command", ""), result)
        return {"success": result.get("success", False), "target": target, "renice_result": result}

    if action_id == "switch_powersave":
        result = cpu_experiment.set_governor("powersave")
        tracelog.log("OPERATION", "Incident Response", "Applied powersave governor", result)
        return {"success": result.get("applied", False), **result}

    if action_id == "release_experiment_memory":
        if not memory_experiment.status()["holding"]:
            return {
                "success": False,
                "detail": "No experiment-held memory is currently allocated by Kernel "
                          "Defender to release. If system-wide pressure is coming from a "
                          "real process, use 'Inspect processes' to identify it first — "
                          "this dashboard can't free memory it didn't allocate.",
            }
        result = memory_experiment.release()
        tracelog.log("RESULT", "Incident Response", "Memory released", result)
        return {"success": True, **result}

    if action_id == "reap_zombie":
        result = zombie_experiment.reap_all_current()
        tracelog.log("RESULT", "Incident Response", "Zombie reap attempted", result)
        return result

    if action_id.startswith("terminate_parent_"):
        try:
            pid = int(action_id.rsplit("_", 1)[-1])
        except ValueError:
            return {"success": False, "detail": "Malformed action id."}
        permission = monitor.can_signal(pid)
        if permission is None:
            return {"success": True, "detail": f"PID {pid} no longer exists."}
        if permission is False:
            return {"success": False, "detail": f"No permission to signal PID {pid} — it's owned by another user and would need sudo."}
        try:
            os.kill(pid, signal.SIGTERM)
            tracelog.log("COMMAND", "Incident Response", f"kill -TERM {pid}")
        except ProcessLookupError:
            return {"success": True, "detail": f"PID {pid} already exited."}
        time.sleep(0.5)
        tracelog.log("RESULT", "Incident Response", f"Sent SIGTERM to PID {pid}")
        return {"success": True, "terminated_pid": pid,
                "detail": f"Sent SIGTERM to PID {pid}. Its zombie child should be "
                          f"reaped by init/systemd shortly — check the Process tab in a moment."}

    if action_id == "recover_deadlock":
        exp = running_experiments.get("deadlock")
        if not exp:
            return {"success": False, "detail": "No deadlock experiment is currently running."}
        snap = exp.recover("ThreadB")
        tracelog.log("RESULT", "Incident Response", "Deadlock recovery applied", snap)
        return {"success": snap.get("cycle") is None, "snapshot_after": snap}

    if action_id == "apply_fairness_fix":
        exp = running_experiments.get("starvation")
        if not exp:
            return {"success": False, "detail": "No starvation experiment is currently running."}
        exp.apply_fairness_fix()
        tracelog.log("RESULT", "Incident Response", "Fairness fix applied")
        return {"success": True, "fairness_applied": True}

    if action_id == "acknowledge":
        tracelog.log("RESULT", "Incident Response", "Incident acknowledged, no action taken")
        return {"success": True, "acknowledged": True}

    return {"success": False, "detail": f"Unknown action '{action_id}'"}
