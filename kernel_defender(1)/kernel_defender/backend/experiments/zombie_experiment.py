"""
zombie_experiment.py — Engine: real zombie process creation via os.fork(),
plus honest, parent-aware reaping of ANY zombie found on the system (not
just ones this lab created).

IMPORTANT FIX: a zombie can only be reaped by calling wait()/waitpid()
FROM ITS ACTUAL PARENT PROCESS — this is a real Linux kernel rule, not a
limitation of this tool. The previous version always reported success
and marked the incident resolved even when the zombie wasn't a child of
this app, so nothing actually happened and the same zombie reappeared on
the next scan. reap_all_current() below checks real parentage via
/proc/<pid>/status before attempting anything, and reports the honest
outcome either way.
"""
import os
import time

import monitor
import trace as tracelog

_pending_zombie_pid = None


def create_zombie():
    global _pending_zombie_pid
    tracelog.log("USER_ACTION", "Process Lab", "Create controlled zombie")

    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child: exit immediately, parent won't reap it yet -> zombie
    else:
        _pending_zombie_pid = pid
        time.sleep(0.3)  # give the kernel a moment to transition child to Z
        tracelog.log("KERNEL_EVENT", "Process Lab",
                     f"Child PID {pid} exited without being reaped")
        return {"child_pid": pid,
                "verify_command": f"ps -o pid,ppid,stat,cmd -p {pid}"}


def status():
    if _pending_zombie_pid is None:
        return {"error": "no zombie created yet"}
    try:
        with open(f"/proc/{_pending_zombie_pid}/status") as f:
            content = f.read()
        state_line = [l for l in content.splitlines() if l.startswith("State:")]
        return {"pid": _pending_zombie_pid, "raw_state": state_line[0] if state_line else "gone"}
    except FileNotFoundError:
        return {"pid": _pending_zombie_pid, "raw_state": "already reaped / gone"}


def _real_ppid(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            status_text = f.read()
    except FileNotFoundError:
        return None  # process is already gone
    for line in status_text.splitlines():
        if line.startswith("PPid:"):
            return int(line.split(":", 1)[1].strip())
    return None


def reap_by_pid(pid):
    """The honest version: only actually reaps if we are really the parent.
    Otherwise explains exactly why it can't, instead of pretending to fix it."""
    global _pending_zombie_pid
    our_pid = os.getpid()
    ppid = _real_ppid(pid)

    if ppid is None:
        if pid == _pending_zombie_pid:
            _pending_zombie_pid = None
        return {"success": True, "pid": pid,
                "detail": f"PID {pid} no longer exists — already reaped or cleaned up."}

    if ppid != our_pid:
        return {
            "success": False,
            "pid": pid,
            "not_our_child": True,
            "actual_parent_pid": ppid,
            "detail": (
                f"PID {pid}'s real parent is PID {ppid}, not Kernel Defender "
                f"(PID {our_pid}). Only a process's own parent can call "
                f"wait()/waitpid() on it — this is a real Linux kernel rule, "
                f"not a limitation of this dashboard. If PID {ppid} exits or "
                f"is killed, PID {pid} will automatically be re-parented to "
                f"init/systemd (PID 1), which reaps orphaned zombies on its own."
            ),
        }

    try:
        result_pid, exit_status = os.waitpid(pid, 0)
        if pid == _pending_zombie_pid:
            _pending_zombie_pid = None
        tracelog.log("RESULT", "Process Lab", f"PID {result_pid} reaped, exit_status={exit_status}")
        return {"success": True, "pid": pid, "reaped_pid": result_pid, "exit_status": exit_status}
    except ChildProcessError as e:
        return {"success": False, "pid": pid, "detail": str(e)}


def reap_all_current():
    """Used by the Incident Response flow: reaps every REAL zombie currently
    found on the system (via /proc), not just the lab's own tracked one —
    and only reports overall success if every one of them was genuinely
    reaped."""
    zombies = monitor.zombies()
    if not zombies:
        return {"success": True, "results": [], "detail": "No zombie processes currently found."}
    results = [reap_by_pid(int(z["pid"])) for z in zombies]
    tracelog.log("OPERATION", "Incident Response", "Attempted reap on all detected zombies", results)
    return {"success": all(r["success"] for r in results), "results": results}


def reap():
    """Lab button: reap the specific zombie this experiment created."""
    if _pending_zombie_pid is None:
        return {"success": False, "detail": "No zombie to reap."}
    return reap_by_pid(_pending_zombie_pid)


if __name__ == "__main__":
    info = create_zombie()
    print("created:", info)
    print("status before reap:", status())
    print("reap result:", reap())
    print("status after reap:", status())
