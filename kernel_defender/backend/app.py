"""
app.py — Kernel Defender REST API (full system).

Run:
    pip install -r requirements.txt
    python app.py
Then open frontend/dashboard.html.
"""
import sys
import threading
import time
import uuid

sys.path.append("experiments")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import monitor
import trace as tracelog
import db
import incident_actions
from experiments.deadlock_experiment import DeadlockExperiment, guard_on_off_comparison
from experiments.starvation_experiment import StarvationExperiment
from experiments import cpu_experiment, memory_experiment, io_experiment, zombie_experiment, ipc_experiment, scheduling_experiment

app = FastAPI(title="Kernel Defender API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

INCIDENTS = []
RUNNING = {"deadlock": None, "starvation": None}


def get_active_incident(kind):
    for i in reversed(INCIDENTS):
        if i["type"] == kind and i["status"] == "active":
            return i
    return None


def new_incident(kind, severity, detail):
    """Dedupe: while a condition is still active, don't spam duplicate
    incident cards — only one live incident per type at a time."""
    existing = get_active_incident(kind)
    if existing:
        return existing
    incident = {
        "id": str(uuid.uuid4())[:8],
        "type": kind,
        "severity": severity,
        "detail": detail,
        "created": time.time(),
        "status": "active",
    }
    INCIDENTS.append(incident)
    db.save_incident(incident)
    tracelog.log("RESULT", "Incident Engine", detail, incident)
    return incident


def resolve_incident(incident_id):
    for i in INCIDENTS:
        if i["id"] == incident_id:
            i["status"] = "resolved"
            return i
    return None


# ---------------- Dashboard ----------------

@app.get("/api/snapshot")
def get_snapshot():
    snap = monitor.snapshot()
    if snap["cpu_percent"] > 90:
        new_incident("cpu_performance", "MEDIUM", f"CPU utilization {snap['cpu_percent']}%")
    if snap["memory"]["used_percent"] > 90:
        new_incident("memory_pressure", "HIGH", f"Memory usage {snap['memory']['used_percent']}%")
    if snap["zombies"]:
        new_incident("zombie_process", "LOW", f"{len(snap['zombies'])} zombie process(es)")
    return snap


@app.get("/api/incidents")
def get_incidents(include_resolved: bool = False):
    if include_resolved:
        return list(reversed(INCIDENTS[-50:]))
    return [i for i in reversed(INCIDENTS[-50:]) if i["status"] == "active"]


@app.get("/api/incidents/{incident_id}/inspections")
def get_incident_inspections(incident_id: str):
    incident = next((i for i in INCIDENTS if i["id"] == incident_id), None)
    if not incident:
        return {"error": "incident not found"}
    return {"incident": incident, "inspections": incident_actions.available_inspections(incident["type"])}


@app.post("/api/incidents/{incident_id}/inspect/{inspection_id}")
def run_incident_inspection(incident_id: str, inspection_id: str):
    incident = next((i for i in INCIDENTS if i["id"] == incident_id), None)
    if not incident:
        return {"error": "incident not found"}
    return incident_actions.run_inspection(incident["type"], inspection_id)


@app.get("/api/incidents/{incident_id}/actions")
def get_incident_actions(incident_id: str):
    incident = next((i for i in INCIDENTS if i["id"] == incident_id), None)
    if not incident:
        return {"error": "incident not found"}
    return {"incident": incident, "actions": incident_actions.available_actions(incident["type"])}


@app.post("/api/incidents/{incident_id}/actions/{action_id}")
def run_incident_action(incident_id: str, action_id: str):
    incident = next((i for i in INCIDENTS if i["id"] == incident_id), None)
    if not incident:
        return {"error": "incident not found"}
    result = incident_actions.execute(incident["type"], action_id, RUNNING)
    # Only mark resolved if the action genuinely succeeded — this is the
    # fix for incidents (like zombies not owned by this process) that were
    # previously marked "resolved" even when nothing actually happened.
    resolved = bool(result.get("success", False))
    if resolved:
        resolve_incident(incident_id)
    db.save_experiment("incident_action", {"incident": incident, "action_id": action_id, "result": result})
    return {"incident_id": incident_id, "action_id": action_id, "result": result, "resolved": resolved}


# ---------------- Processes ----------------

@app.get("/api/processes")
def get_processes(limit: int = 25):
    return monitor.list_processes(limit=limit)


@app.get("/api/processes/tree")
def get_process_tree():
    return monitor.process_tree()


@app.post("/api/experiments/zombie/create")
def create_zombie():
    result = zombie_experiment.create_zombie()
    new_incident("zombie_process", "LOW", f"Controlled zombie created: PID {result['child_pid']}")
    db.save_experiment("zombie", result)
    return result


@app.get("/api/experiments/zombie/status")
def zombie_status():
    return zombie_experiment.status()


@app.post("/api/experiments/zombie/reap")
def reap_zombie():
    result = zombie_experiment.reap()
    db.save_experiment("zombie_reap", result)
    return result


# ---------------- Memory ----------------

@app.get("/api/memory/state")
def memory_state():
    return monitor.memory_stats()


@app.get("/api/memory/top-consumers")
def memory_top_consumers(limit: int = 10):
    return monitor.top_memory_consumers(limit=limit)


@app.get("/api/experiments/memory/status")
def memory_experiment_status():
    return memory_experiment.status()


@app.post("/api/experiments/memory/run")
def run_memory_experiment(target_mb: int = 256, hold_seconds: int = 3):
    result = memory_experiment.run_experiment(target_mb=target_mb, hold_seconds=hold_seconds)
    if result.get("error"):
        return result
    if not result["safety_check"]["allowed"]:
        new_incident("memory_action_blocked", "LOW",
                      f"Requested {result['safety_check']['requested_mb']}MB clamped to "
                      f"{result['target_mb']}MB for safety")
    if result.get("incident"):
        inc = result["incident"]
        new_incident(inc["type"], inc["severity"], inc["detail"])
    db.save_experiment("memory", result)
    return result


@app.post("/api/experiments/memory/release")
def release_memory_experiment():
    result = memory_experiment.release()
    db.save_experiment("memory_release", result)
    return result


# ---------------- I/O ----------------

@app.get("/api/io/state")
def io_state():
    return monitor.disk_io_stats()


@app.post("/api/experiments/io/run")
def run_io_experiment(size_mb: int = 200):
    result = io_experiment.run_experiment(size_mb=size_mb)
    if result.get("incident"):
        inc = result["incident"]
        new_incident(inc["type"], inc["severity"], inc["detail"])
    db.save_experiment("io", result)
    return result
# ---------------- IPC Lab ----------------

@app.post("/api/experiments/ipc/run")
def run_ipc(method: str = "pipe", n_messages: int = 1000):
    if method == "pipe":
        result = ipc_experiment.pipe_experiment(n_messages)
    elif method == "queue":
        result = ipc_experiment.queue_experiment(n_messages)
    elif method == "shared_memory":
        result = ipc_experiment.shared_memory_experiment(n_messages)
    elif method == "socket":
        result = ipc_experiment.socket_experiment(n_messages)
    else:
        return {"error": f"unknown method '{method}'"}
    db.save_experiment(f"ipc_{method}", result)
    return result


@app.post("/api/experiments/ipc/compare")
def compare_ipc(n_messages: int = 500):
    result = ipc_experiment.compare_all(n_messages)
    db.save_experiment("ipc_comparison", result)
    return result



# ---------------- Scheduling Lab ----------------

@app.post("/api/experiments/scheduling/run")
def run_scheduling(algorithm: str = "FCFS", quantum: int = 2):
    procs = scheduling_experiment.example_processes()
    if algorithm == "FCFS":
        result = scheduling_experiment.fcfs(procs)
    elif algorithm == "SJF":
        result = scheduling_experiment.sjf(procs)
    elif algorithm == "Round Robin":
        result = scheduling_experiment.round_robin(procs, quantum)
    elif algorithm == "Priority":
        result = scheduling_experiment.priority_scheduling(procs)
    else:
        return {"error": f"unknown algorithm '{algorithm}'"}
    db.save_experiment(f"scheduling_{algorithm}", result)
    return result


@app.post("/api/experiments/scheduling/compare")
def compare_scheduling(quantum: int = 2):
    procs = scheduling_experiment.example_processes()
    result = scheduling_experiment.compare_all(procs, quantum)
    db.save_experiment("scheduling_comparison", result)
    return result


# ---------------- CPU Lab ----------------

@app.get("/api/cpu/state")
def cpu_state():
    return {
        "utilization": monitor.cpu_utilization_percent(0.1),
        "per_core_percent": monitor.per_core_utilization_percent(0.05),
        "freq_mhz": monitor.cpu_frequencies_mhz(),
        "governor": monitor.cpu_governor(),
        "available_governors": monitor.cpu_available_governors(),
        "temp_c": monitor.cpu_temperature_c(),
    }


@app.post("/api/experiments/cpu/run")
def run_cpu_experiment(seconds: int = 4, target_governor: str = None):
    result = cpu_experiment.run_experiment(seconds=seconds, target_governor=target_governor)
    db.save_experiment("cpu", result)
    return result


# Non-blocking version: lets the frontend poll /api/cpu/state for a live
# chart WHILE the workload runs, matching the doc's "live visualization"
# mockup (section 14) instead of freezing the UI until it's done.
CPU_RUN = {"running": False, "result": None}


@app.post("/api/cpu/governor")
def apply_governor(governor: str):
    """Apply a policy immediately (the mockup's standalone [APPLY] button),
    independent of running a full timed experiment."""
    tracelog.log("USER_ACTION", "CPU Lab", f"Selected policy: {governor}")
    result = cpu_experiment.set_governor(governor)
    tracelog.log("SAFETY", "CPU Lab", "Safety check", result)
    if result.get("applied"):
        tracelog.log("RESULT", "CPU Lab", f"Policy changed to {governor}")
    else:
        new_incident("cpu_action_blocked", "LOW", f"Governor change to '{governor}' failed: {result}")
    return result


@app.post("/api/experiments/cpu/start")
def start_cpu_experiment(seconds: int = 6, target_governor: str = None):
    if CPU_RUN["running"]:
        return {"error": "an experiment is already running"}
    CPU_RUN["running"] = True
    CPU_RUN["result"] = None
    tracelog.log("USER_ACTION", "CPU Lab", f"Run CPU experiment ({seconds}s, governor={target_governor or 'unchanged'})")

    def worker():
        result = cpu_experiment.run_experiment(seconds=seconds, target_governor=target_governor)
        db.save_experiment("cpu", result)
        CPU_RUN["result"] = result
        CPU_RUN["running"] = False
        tracelog.log("RESULT", "CPU Lab", "Experiment complete", result)

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True}


@app.get("/api/experiments/cpu/result")
def cpu_experiment_result():
    return {"running": CPU_RUN["running"], "result": CPU_RUN["result"]}


# ------------- Continuous stress test with Start/Stop + safety monitor -------------

@app.post("/api/cpu/stress/start")
def start_cpu_stress(temp_limit_c: int = 80):
    result = cpu_experiment.start_stress_test(temp_limit_c=temp_limit_c)
    return result


@app.get("/api/cpu/stress/live")
def cpu_stress_live():
    return cpu_experiment.stress_live_state()


@app.post("/api/cpu/stress/stop")
def stop_cpu_stress():
    result = cpu_experiment.stop_stress_test()
    if result.get("stopped"):
        db.save_experiment("cpu_stress", result)
    return result


# ---------------- LockGuard: Deadlock ----------------

@app.post("/api/experiments/deadlock/start")
def start_deadlock():
    exp = DeadlockExperiment()
    ids = exp.start()
    RUNNING["deadlock"] = exp
    tracelog.log("USER_ACTION", "LockGuard", "Started deadlock experiment", ids)
    time.sleep(0.8)
    cycle = exp.guard.detect_cycle()
    if cycle:
        new_incident("deadlock", "HIGH", f"Circular wait: {' -> '.join(cycle)}")
    result = {"thread_ids": ids, "cycle_detected": cycle, "snapshot": exp.guard.snapshot()}
    db.save_experiment("deadlock", result)
    return result


@app.get("/api/experiments/deadlock/status")
def deadlock_status():
    exp = RUNNING["deadlock"]
    if not exp:
        return {"error": "no experiment running"}
    return exp.guard.snapshot()


@app.post("/api/experiments/deadlock/recover")
def recover_deadlock(victim: str = "ThreadB"):
    exp = RUNNING["deadlock"]
    if not exp:
        return {"error": "no experiment running"}
    snap = exp.recover(victim)
    return {"recovered": True, "victim": victim, "snapshot_after": snap}


@app.post("/api/experiments/deadlock/guard-comparison")
def deadlock_guard_comparison(timeout_sec: int = 3):
    result = guard_on_off_comparison(guard_timeout_sec=timeout_sec)
    db.save_experiment("guard_comparison", result)
    return result


# ---------------- LockGuard: Starvation ----------------

@app.post("/api/experiments/starvation/start")
def start_starvation():
    exp = StarvationExperiment()
    exp.start()
    RUNNING["starvation"] = exp
    return {"started": True, "threads": exp.n_threads}


@app.get("/api/experiments/starvation/report")
def starvation_report():
    exp = RUNNING["starvation"]
    if not exp:
        return {"error": "no experiment running"}
    report = exp.report()
    starved = [name for name, r in report.items() if r["avg_wait_sec"] > 0.15]
    if starved:
        new_incident("starvation", "HIGH", f"Possible starvation: {starved}")
    db.save_experiment("starvation", report)
    return report


@app.post("/api/experiments/starvation/fix")
def fix_starvation():
    exp = RUNNING["starvation"]
    if not exp:
        return {"error": "no experiment running"}
    exp.apply_fairness_fix()
    exp.wait_log = {k: [] for k in exp.wait_log}
    exp.service_count = {k: 0 for k in exp.service_count}
    return {"fairness_applied": True}


@app.post("/api/experiments/starvation/stop")
def stop_starvation():
    exp = RUNNING["starvation"]
    if exp:
        exp.stop()
    return {"stopped": True}


# ---------------- Execution Trace ----------------

@app.get("/api/trace")
def get_trace(limit: int = 100):
    return tracelog.recent(limit=limit)


# ---------------- Analytics ----------------

@app.get("/api/analytics/history")
def analytics_history(exp_type: str = None, limit: int = 30):
    return db.get_experiment_history(limit=limit, exp_type=exp_type)


# ---------------- Learn ----------------

LEARN_CONTENT = {
    "deadlock": {
        "title": "Deadlock (Circular Wait)",
        "body": "A deadlock happens when two or more threads each hold a "
                "resource the other needs, and neither will release it. "
                "This requires four conditions simultaneously: mutual "
                "exclusion, hold-and-wait, no preemption, and circular wait. "
                "LockGuard detects it by building a wait-for graph from real "
                "thread/lock activity and running cycle detection (DFS).",
    },
    "starvation": {
        "title": "Starvation",
        "body": "Starvation occurs when a thread is perpetually denied the "
                "resources it needs to proceed, usually because a scheduling "
                "or locking policy favors other threads. Unlike deadlock, "
                "the starved thread isn't blocked forever — it's just "
                "consistently deprioritized.",
    },
    "zombie": {
        "title": "Zombie Process",
        "body": "A zombie is a process that has terminated, but whose "
                "parent has not yet called wait()/waitpid() to read its "
                "exit status. The kernel keeps a minimal process-table "
                "entry (PID + exit code) until it's reaped.",
    },
    "cpu_governor": {
        "title": "CPU Frequency Governors",
        "body": "Linux's CPUFreq subsystem lets the kernel scale CPU "
                "frequency dynamically. 'powersave' favors low frequency, "
                "'performance' locks to the highest supported frequency, "
                "and 'schedutil' scales based on real-time scheduler load.",
    },
    "memory_pressure": {
        "title": "Memory Pressure",
        "body": "Memory pressure occurs when demand for RAM approaches "
                "total capacity, forcing the kernel to reclaim pages, evict "
                "caches, or swap. Sustained pressure degrades performance "
                "system-wide, not just for the allocating process.",
    },
    "io_bottleneck": {
        "title": "I/O Bottleneck",
        "body": "An I/O bottleneck occurs when a process's throughput is "
                "limited by disk speed rather than CPU. /proc/diskstats "
                "exposes real sector read/write counters the kernel "
                "maintains per block device.",
    },
}


@app.get("/api/learn/{topic}")
def learn(topic: str):
    return LEARN_CONTENT.get(topic, {"error": "no content for this topic"})


@app.get("/api/learn")
def learn_index():
    return {k: v["title"] for k, v in LEARN_CONTENT.items()}


# ---------------- Kernel Defender Lab (Level 3 kernel customization) ----------------

sys.path.append("kernel")
from experiments import kernel_experiment


@app.get("/api/kernel/status")
def kernel_status():
    return monitor.kernel_defender_status()


@app.post("/api/kernel/mode")
def kernel_set_mode(mode: str):
    import kernel_interface
    tracelog.log("USER_ACTION", "Kernel Defender Lab", f"Requested mode change to {mode}")
    result = kernel_interface.set_mode(mode)
    return result


@app.post("/api/kernel/test")
def kernel_run_test():
    return kernel_experiment.run_kernel_test()


@app.post("/api/kernel/test-cpu")
def kernel_test_cpu():
    return kernel_experiment.test_with_cpu()


@app.post("/api/kernel/test-memory")
def kernel_test_memory():
    return kernel_experiment.test_with_memory()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
