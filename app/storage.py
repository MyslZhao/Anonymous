"""SQLite 存档：保存每次模拟的事件、建议、裁决与指标，供回看与对比。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from app.config import settings

_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                seed INTEGER NOT NULL,
                severity INTEGER NOT NULL,
                decision_agent INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maps (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )


# ---------------------------------------------------------------- 自定义地图


def save_map(map_id: str, name: str, payload: dict) -> None:
    init_db()
    created = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO maps (id, name, created_at, payload) VALUES (?,?,?,?)",
            (map_id, name, created, json.dumps(payload, ensure_ascii=False)),
        )


def load_map(map_id: str) -> dict | None:
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT payload FROM maps WHERE id = ?", (map_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def list_maps(limit: int = 50) -> list[dict]:
    init_db()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM maps ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"id": row[0], "name": row[1], "created_at": row[2]} for row in rows]


def save_run(seed: int, severity: int, decision_agent: bool, payload: dict) -> int:
    init_db()
    created = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK, _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO runs (created_at, seed, severity, decision_agent, payload) VALUES (?,?,?,?,?)",
            (created, seed, severity, int(decision_agent), json.dumps(payload, ensure_ascii=False)),
        )
        return int(cursor.lastrowid)


def list_runs(limit: int = 20) -> list[dict]:
    init_db()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, seed, severity, decision_agent, payload FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    runs: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row[5])
        except json.JSONDecodeError:
            payload = {}
        metrics = payload.get("metrics", {})
        runs.append(
            {
                "id": row[0],
                "created_at": row[1],
                "seed": row[2],
                "severity": row[3],
                "decision_agent": bool(row[4]),
                "coverage": metrics.get("coverage", 0),
                "rescued": metrics.get("rescued", 0),
                "conflicts": metrics.get("conflicts", 0),
            }
        )
    return runs
