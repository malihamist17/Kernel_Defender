#!/usr/bin/env python3
import json, os, subprocess, threading, time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

PY_PID = None
events = deque(maxlen=500)
waiting = {}
completed = deque(maxlen=200)
lock = threading.Lock()

def detect_python_pid():
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "python.*app.py"], text=True).strip().split()
        return int(out[0]) if out else None
    except Exception:
        return None

def python_thread_count(pid):
    if not pid:
        return 0
    try:
        return len(os.listdir(f"/proc/{pid}/task"))
    except FileNotFoundError:
        return 0

def reader():
    while True:
        try:
            proc = subprocess.Popen(
                ["sudo", "bpftrace", "-q", "/home/mahi-rat/futex_watch.bt"],
                stdout=subprocess.PIPE, text=True, bufsize=1)
            for line in proc.stdout:
                parts = line.strip().split(",")
                if len(parts) != 7:
                    continue
                try:
                    ts, pid, tid, comm, uaddr, op, wait_us = parts
                    ev = {"ts_ns": int(ts), "pid": int(pid), "tid": int(tid),
                          "comm": comm, "uaddr": uaddr, "op": int(op),
                          "wait_us": int(wait_us)}
                except ValueError:
                    continue
                with lock:
                    if ev["op"] in (0, 9):
                        events.append(ev)
                        waiting[ev["tid"]] = {"uaddr": ev["uaddr"], "since_ns": ev["ts_ns"]}
                    elif ev["op"] == 255:
                        waiting.pop(ev["tid"], None)
                        completed.append({"ts_ns": ev["ts_ns"], "tid": ev["tid"],
                                          "wait_us": ev["wait_us"]})
        except Exception:
            pass
        time.sleep(1)

def find_cycles():
    with lock:
        snap = dict(waiting)
    by_uaddr = {}
    for tid, info in snap.items():
        by_uaddr[info["uaddr"]] = tid
    cycles = []
    for tid, info in snap.items():
        partner = by_uaddr.get(info["uaddr"])
        if partner and partner != tid and partner in snap:
            pair = tuple(sorted([tid, partner]))
            if pair not in [tuple(sorted(c["tids"])) for c in cycles]:
                cycles.append({"tids": list(pair), "uaddr": info["uaddr"]})
    return cycles

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/events"):
            with lock:
                now = time.time_ns()
                waiters = [{"tid": t, "uaddr": i["uaddr"],
                            "waiting_ms": (now - i["since_ns"]) // 1_000_000}
                           for t, i in waiting.items()]
                recent_waits = list(completed)[-20:]
                max_wait = max((c["wait_us"] for c in completed), default=0)
            body = json.dumps({
                "recent": list(events)[-40:],
                "current_waiters": waiters,
                "cycles": find_cycles(),
                "recent_completions": recent_waits,
                "max_wait_us": max_wait,
                "total_events": len(events),
                "python_pid": PY_PID,
                "python_thread_count": python_thread_count(PY_PID),
                "kernel_blocked_now": len(waiters),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

if __name__ == "__main__":
    PY_PID = int(os.environ.get("LG_PID", "0")) or detect_python_pid()
    print(f"[bridge] watching python pid = {PY_PID}")
    threading.Thread(target=reader, daemon=True).start()
    HTTPServer(("0.0.0.0", 8001), H).serve_forever()
