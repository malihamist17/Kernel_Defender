"""
trace.py — the single Execution Trace log referenced throughout your design
doc (section 9). Every experiment/action appends here, tagged by kind, so
the frontend's "Execution Trace" page can show one unified timeline:
command | interface | kernel_event | internal_analysis | measurement | result
"""
import time
import threading

_lock = threading.Lock()
_LOG = []
MAX_ENTRIES = 500


def log(kind, actor, detail, data=None):
    """kind: one of COMMAND, INTERFACE, KERNEL_EVENT, INTERNAL_ANALYSIS,
    MEASUREMENT, RESULT, USER_ACTION, SAFETY."""
    entry = {
        "ts": time.time(),
        "kind": kind,
        "actor": actor,     # e.g. "CPU Lab", "LockGuard", "user"
        "detail": detail,
        "data": data,
    }
    with _lock:
        _LOG.append(entry)
        if len(_LOG) > MAX_ENTRIES:
            del _LOG[: len(_LOG) - MAX_ENTRIES]
    return entry


def recent(limit=100):
    with _lock:
        return list(reversed(_LOG[-limit:]))


def clear():
    with _lock:
        _LOG.clear()
