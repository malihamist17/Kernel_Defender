# 🛡️ Kernel Defender

**Kernel Defender** is a full-stack Linux kernel monitoring, IPC analysis, and concurrency lab. It provides real-time system diagnostics, diagnostic incident investigation, and interactive operating system experiments—ranging from CPU stress monitoring and memory pressure to real deadlock detection and dynamic priority aging.

---

## 🏗️ System Architecture

```
+-----------------------------------------------------------------------------------+
|                                  FRONTEND UI                                      |
|                 (Single-Page Dashboard / HTML5 / JS / Chart.js)                   |
+-----------------------------------------------------------------------------------+
                                         │  HTTP / REST API
                                         ▼
+-----------------------------------------------------------------------------------+
|                                 FASTAPI BACKEND                                   |
|                                    (app.py)                                       |
|                                        │                                          |
|   ┌───────────────────┬────────────────┴──────────────────┬──────────────────┐    |
|   ▼                   ▼                                   ▼                  ▼    |
| [Monitor Module]   [Incident Engine]               [LockGuard Engine]   [Lab Modules] |
| (/proc & /sys      (Deduplication &                 (Deadlocks, Aging,  (CPU, Memory, |
|  parsing)           Consequence Actions)            Priority Boosting)   IPC, Sched)   |
+-----------------------------------------------------------------------------------+
                                         │  Native Syscalls & Pthreads
                                         ▼
+-----------------------------------------------------------------------------------+
|                                LINUX OS KERNEL                                    |
|                      (/proc, /sys, os.setpriority, POSIX locks)                    |
+-----------------------------------------------------------------------------------+

```

---

## 🚀 Key Features & App Navigation

### 1. Dashboard & Incident Engine

* **Real-Time Telemetry:** Monitors CPU%, Memory%, Governor status, Temperature, and Zombie process counts via `GET /api/snapshot`.
* **Direct `/proc` & `/sys` Parsing:**
* Computes CPU utilization by reading `/proc/stat` deltas over 150ms intervals.
* Reads RAM consumption directly from `/proc/meminfo`.
* Scans `/proc/[pid]/status` for process state `Z` (Zombies).


* **Smart Deduplication:** Flags incidents when resource limits cross thresholds (CPU/RAM > 90%, Zombie count > 0) without spamming redundant alert cards.
* **Honest Investigation & Resolution:**
* **Step 1 (Inspect):** Executes real diagnostic checks (e.g., `ps -o pid,ppid,stat,cmd -p <pid>`).
* **Step 2 (Act):** Shows explicit consequence popups before executing actions. Resolves incidents only if the OS syscall or kernel process action genuinely succeeds (e.g., refusing to fake-reap zombies owned by unparented processes).



---

### 2. Experiment Lab

#### 🟢 CPU Sub-Tab

* **Per-Core Metrics:** Parses individual `cpuN` lines in `/proc/stat` to graph usage across every logical core.
* **Governor Control:** Interface to toggle active frequency governors via `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`.
* **Continuous Stress & Thermal Safety Monitor:**
* Spawns multi-core CPU-bound workers using `multiprocessing.Process`.
* Features an independent safety thread polling CPU temperatures every 0.5s that automatically aborts workloads if safe thermal limits are exceeded.



#### 🔒 LockGuard Sub-Tab (Concurrency & OS Theory)

* **Deadlock Detection & Recovery:**
* Launches real kernel threads (`threading.Thread`) executing circular wait conditions.


* Uses `InstrumentedLock` to intercept lock events and build a live **Wait-For Graph**.


* Runs DFS cycle detection to identify deadlocks and allows user-driven victim preemption recovery.




* **Starvation & Priority Aging:**
* Identifies starved threads (e.g., `T3`) experiencing low service counts and long wait times.
* Applies dynamic **Aging & Priority Boosting** using native Linux `os.setpriority` calls to temporarily escalate thread priorities.
* Restores base priorities once critical sections complete, balancing service counts across threads.



#### 🧠 Memory & Tiered Memory Sub-Tab

* **Memory Pressure Simulation:** Allocates controlled RAM blocks to test OS page reclamation and memory warnings.
* **Tiered Memory Analysis:** Demonstrates userspace page demotion strategies using `madvise(MADV_PAGEOUT)` to offload cold memory pages to swap disks.
* **Virtual-to-Physical Address Translation:** Inspects page tables PGD/PUD/PMD/PTE via `/proc/self/pagemap`.

#### 🔀 IPC & Scheduling Labs

* **IPC Benchmarking:** Compares execution overhead across standard Linux IPC mechanisms: Unix Pipes, Message Queues, Shared Memory (`shm`), and Local Sockets.
* **Process Scheduling Visualizer:** Simulates CPU scheduling algorithms (FCFS, SJF, Round-Robin, and Priority Scheduling) to compare turnaround time, wait time, and throughput.



---

## 🛠️ Installation & Setup

### Prerequisites

* **OS:** Linux (Kali Linux, Ubuntu, or Debian recommended)
* **Python:** 3.9+
* **Privileges:** Standard user (Root required for native governor changes and full system niceness tuning)

### Quickstart

1. **Clone the Repository:**
```bash
git clone https://github.com/your-repo/kernel-defender.git
cd kernel-defender

```


2. **Set Up Python Virtual Environment:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

```


3. **Launch the FastAPI Server:**
```bash
cd backend
python app.py

```


4. **Access the Dashboard:**
Open `frontend/dashboard.html` in your web browser (or navigate to `http://localhost:8000`).

---

## 📡 Key REST API Reference

| Component | Endpoint | Method | Description |
| --- | --- | --- | --- |
| **System** | `/api/snapshot` | `GET` | Fetches system CPU, memory, zombie, and thermal snapshot.

 |
| **Incidents** | `/api/incidents` | `GET` | Lists active deduplicated system incidents.

 |
| **Deadlock** | `/api/experiments/deadlock/start` | `POST` | Triggers circular wait and DFS cycle detection. |
| **Deadlock** | `/api/experiments/deadlock/recover` | `POST` | Recovers from deadlock by preempting victim thread. |
| **Starvation** | `/api/experiments/starvation/start` | `POST` | Starts multi-thread contention experiment. |
| **Starvation** | `/api/experiments/starvation/report` | `GET` | Fetches wait durations and thread priorities. |
| **Starvation** | `/api/experiments/starvation/fix` | `POST` | Enables dynamic priority aging (`os.setpriority`). |
| **Scheduling** | `/api/experiments/scheduling/run` | `POST` | Simulates FCFS, SJF, RR, or Priority scheduling.

 |

---

## 🤝 Project Structure

```
Kernel_Defender/
├── backend/
│   ├── app.py                      # Core FastAPI Server & REST Endpoints
│   ├── lockguard.py                # Merged Deadlock Graph Engine & Dynamic Aging
│   ├── monitor.py                  # Linux /proc and /sys telemetry parser
│   ├── db.py                       # Incident history & SQLite persistence
│   ├── incident_actions.py         # Diagnostic inspection & resolution handler
│   └── experiments/                # Module implementations (CPU, Memory, IPC, etc.)
├── frontend/
│   ├── dashboard.html              # Main User Interface
│   ├── js/                         # Real-time polling & Chart.js rendering
│   └── css/                        # Dashboard styling & alert theme
└── README.md                       # Documentation

```
