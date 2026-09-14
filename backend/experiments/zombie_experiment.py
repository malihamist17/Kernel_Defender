"""
zombie_experiment.py — Engine: real zombie process creation via os.fork().

A zombie is a process that has exited but whose parent hasn't called
wait()/waitpid() to collect its exit status yet — the kernel keeps a
minimal entry (PID, exit code) in the process table until reaped.
This creates a REAL zombie you can verify independently with:
    ps -o pid,ppid,stat,cmd -p <pid>
(STAT column will show 'Z').

Only works on POSIX (Linux) — os.fork() doesn't exist on Windows.
"""
import os
import time

import trace as tracelog

_pending_zombie_pid = None


def create_zombie():
    global _pending_zombie_pid
    tracelog.log("USER_ACTION", "Process Lab", "Create controlled zombie")

    pid = os.fork()
    if pid == 0:
        # child: exit immediately, parent won't reap it yet -> zombie
        os._exit(0)
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


def reap():
    """Controlled intervention: parent calls waitpid() -> zombie disappears."""
    global _pending_zombie_pid
    if _pending_zombie_pid is None:
        return {"error": "no zombie to reap"}
    tracelog.log("USER_ACTION", "Process Lab", f"Reap PID {_pending_zombie_pid}")
    pid, exit_status = os.waitpid(_pending_zombie_pid, 0)
    tracelog.log("RESULT", "Process Lab", f"PID {pid} reaped, exit_status={exit_status}")
    _pending_zombie_pid = None
    return {"reaped_pid": pid, "exit_status": exit_status}


if __name__ == "__main__":
    info = create_zombie()
    print("created:", info)
    print("status before reap:", status())
    print("reap result:", reap())
    print("status after reap:", status())
