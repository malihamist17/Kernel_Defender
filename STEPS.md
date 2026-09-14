# Kernel Defender — Build Guide (Full System)

## 0. Run it

```bash
cd kernel_defender/backend
python3 -m venv venv          # first time only
source venv/bin/activate
pip install -r requirements.txt
python app.py                 # starts API on :8000
```
Open `frontend/dashboard.html` in a browser on the **same Linux machine**
(VM or bare metal — must be real Linux, not Windows, since this reads
`/proc` and `/sys` directly). Root/`sudo python app.py` is only needed to
unlock the CPU governor write in the CPU Lab — everything else (memory,
I/O, deadlock, starvation, zombie, processes) works fully unprivileged.

---

## 1. What's in the full system now

| Nav section | File(s) | What it does |
|---|---|---|
| Dashboard | `monitor.py`, `app.py: /api/snapshot` | Live CPU/mem/temp/zombie overview + auto-generated incidents |
| CPU Lab | `experiments/cpu_experiment.py` | Real CPU-bound workload + real `scaling_governor` write via `/sys` |
| LockGuard | `lockguard.py`, `experiments/deadlock_experiment.py`, `experiments/starvation_experiment.py` | Real wait-for graph + DFS cycle detection over real OS threads; Guard ON/OFF comparison with measured detection time |
| Processes | `monitor.py: list_processes/process_tree`, `experiments/zombie_experiment.py` | Live `/proc` process list + a real `os.fork()`-based zombie you can verify with `ps -T -p <pid>` |
| Memory | `experiments/memory_experiment.py`, `monitor.py: memory_stats/top_memory_consumers` | Real page-touched allocation, measured system-wide effect via `/proc/meminfo`, and top-RSS process ranking |
| I/O | `experiments/io_experiment.py`, `monitor.py: disk_io_stats` | Real `fsync`'d file write/read, measured via `/proc/diskstats` sector deltas |
| Experiment Lab | frontend only | Quick-launch links into each engine's tab |
| Analytics | `db.py`, `app.py: /api/analytics/history` | SQLite-persisted history of every experiment run — survives backend restarts |
| Execution Trace | `trace.py`, `app.py: /api/trace` | Single unified timeline of COMMAND/INTERFACE/KERNEL_EVENT/MEASUREMENT/RESULT entries across all engines |
| Learn | `app.py: LEARN_CONTENT` | Short OS-concept explanations tied to each incident type |

All 27 REST endpoints were checked to register and respond correctly
before this was handed to you (I ran each handler function directly with
a lightweight FastAPI stub, since I don't have real Linux hardware access
in my own environment — you should still smoke-test the full stack once
on your machine per the checklist below).

---

## 2. First-run checklist (do this before your demo, not during it)

1. `python app.py` → confirm `Uvicorn running on http://0.0.0.0:8000` with no traceback.
2. Open `dashboard.html` → Dashboard tab should flip to `● LIVE` within ~3s.
3. **CPU Lab** → click "Run Experiment" → confirm JSON trace appears; note
   that `governor`/`temp_c` will show `unavailable`/`null` on a VM — expected.
4. **LockGuard** → "Start Deadlock" → confirm `cycle_detected` is non-null →
   "Recover" → confirm `snapshot_after.cycle` becomes `null`. Then try
   "Guard ON vs OFF Comparison" and note the measured detection time.
5. **LockGuard** → "Start Starvation" → wait 3s → table should show one
   thread with a visibly lower service count / higher avg wait → "Apply
   Fairness Fix" → numbers should even out on the next report.
6. **Processes** → "Create Controlled Zombie" → "Check Status" should show
   `Z (zombie)` → open a terminal and run the printed `verify_command`
   yourself to see it independently → "Reap" → status should go to "gone".
7. **Memory** → "Allocate" 512MB for a few seconds → watch the Dashboard's
   memory% rise in another tab → "Release" → confirm it drops back.
8. **I/O** → run a 100–200MB test → confirm `disk_delta` shows your real
   block device name (e.g. `sda`, `vda`, `nvme0n1`) with a write_kb close
   to the size you requested.
9. **Analytics** → refresh → every experiment you just ran should appear
   here, timestamped, and should *still be there* after you restart
   `app.py` (that's the SQLite persistence working).
10. **Execution Trace** → refresh → you should see a readable timeline of
    everything you just did, in order.
11. **Record your demo now.** This is your working fallback if anything
    breaks on the actual presentation machine.

---

## 3. What to say in your defense

- **"Where's the kernel interaction?"** — point to the specific files:
  `/proc/stat`, `/proc/meminfo`, `/proc/[pid]/status`, `/proc/[pid]/task/`,
  `/proc/diskstats`, `/sys/devices/system/cpu/cpu*/cpufreq/*`,
  `/sys/class/thermal/thermal_zone*/temp`, and `os.fork()`/`os.waitpid()`
  for real process lifecycle control. None of these are simulated.
- **"How many OS concepts?"** — five are cleanly separated by file: CPU
  scheduling/utilization accounting, CPU frequency governors, process/thread
  lifecycle (including zombie states), memory management, and
  synchronization (deadlock + starvation via a real resource-allocation
  wait-for graph). I/O monitoring via `/proc/diskstats` is a sixth if you
  want to count it separately.
- **"Is the deadlock detector real, or scripted?"** — it's a real DFS
  cycle-detection algorithm running against live thread/lock state,
  the same theoretical model (resource allocation graph) taught for OS
  deadlock detection — see `lockguard.py`'s module docstring for exactly
  how it differs from kernel-level futex introspection (which needs
  eBPF/ptrace and is flagged as optional Phase 3 work below).

---

## 4. If you still have time before your deadline (nice-to-haves, in priority order)

1. **Live charts** (CPU freq, memory over time) using Chart.js via CDN —
   sample `monitor.py` functions every 200ms during an experiment and plot
   the returned series; this directly matches the "live visualization"
   mockups in your original design doc.
2. **`subprocess`-based literal commands in the trace** — e.g. wrap
   `ps -T -p <pid>` with `subprocess.run(...)` in the zombie experiment and
   push the literal stdout into `trace.py`, so the Execution Trace panel
   shows the actual shell command + output your doc's section 9 describes.
3. **Guard ON/OFF for starvation**, mirroring the deadlock comparison —
   run the biased version and the `fair_mode` version back-to-back and
   report both wait-time distributions in one call.

## 5. Explicitly out of scope for a "few days" deadline — do not attempt

- eBPF/kernel tracing (Phase 3 in the original doc) — real value, but a
  multi-day learning curve on its own; not worth the risk this close to
  a deadline.
- A custom Loadable Kernel Module — same reasoning, plus real risk of
  crashing the demo machine.
- A full React frontend — the single-file dashboard you have is faster to
  demo reliably and needs no build step; don't rebuild it in React unless
  you have days to spare afterward.
