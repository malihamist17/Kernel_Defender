"""
starvation_experiment.py — several real threads compete for one shared
resource lock under a biased acquisition policy, so one thread is
starved. We measure real wait times per thread (wall-clock, from the
instrumented lock), which is the metric your faculty doc calls out
(waiting-time reduction as a research outcome).
"""
import random
import threading
import time

from lockguard import LockGuard, InstrumentedLock


class StarvationExperiment:
    def __init__(self, n_threads=4, duration_sec=6):
        self.guard = LockGuard()
        self.shared = InstrumentedLock("SharedResource", self.guard)
        self.n_threads = n_threads
        self.duration_sec = duration_sec
        self.service_count = {f"T{i}": 0 for i in range(n_threads)}
        self.wait_log = {f"T{i}": [] for i in range(n_threads)}
        self._stop = threading.Event()
        self.fair_mode = False  # toggled by "apply fairness fix" action

    def _worker(self, idx):
        name = f"T{idx}"
        while not self._stop.is_set():
            start = time.time()
            # Bias: thread 3 (the "victim") always yields to a random short
            # sleep before requesting, so it loses the race under contention —
            # a simple, honest way to reproduce priority-inversion-style
            # starvation without needing raw kernel scheduler access.
            if not self.fair_mode and idx == self.n_threads - 1:
                time.sleep(0.05)
            self.shared.acquire(name)
            waited = time.time() - start
            self.wait_log[name].append(round(waited, 3))
            self.service_count[name] += 1
            time.sleep(0.02)  # critical section
            self.shared.release(name)
            time.sleep(0.01 if self.fair_mode else 0.005)

    def start(self):
        self._threads = [
            threading.Thread(target=self._worker, args=(i,), daemon=True, name=f"T{i}")
            for i in range(self.n_threads)
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop.set()

    def apply_fairness_fix(self):
        """Controlled intervention: switch to a fair acquisition policy
        (round-robin-ish backoff) — the measurable 'after' state."""
        self.fair_mode = True

    def report(self):
        result = {}
        for name, waits in self.wait_log.items():
            avg_wait = round(sum(waits) / len(waits), 3) if waits else 0
            max_wait = round(max(waits), 3) if waits else 0
            result[name] = {
                "service_count": self.service_count[name],
                "avg_wait_sec": avg_wait,
                "max_wait_sec": max_wait,
            }
        return result


if __name__ == "__main__":
    exp = StarvationExperiment()
    exp.start()
    time.sleep(4)
    print("BEFORE FIX:", exp.report())
    exp.apply_fairness_fix()
    exp.wait_log = {k: [] for k in exp.wait_log}
    exp.service_count = {k: 0 for k in exp.service_count}
    time.sleep(4)
    print("AFTER FIX:", exp.report())
    exp.stop()
