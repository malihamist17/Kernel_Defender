"""
monitor.py — Kernel Defender monitoring layer.

Reads REAL kernel-exposed state from /proc and /sys. No simulated numbers.
This is the "Engine 1" data source: CPU, memory, processes, threads, temp.
"""
import glob
import os
import time


# ---------- CPU ----------

def read_cpu_times():
    """Parse the aggregate 'cpu' line in /proc/stat -> dict of jiffies."""
    with open("/proc/stat") as f:
        line = f.readline()
    fields = line.split()
    labels = ["user", "nice", "system", "idle", "iowait",
              "irq", "softirq", "steal", "guest", "guest_nice"]
    values = [int(x) for x in fields[1:]]
    return dict(zip(labels, values))


def cpu_utilization_percent(sample_interval=0.2):
    """Real CPU utilization via two /proc/stat samples (like `top` does)."""
    t1 = read_cpu_times()
    time.sleep(sample_interval)
    t2 = read_cpu_times()

    idle1 = t1["idle"] + t1["iowait"]
    idle2 = t2["idle"] + t2["iowait"]
    total1 = sum(t1.values())
    total2 = sum(t2.values())

    total_delta = total2 - total1
    idle_delta = idle2 - idle1
    if total_delta == 0:
        return 0.0
    return round((1 - idle_delta / total_delta) * 100, 1)


def cpu_frequencies_mhz():
    """Per-core current frequency from cpufreq (kHz -> MHz)."""
    freqs = {}
    for path in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")):
        core = path.split("/")[5]  # e.g. cpu0
        try:
            with open(path) as f:
                khz = int(f.read().strip())
            freqs[core] = round(khz / 1000, 1)
        except (IOError, PermissionError):
            freqs[core] = None
    return freqs


def cpu_governor():
    path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
    try:
        with open(path) as f:
            return f.read().strip()
    except (IOError, FileNotFoundError):
        return "unavailable"


def cpu_available_governors():
    path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors"
    try:
        with open(path) as f:
            return f.read().split()
    except (IOError, FileNotFoundError):
        return []


def cpu_temperature_c():
    """Best-effort: scan thermal zones. Not all machines expose this."""
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            with open(zone) as f:
                milli_c = int(f.read().strip())
            return round(milli_c / 1000, 1)
        except (IOError, PermissionError, ValueError):
            continue
    return None


# ---------- Memory ----------

def memory_stats():
    """Parse /proc/meminfo into a dict of MB values."""
    stats = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":")
            kb = int(val.strip().split()[0])
            stats[key] = round(kb / 1024, 1)  # MB
    total = stats.get("MemTotal", 0)
    avail = stats.get("MemAvailable", 0)
    used = round(total - avail, 1)
    used_pct = round((used / total) * 100, 1) if total else 0
    return {
        "total_mb": total,
        "available_mb": avail,
        "used_mb": used,
        "used_percent": used_pct,
        "swap_total_mb": stats.get("SwapTotal", 0),
        "swap_used_mb": round(stats.get("SwapTotal", 0) - stats.get("SwapFree", 0), 1),
    }


# ---------- Processes / Threads ----------

def list_processes(limit=25):
    """Walk /proc/[pid] directly (this is literally what `ps` does)."""
    procs = []
    for pid_dir in glob.glob("/proc/[0-9]*"):
        pid = os.path.basename(pid_dir)
        try:
            with open(f"{pid_dir}/status") as f:
                status = f.read()
            with open(f"{pid_dir}/stat") as f:
                stat_fields = f.read().split()
            name = _extract(status, "Name")
            state = _extract(status, "State").split()[0]
            ppid = _extract(status, "PPid")
            vm_rss_kb = _extract(status, "VmRSS")
            threads = _extract(status, "Threads")
            procs.append({
                "pid": pid,
                "ppid": ppid,
                "name": name,
                "state": state,          # R, S, D, Z, T ...
                "is_zombie": state == "Z",
                "mem_kb": vm_rss_kb,
                "threads": threads,
            })
        except (IOError, IndexError, FileNotFoundError):
            continue  # process exited between listdir and read — normal race
    return procs[:limit]


def process_threads(pid):
    """List thread IDs (TIDs) for a PID by reading /proc/[pid]/task/."""
    task_dir = f"/proc/{pid}/task"
    if not os.path.isdir(task_dir):
        return []
    return sorted(os.listdir(task_dir), key=int)


def _extract(status_text, field):
    for line in status_text.splitlines():
        if line.startswith(field + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def zombies():
    return [p for p in list_processes(limit=10_000) if p["is_zombie"]]


# ---------- Process tree ----------

def process_tree():
    """Build a parent->children tree from /proc, same relationships `pstree` uses."""
    procs = list_processes(limit=10_000)
    by_pid = {p["pid"]: {**p, "children": []} for p in procs}
    roots = []
    for p in by_pid.values():
        parent = by_pid.get(p["ppid"])
        if parent:
            parent["children"].append(p)
        else:
            roots.append(p)
    return roots


def top_memory_consumers(limit=10):
    """Sort real processes by resident memory (VmRSS from /proc/[pid]/status)."""
    procs = list_processes(limit=10_000)
    def mem_kb(p):
        try:
            return int(p["mem_kb"].split()[0]) if p["mem_kb"] else 0
        except (ValueError, IndexError):
            return 0
    return sorted(procs, key=mem_kb, reverse=True)[:limit]


# ---------- Disk I/O ----------

def disk_io_stats():
    """Parse /proc/diskstats — real sectors read/written per block device.
    Field layout: https://www.kernel.org/doc/Documentation/iostats.txt"""
    devices = {}
    with open("/proc/diskstats") as f:
        for line in f:
            fields = line.split()
            if len(fields) < 14:
                continue
            name = fields[2]
            if name[-1].isdigit() and not name.startswith(("loop", "ram")):
                # keep whole disks + partitions, skip loop/ram devices
                pass
            if name.startswith(("loop", "ram")):
                continue
            devices[name] = {
                "reads_completed": int(fields[3]),
                "sectors_read": int(fields[5]),
                "writes_completed": int(fields[7]),
                "sectors_written": int(fields[9]),
            }
    return devices


def disk_io_delta(before, after, sector_size=512):
    """Compute real KB read/written between two disk_io_stats() snapshots."""
    delta = {}
    for name, after_vals in after.items():
        before_vals = before.get(name)
        if not before_vals:
            continue
        read_kb = (after_vals["sectors_read"] - before_vals["sectors_read"]) * sector_size / 1024
        write_kb = (after_vals["sectors_written"] - before_vals["sectors_written"]) * sector_size / 1024
        if read_kb or write_kb:
            delta[name] = {"read_kb": round(read_kb, 1), "write_kb": round(write_kb, 1)}
    return delta


# ---------- Snapshot used by the Incident Detector ----------

def snapshot():
    return {
        "cpu_percent": cpu_utilization_percent(sample_interval=0.15),
        "cpu_freq_mhz": cpu_frequencies_mhz(),
        "cpu_governor": cpu_governor(),
        "cpu_temp_c": cpu_temperature_c(),
        "memory": memory_stats(),
        "zombies": zombies(),
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), indent=2))
