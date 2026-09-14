"""
db.py — lightweight SQLite persistence so Analytics/History survive a
backend restart. Uses only the stdlib sqlite3 module — no extra install.
"""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "kernel_defender.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            result_json TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            severity TEXT NOT NULL,
            detail TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """)
    return conn


def save_experiment(exp_type, result):
    conn = _connect()
    conn.execute(
        "INSERT INTO experiments (type, result_json, ts) VALUES (?, ?, ?)",
        (exp_type, json.dumps(result), time.time()),
    )
    conn.commit()
    conn.close()


def get_experiment_history(limit=50, exp_type=None):
    conn = _connect()
    if exp_type:
        rows = conn.execute(
            "SELECT type, result_json, ts FROM experiments WHERE type=? ORDER BY id DESC LIMIT ?",
            (exp_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT type, result_json, ts FROM experiments ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [{"type": t, "result": json.loads(r), "ts": ts} for t, r, ts in rows]


def save_incident(incident):
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO incidents (id, type, severity, detail, ts) VALUES (?, ?, ?, ?, ?)",
        (incident["id"], incident["type"], incident["severity"], incident["detail"], incident["created"]),
    )
    conn.commit()
    conn.close()


def get_incidents(limit=50):
    conn = _connect()
    rows = conn.execute(
        "SELECT id, type, severity, detail, ts FROM incidents ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [{"id": i, "type": t, "severity": s, "detail": d, "created": ts} for i, t, s, d, ts in rows]
