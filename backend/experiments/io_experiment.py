"""
io_experiment.py — Engine: real controlled disk I/O workload.

Writes and reads an actual file on disk (fsync'd, so it really hits the
block layer, not just the page cache) and measures the real effect via
/proc/diskstats before/after — same technique `iostat` uses.
"""
import os
import tempfile
import time

import monitor
import trace as tracelog


def run_experiment(size_mb=200):
    before_disk = monitor.disk_io_stats()
    tracelog.log("MEASUREMENT", "I/O Lab", "Before diskstats snapshot")

    path = os.path.join(tempfile.gettempdir(), "kernel_defender_io_test.bin")
    chunk = os.urandom(1024 * 1024)  # 1 MB of random data, avoids compression tricks

    tracelog.log("COMMAND", "I/O Lab", f"Writing {size_mb}MB to {path}")
    t0 = time.time()
    with open(path, "wb") as f:
        for _ in range(size_mb):
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())  # force real disk write, not just page cache
    write_time = round(time.time() - t0, 3)

    tracelog.log("COMMAND", "I/O Lab", "Reading file back")
    t1 = time.time()
    with open(path, "rb") as f:
        while f.read(1024 * 1024):
            pass
    read_time = round(time.time() - t1, 3)

    after_disk = monitor.disk_io_stats()
    delta = monitor.disk_io_delta(before_disk, after_disk)
    tracelog.log("RESULT", "I/O Lab", "I/O workload complete", {
        "write_time_sec": write_time, "read_time_sec": read_time, "disk_delta": delta,
    })

    os.remove(path)

    incident = None
    if write_time > 3:
        incident = {"type": "io_bottleneck", "severity": "MEDIUM",
                    "detail": f"{size_mb}MB write took {write_time}s"}

    return {
        "size_mb": size_mb,
        "write_time_sec": write_time,
        "read_time_sec": read_time,
        "write_throughput_mbps": round(size_mb / write_time, 1) if write_time else None,
        "disk_delta": delta,
        "incident": incident,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_experiment(size_mb=50), indent=2))
