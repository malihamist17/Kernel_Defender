
"""
ipc_experiment.py — Real IPC between two real OS processes.
"""
import multiprocessing as mp
import os
import socket
import time
import trace as tracelog


def _pipe_child(conn, n_messages):
    """Runs in the CHILD process — must be at module level for pickling."""
    count = 0
    while count < n_messages:
        conn.recv()
        count += 1
    conn.send(f"child_pid={os.getpid()} received={count}")


def pipe_experiment(n_messages=1000):
    """Two processes talk over an anonymous pipe (pipe(2) syscall)."""
    parent_conn, child_conn = mp.Pipe()

    p = mp.Process(target=_pipe_child, args=(child_conn, n_messages))
    t0 = time.time()
    p.start()
    for i in range(n_messages):
        parent_conn.send(f"msg_{i}")
    report = parent_conn.recv()
    p.join()
    elapsed = round(time.time() - t0, 4)

    tracelog.log("KERNEL_EVENT", "IPC Lab",
                 f"pipe() -> parent={os.getpid()} child={p.pid}")
    return {
        "method": "pipe",
        "parent_pid": os.getpid(),
        "child_pid": p.pid,
        "messages": n_messages,
        "elapsed_sec": elapsed,
        "throughput_msg_per_sec": round(n_messages / elapsed, 1),
        "avg_latency_ms": round(elapsed / n_messages * 1000, 3),
        "child_report": report,
    }

def _queue_consumer(queue, count, out):
    """Runs in the CHILD process."""
    received = 0
    while received < count:
        queue.get()
        received += 1
    out.put(received)


def queue_experiment(n_messages=1000):
    """Queue = pipe + internal lock + buffer."""
    q = mp.Queue()
    result_q = mp.Queue()

    p = mp.Process(target=_queue_consumer, args=(q, n_messages, result_q))
    t0 = time.time()
    p.start()
    for i in range(n_messages):
        q.put(f"msg_{i}")
    received = result_q.get()
    p.join()
    elapsed = round(time.time() - t0, 4)

    tracelog.log("KERNEL_EVENT", "IPC Lab",
                 f"Queue -> parent={os.getpid()} child={p.pid}")
    return {
        "method": "queue",
        "parent_pid": os.getpid(),
        "child_pid": p.pid,
        "messages": n_messages,
        "received": received,
        "elapsed_sec": elapsed,
        "throughput_msg_per_sec": round(n_messages / elapsed, 1),
    }

def _shm_writer(name, count, rec_size):
    """Runs in the CHILD process."""
    from multiprocessing import shared_memory
    shm = shared_memory.SharedMemory(name=name)
    for i in range(count):
        data = f"msg_{i}".encode().ljust(rec_size, b"\0")
        shm.buf[i * rec_size:(i + 1) * rec_size] = data
    shm.close()


def shared_memory_experiment(n_messages=1000):
    """Both processes write/read the SAME physical RAM pages."""
    from multiprocessing import shared_memory
    RECORD_SIZE = 64
    shm = shared_memory.SharedMemory(create=True, size=RECORD_SIZE * n_messages)

    p = mp.Process(target=_shm_writer, args=(shm.name, n_messages, RECORD_SIZE))
    t0 = time.time()
    p.start()
    p.join()
    elapsed = round(time.time() - t0, 4)

    read_count = 0
    for i in range(n_messages):
        rec = bytes(shm.buf[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]).rstrip(b"\0")
        if rec:
            read_count += 1

    shm.close()
    shm.unlink()

    tracelog.log("KERNEL_EVENT", "IPC Lab",
                 f"shm_open -> parent={os.getpid()} child={p.pid}")
    return {
        "method": "shared_memory",
        "parent_pid": os.getpid(),
        "child_pid": p.pid,
        "messages": n_messages,
        "read_back": read_count,
        "elapsed_sec": elapsed,
        "throughput_msg_per_sec": round(n_messages / elapsed, 1),
    }
def _socket_client(path, count):
    """Runs in the CHILD process."""
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(path)
    for i in range(count):
        c.sendall(f"msg_{i}".encode())
    c.close()


def socket_experiment(n_messages=1000):
    """Unix domain socket — same syscall as TCP but local only."""
    sock_path = f"/tmp/ipc_test_{os.getpid()}.sock"
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)

    p = mp.Process(target=_socket_client, args=(sock_path, n_messages))
    t0 = time.time()
    p.start()
    conn, _ = server.accept()
    received = 0
    buf = b""
    while received < n_messages:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        received = buf.count(b"msg_")
    p.join()
    conn.close()
    server.close()
    os.unlink(sock_path)
    elapsed = round(time.time() - t0, 4)

    tracelog.log("KERNEL_EVENT", "IPC Lab",
                 f"socket(AF_UNIX) -> parent={os.getpid()} child={p.pid}")
    return {
        "method": "socket",
        "parent_pid": os.getpid(),
        "child_pid": p.pid,
        "messages": n_messages,
        "elapsed_sec": elapsed,
        "throughput_msg_per_sec": round(n_messages / elapsed, 1),
    }

def compare_all(n_messages=500):
    """Run every method back-to-back — the educational payoff."""
    results = {}
    for name, fn in [
        ("pipe", pipe_experiment),
        ("queue", queue_experiment),
        ("shared_memory", shared_memory_experiment),
        ("socket", socket_experiment),
    ]:
        try:
            results[name] = fn(n_messages)
        except Exception as e:
            results[name] = {"error": str(e)}
    return results



