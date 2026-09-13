"""
kernel_interface.py — the user-space side of Kernel Space <-> User Space
communication. Talks to the real kernel module ONLY through
/proc/kernel_defender — never assumes the module is loaded, and always
reports honestly when it isn't (this file works correctly whether or not
you've built/loaded defender_module.ko, which matters since this sandbox
can't compile or load real kernel modules to test against).
"""
import time

PROC_PATH = "/proc/kernel_defender"


def is_module_loaded():
    import os
    return os.path.exists(PROC_PATH)


def read_status():
    """Reads the real kernel module's /proc file. Returns an honest
    'not loaded' status instead of fabricating data if the module isn't
    built/loaded — this dashboard never pretends kernel state that isn't
    really there."""
    if not is_module_loaded():
        return {
            "loaded": False,
            "detail": f"{PROC_PATH} does not exist — the kernel module isn't "
                      f"built/loaded. See STEPS.md for build/insmod instructions.",
        }
    try:
        with open(PROC_PATH) as f:
            raw = f.read()
    except PermissionError:
        return {"loaded": True, "detail": f"{PROC_PATH} exists but isn't readable by this user."}
    except OSError as e:
        return {"loaded": True, "detail": f"Error reading {PROC_PATH}: {e}"}

    status = {"loaded": True, "raw": raw}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        status[key.strip().lower().replace(" ", "_")] = value.strip()
    return status


def get_mode():
    status = read_status()
    return status.get("mode") if status.get("loaded") else None


def set_mode(mode):
    """Writes 'MODE:<mode>' to the real /proc file — an actual real
    kernel-space write, not a simulated one. Reports honestly if the
    module isn't loaded or the write fails."""
    if mode not in ("NORMAL", "MONITOR", "PROTECT"):
        return {"success": False, "detail": f"Unknown mode '{mode}' — must be NORMAL, MONITOR, or PROTECT."}
    if not is_module_loaded():
        return {"success": False, "detail": f"{PROC_PATH} does not exist — the kernel module isn't built/loaded."}
    try:
        with open(PROC_PATH, "w") as f:
            f.write(f"MODE:{mode}")
    except PermissionError:
        return {"success": False, "detail": f"No write permission on {PROC_PATH}."}
    except OSError as e:
        return {"success": False, "detail": f"Write failed: {e}"}
    time.sleep(0.05)
    new_status = read_status()
    success = new_status.get("mode") == mode
    return {"success": success, "status_after": new_status}


def record_event(text):
    """Writes 'EVENT:<text>' to the real /proc file — used by
    kernel_experiment.py to let the kernel module's own incident counter
    reflect real events from CPU/Memory integration tests."""
    if not is_module_loaded():
        return {"success": False, "detail": "Module not loaded."}
    try:
        with open(PROC_PATH, "w") as f:
            f.write(f"EVENT:{text}")
        return {"success": True}
    except OSError as e:
        return {"success": False, "detail": str(e)}


if __name__ == "__main__":
    import json
    print(json.dumps(read_status(), indent=2))
