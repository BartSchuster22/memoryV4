"""SQLite-only D0 storage probe.

This is not the full governed Store port. It is a tiny shell used to prove that
the single-container baseline is wired to SQLite and has a writable durable path.
Later phases add migrations, ports, and the governed schema behind this boundary.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageHealth:
    backend: str
    path: str
    status: str


def probe_sqlite(database_path: Path) -> StorageHealth:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        result = conn.execute("PRAGMA quick_check").fetchone()
    status = "ok" if result and result[0] == "ok" else "degraded"
    return StorageHealth(backend="sqlite", path=str(database_path), status=status)
