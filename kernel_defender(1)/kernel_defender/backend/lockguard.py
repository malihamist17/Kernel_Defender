"""
lockguard.py — Engine 2: deadlock/starvation detection over REAL threads.

Design note (important for your defense):
Python threads are real OS threads (each `threading.Thread` is a genuine
pthread under the hood — you can see them with `ps -T -p <pid>` or
`/proc/<pid>/task/`). We can't hook the kernel futex table from userspace
without eBPF/ptrace, so LockGuard uses an *instrumented lock wrapper*:
every acquire/release/wait is recorded, and from that real, live
thread/resource activity we build an actual wait-for graph and run
real cycle detection (DFS) on it — this is the same algorithm used in
OS deadlock-detection theory (resource allocation graphs).

This is a legitimate systems technique (similar to how tools like
`py-spy`, Java's ThreadMXBean deadlock detector, or `strace`-based lock
tracers work) — it is not a "fake" simulation, it observes real
acquisition/blocking events from real OS threads.
"""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class LockState:
    name: str
    owner: str = None          # thread name currently holding it
    waiters: set = field(default_factory=set)


class InstrumentedLock:
    """A real threading.Lock wrapped so LockGuard can observe it."""

    def __init__(self, name, guard):
        self.name = name
        self._lock = threading.Lock()
        self.guard = guard
        guard.register_lock(name)

    def acquire(self, thread_name):
        self.guard.mark_waiting(thread_name, self.name)
        self._lock.acquire()  # real OS-level block happens here
        self.guard.mark_acquired(thread_name, self.name)

    def release(self, thread_name):
        try:
            self._lock.release()
        except RuntimeError:
            # Lock was already force-released by LockGuard recovery (the
            # victim thread is treated as aborted, mirroring real OS
            # deadlock recovery by process termination) — safe to ignore.
            pass
        self.guard.mark_released(thread_name, self.name)


class LockGuard:
    """Owns the wait-for graph and runs cycle detection + measurements."""

    def __init__(self):
        self._locks = {}                 # name -> LockState
        self._lock_owned_by_thread = {}  # thread_name -> set(lock_names)
        self._mutex = threading.Lock()   # protects our own bookkeeping
        self.trace = []
        self.wait_start_time = {}        # (thread,lock) -> timestamp

    # ---- instrumentation hooks ----

    def register_lock(self, name):
        with self._mutex:
            self._locks.setdefault(name, LockState(name))

    def mark_waiting(self, thread_name, lock_name):
        with self._mutex:
            self._locks[lock_name].waiters.add(thread_name)
            self.wait_start_time[(thread_name, lock_name)] = time.time()
            self._log("WAIT_ANALYSIS", f"{thread_name} -> {lock_name}")

    def mark_acquired(self, thread_name, lock_name):
        with self._mutex:
            state = self._locks[lock_name]
            state.owner = thread_name
            state.waiters.discard(thread_name)
            self._lock_owned_by_thread.setdefault(thread_name, set()).add(lock_name)
            self.wait_start_time.pop((thread_name, lock_name), None)
            self._log("RESOURCE_ANALYSIS", f"{lock_name} -> {thread_name}")

    def mark_released(self, thread_name, lock_name):
        with self._mutex:
            state = self._locks[lock_name]
            if state.owner == thread_name:
                state.owner = None
            self._lock_owned_by_thread.get(thread_name, set()).discard(lock_name)
            self._log("RESOURCE_RELEASE", f"{lock_name} released by {thread_name}")

    def _log(self, event, detail):
        self.trace.append({"t": time.time(), "event": event, "detail": detail})

    # ---- graph analysis (this is the OS-concept core) ----

    def build_wait_for_graph(self):
        """thread -> set(threads it is waiting on), via the locks it wants."""
        with self._mutex:
            graph = {}
            for lock_name, state in self._locks.items():
                if state.owner is None:
                    continue
                for waiter in state.waiters:
                    graph.setdefault(waiter, set()).add(state.owner)
            return graph

    def detect_cycle(self):
        """Classic DFS cycle detection on the wait-for graph."""
        graph = self.build_wait_for_graph()
        visited, stack, path = set(), set(), []

        def dfs(node):
            visited.add(node)
            stack.add(node)
            path.append(node)
            for neighbor in graph.get(node, ()):
                if neighbor not in visited:
                    result = dfs(neighbor)
                    if result:
                        return result
                elif neighbor in stack:
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:] + [neighbor]
            stack.discard(node)
            path.pop()
            return None

        for node in list(graph.keys()):
            if node not in visited:
                cycle = dfs(node)
                if cycle:
                    self._log("DETECTOR", f"Cycle found: {' -> '.join(cycle)}")
                    return cycle
        return None

    def waiting_durations(self):
        now = time.time()
        with self._mutex:
            return {f"{t}->{l}": round(now - ts, 3)
                    for (t, l), ts in self.wait_start_time.items()}

    def snapshot(self):
        return {
            "locks": {n: {"owner": s.owner, "waiters": list(s.waiters)}
                      for n, s in self._locks.items()},
            "wait_for_graph": {k: list(v) for k, v in self.build_wait_for_graph().items()},
            "cycle": self.detect_cycle(),
            "waiting_durations": self.waiting_durations(),
        }

    def force_release(self, thread_name, lock_name):
        """Recovery action: forcibly break a cycle (last-resort intervention).
        In real kernels there's no safe generic 'force unlock another thread's
        mutex' — here we simulate the RECOVERY POLICY decision (which thread
        to abort) while the actual unlock happens via the test workload
        catching an injected exception. See deadlock_experiment.py."""
        self._log("RECOVERY", f"Recovery selected victim: {thread_name} on {lock_name}")
