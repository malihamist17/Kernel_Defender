"""
deadlock_experiment.py — Engine 3 controlled experiment: classic circular-wait
deadlock, built from two REAL OS threads (verifiable with `ps -T -p <pid>`).

Thread A: holds Lock A, requests Lock B
Thread B: holds Lock B, requests Lock A
-> circular wait -> LockGuard.detect_cycle() finds it live.
"""
import threading
import time

from lockguard import LockGuard, InstrumentedLock


class DeadlockExperiment:
    def __init__(self):
        self.guard = LockGuard()
        self.lock_a = InstrumentedLock("LockA", self.guard)
        self.lock_b = InstrumentedLock("LockB", self.guard)
        self.victim_signal = threading.Event()
        self._threads = []

    def _thread_a(self):
        name = threading.current_thread().name
        self.lock_a.acquire(name)
        time.sleep(0.3)  # widen the window so the deadlock is observable
        self.lock_b.acquire(name)  # blocks forever (until recovery)
        self.lock_b.release(name)
        self.lock_a.release(name)

    def _thread_b(self):
        name = threading.current_thread().name
        self.lock_b.acquire(name)
        time.sleep(0.3)
        self.lock_a.acquire(name)  # blocks forever (until recovery)
        self.lock_a.release(name)
        self.lock_b.release(name)

    def start(self):
        t_a = threading.Thread(target=self._thread_a, name="ThreadA", daemon=True)
        t_b = threading.Thread(target=self._thread_b, name="ThreadB", daemon=True)
        self._threads = [t_a, t_b]
        t_a.start()
        t_b.start()
        # native OS thread ids — verifiable via /proc/<pid>/task
        return {"thread_a_native_id": t_a.native_id, "thread_b_native_id": t_b.native_id}

    def status(self):
        return self.guard.snapshot()

    def recover(self, victim="ThreadB"):
        """Recovery policy: forcibly release the victim's held lock so the
        other thread can proceed. This models OS deadlock recovery by
        resource preemption / process termination (see OS textbook: Silberschatz
        ch. on deadlock recovery)."""
        self.guard.force_release(victim, "LockB" if victim == "ThreadB" else "LockA")
        if victim == "ThreadB":
            # release jammed lock's underlying primitive so ThreadA's acquire()
            # can complete -> breaks the cycle
            try:
                self.lock_b._lock.release()
            except RuntimeError:
                pass
        else:
            try:
                self.lock_a._lock.release()
            except RuntimeError:
                pass
        time.sleep(0.3)  # let the woken thread actually complete its acquire()
        return self.guard.snapshot()


def guard_on_off_comparison(guard_timeout_sec=3):
    """Research measurement: run the same deadlock scenario twice.

    GUARD OFF -> LockGuard's detector is never consulted; we just wait and
    report that the threads are stuck (bounded by a timeout so the demo
    doesn't hang forever - a real 'no guard' system would hang indefinitely).

    GUARD ON -> LockGuard detects the cycle and recovery is applied
    immediately; we measure real detection time and total resolution time.
    """
    import time as _time

    # --- GUARD OFF ---
    off_start = _time.time()
    exp_off = DeadlockExperiment()
    exp_off.start()
    _time.sleep(guard_timeout_sec)
    still_stuck = exp_off.guard.detect_cycle() is not None
    off_elapsed = round(_time.time() - off_start, 3)
    exp_off.recover("ThreadB")  # clean up so the daemon threads don't leak

    # --- GUARD ON ---
    exp_on = DeadlockExperiment()
    on_start = _time.time()
    exp_on.start()
    detected_at = None
    for _ in range(50):  # poll up to ~2.5s for the cycle to form and be seen
        if exp_on.guard.detect_cycle():
            detected_at = _time.time()
            break
        _time.sleep(0.05)
    detection_time_ms = round((detected_at - on_start) * 1000, 1) if detected_at else None
    exp_on.recover("ThreadB")
    _time.sleep(0.2)
    resolved = exp_on.guard.detect_cycle() is None
    on_total_elapsed = round(_time.time() - on_start, 3)

    return {
        "guard_off": {
            "still_deadlocked_after_sec": off_elapsed,
            "would_hang_indefinitely": still_stuck,
        },
        "guard_on": {
            "detection_time_ms": detection_time_ms,
            "resolved": resolved,
            "total_time_sec": on_total_elapsed,
        },
    }


if __name__ == "__main__":
    exp = DeadlockExperiment()
    ids = exp.start()
    print("started:", ids)
    time.sleep(1.0)
    print("cycle before recovery:", exp.guard.detect_cycle())
    exp.recover("ThreadB")
    time.sleep(0.5)
    print("cycle after recovery:", exp.guard.detect_cycle())
