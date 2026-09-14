"""
scheduling_experiment.py — CPU scheduling algorithm simulator.

Pure simulation. No OS calls, no processes, no risk.
Implements FCFS, SJF (non-preemptive), Round Robin, Priority.
"""
import trace as tracelog


# ---------- Core simulation loop (shared by all algorithms) ----------

def _finalize(processes, timeline, algorithm_name):
    """Compute per-process metrics from the Gantt timeline.

    timeline = list of (pid, start, end) blocks
    """
    # last finish time per pid
    completion = {}
    for pid, _, end in timeline:
        completion[pid] = max(completion.get(pid, 0), end)

    results = []
    for p in processes:
        pid = p["pid"]
        ct = completion.get(pid, 0)
        tat = ct - p["arrival"]        # turnaround = completion - arrival
        wt = tat - p["burst"]          # waiting = turnaround - burst
        results.append({
            "pid": pid,
            "arrival": p["arrival"],
            "burst": p["burst"],
            "priority": p.get("priority", 0),
            "completion": ct,
            "turnaround": tat,
            "waiting": wt,
        })

    avg_wait = round(sum(r["waiting"] for r in results) / len(results), 2)
    avg_tat = round(sum(r["turnaround"] for r in results) / len(results), 2)

    # context switches = transitions between different PIDs in timeline
    switches = 0
    for i in range(1, len(timeline)):
        if timeline[i][0] != timeline[i - 1][0]:
            switches += 1

    tracelog.log("INTERNAL_ANALYSIS", "Scheduling Lab",
                 f"{algorithm_name}: avg_wait={avg_wait}s switches={switches}")

    return {
        "algorithm": algorithm_name,
        "processes": results,
        "timeline": [{"pid": pid, "start": s, "end": e}
                     for pid, s, e in timeline],
        "avg_waiting_sec": avg_wait,
        "avg_turnaround_sec": avg_tat,
        "context_switches": switches,
        "total_time": max(e for _, _, e in timeline) if timeline else 0,
    }


# ---------- Algorithm 1: FCFS ----------

def fcfs(processes):
    """First-Come First-Served: run in arrival order, no preemption."""
    procs = sorted(processes, key=lambda p: p["arrival"])
    timeline = []
    t = 0
    for p in procs:
        if t < p["arrival"]:
            t = p["arrival"]         # CPU idles until next arrival
        start = t
        t += p["burst"]
        timeline.append((p["pid"], start, t))
    return _finalize(processes, timeline, "FCFS")


# ---------- Algorithm 2: SJF (non-preemptive) ----------

def sjf(processes):
    """Shortest Job First: among arrived processes, pick the shortest burst."""
    remaining = list(processes)
    timeline = []
    t = 0
    while remaining:
        # processes that have arrived by time t
        arrived = [p for p in remaining if p["arrival"] <= t]
        if not arrived:
            t = min(p["arrival"] for p in remaining)
            continue
        # pick shortest burst
        chosen = min(arrived, key=lambda p: p["burst"])
        remaining.remove(chosen)
        start = t
        t += chosen["burst"]
        timeline.append((chosen["pid"], start, t))
    return _finalize(processes, timeline, "SJF")


# ---------- Algorithm 3: Round Robin ----------

def round_robin(processes, quantum=2):
    """Round Robin: each process gets `quantum` time, then goes to back."""
    procs = [dict(p, remaining=p["burst"]) for p in processes]
    procs.sort(key=lambda p: p["arrival"])
    timeline = []
    t = 0
    queue = []
    idx = 0  # index into procs for arrival tracking

    # seed queue with processes arriving at t=0
    while idx < len(procs) and procs[idx]["arrival"] <= t:
        queue.append(procs[idx])
        idx += 1

    while queue or idx < len(procs):
        if not queue:
            t = procs[idx]["arrival"]
            while idx < len(procs) and procs[idx]["arrival"] <= t:
                queue.append(procs[idx])
                idx += 1

        p = queue.pop(0)
        run_time = min(quantum, p["remaining"])
        start = t
        t += run_time
        p["remaining"] -= run_time
        timeline.append((p["pid"], start, t))

        # processes that arrived during this slice
        while idx < len(procs) and procs[idx]["arrival"] <= t:
            queue.append(procs[idx])
            idx += 1

        if p["remaining"] > 0:
            queue.append(p)          # send back to queue

    return _finalize(processes, timeline, f"Round Robin (q={quantum})")


# ---------- Algorithm 4: Priority (non-preemptive) ----------

def priority_scheduling(processes):
    """Priority: among arrived, pick lowest priority number (Linux style)."""
    remaining = list(processes)
    timeline = []
    t = 0
    while remaining:
        arrived = [p for p in remaining if p["arrival"] <= t]
        if not arrived:
            t = min(p["arrival"] for p in remaining)
            continue
        chosen = min(arrived, key=lambda p: p.get("priority", 0))
        remaining.remove(chosen)
        start = t
        t += chosen["burst"]
        timeline.append((chosen["pid"], start, t))
    return _finalize(processes, timeline, "Priority")


# ---------- Compare all ----------

def compare_all(processes, quantum=2):
    """Run every algorithm on the same input, return side-by-side results."""
    results = {}
    for name, fn in [
        ("FCFS", fcfs),
        ("SJF", sjf),
        ("Round Robin", lambda p: round_robin(p, quantum)),
        ("Priority", priority_scheduling),
    ]:
        results[name] = fn(processes)

    # highlight the best (lowest avg waiting)
    best = min(results.items(), key=lambda kv: kv[1]["avg_waiting_sec"])[0]
    results["_best"] = best

    tracelog.log("RESULT", "Scheduling Lab",
                 f"Compared 4 algorithms on {len(processes)} processes")
    return results


# ---------- Demo input ----------

def example_processes():
    """A classic 4-process example from any OS textbook."""
    return [
        {"pid": "P1", "arrival": 0, "burst": 5, "priority": 2},
        {"pid": "P2", "arrival": 1, "burst": 3, "priority": 1},
        {"pid": "P3", "arrival": 2, "burst": 8, "priority": 3},
        {"pid": "P4", "arrival": 3, "burst": 2, "priority": 4},
    ]
