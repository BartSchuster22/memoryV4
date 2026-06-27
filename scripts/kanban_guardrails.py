#!/usr/bin/env python3
"""Board-local MemoryV4 Kanban guardrails.

This watchdog is intentionally read-only and silent when healthy. It inspects the
MemoryV4 board database and host pressure, prints actionable alerts only when a
human/operator decision is needed, and never mutates task state.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_DB = Path("/home/herman/.hermes/kanban/boards/memoryv4-next/kanban.db")
DEFAULT_WORKSPACE = Path("/srv/memoryV4")
DEFAULT_NO_PROGRESS_MINUTES = 30
DEFAULT_REVIEW_GATE_MINUTES = 60
DEFAULT_BLOCKED_GATE_MINUTES = 60
DEFAULT_PRESSURE_LOOKBACK_MINUTES = 120
LOAD_STOP = 12.0
SWAP_STOP_PERCENT = 50.0

REMEDIATION_HINTS = (
    "remediated",
    "fixed",
    "ready for review",
    "ready to resume",
    "unblock",
    "unblocked",
    "approved",
    "review passed",
    "tests pass",
)
PRESSURE_HINTS = ("host pressure", "load average", "swap", "overload", "fleet-level")


@dataclass(frozen=True)
class Alert:
    kind: str
    task_id: str
    title: str
    message: str

    def render(self) -> str:
        return f"[{self.kind}] {self.task_id} {self.title}: {self.message}"


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def now_epoch() -> int:
    return int(time.time())


def latest_comment_after(con: sqlite3.Connection, task_id: str, after_ts: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT id, author, body, created_at
        FROM task_comments
        WHERE task_id = ? AND created_at > ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (task_id, after_ts),
    ).fetchone()


def latest_event(con: sqlite3.Connection, task_id: str, kind: str | None = None) -> sqlite3.Row | None:
    if kind:
        return con.execute(
            "SELECT id, kind, payload, created_at FROM task_events WHERE task_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
            (task_id, kind),
        ).fetchone()
    return con.execute(
        "SELECT id, kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def has_recent_progress(con: sqlite3.Connection, task_id: str, since_ts: int) -> bool:
    row = con.execute(
        "SELECT 1 FROM task_comments WHERE task_id = ? AND created_at >= ? LIMIT 1",
        (task_id, since_ts),
    ).fetchone()
    if row:
        return True
    row = con.execute(
        "SELECT 1 FROM task_events WHERE task_id = ? AND created_at >= ? AND kind IN ('heartbeat','commented','completed','blocked','spawned') LIMIT 1",
        (task_id, since_ts),
    ).fetchone()
    return bool(row)


def host_pressure() -> tuple[float | None, float | None]:
    load1 = None
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        pass

    swap_percent = None
    try:
        out = subprocess.check_output(["free", "-b"], text=True, timeout=5)
        for line in out.splitlines():
            if line.startswith("Swap:"):
                parts = line.split()
                total = int(parts[1])
                used = int(parts[2])
                swap_percent = (used / total * 100.0) if total else 0.0
                break
    except Exception:
        pass
    return load1, swap_percent


def no_progress_alerts(con: sqlite3.Connection, cutoff: int) -> Iterable[Alert]:
    rows = con.execute(
        """
        SELECT id, title, started_at, last_heartbeat_at, current_run_id
        FROM tasks
        WHERE status = 'running'
        ORDER BY started_at
        """
    ).fetchall()
    for row in rows:
        last = max(row["last_heartbeat_at"] or 0, row["started_at"] or 0)
        if last < cutoff and not has_recent_progress(con, row["id"], cutoff):
            yield Alert(
                "no-progress",
                row["id"],
                row["title"],
                f"running with no heartbeat/comment/event since {last}; human should inspect before reclaim/resume",
            )


def blocked_gate_rescue_alerts(con: sqlite3.Connection, cutoff: int) -> Iterable[Alert]:
    rows = con.execute(
        "SELECT id, title FROM tasks WHERE status = 'blocked' ORDER BY priority DESC, created_at"
    ).fetchall()
    for row in rows:
        ev = latest_event(con, row["id"], "blocked")
        if not ev or ev["created_at"] > cutoff:
            continue
        comment = latest_comment_after(con, row["id"], ev["created_at"])
        if comment and any(h in comment["body"].lower() for h in REMEDIATION_HINTS):
            yield Alert(
                "blocked-gate-rescue",
                row["id"],
                row["title"],
                "blocked card has post-block remediation/approval language; human-only action required to verify and unblock",
            )


def review_deadlock_alerts(con: sqlite3.Connection, cutoff: int) -> Iterable[Alert]:
    rows = con.execute(
        "SELECT id, title FROM tasks WHERE status = 'blocked' ORDER BY priority DESC, created_at"
    ).fetchall()
    for row in rows:
        ev = latest_event(con, row["id"], "blocked")
        if not ev or ev["created_at"] > cutoff:
            continue
        payload = ev["payload"] or ""
        text = payload.lower()
        if "review-required" not in text:
            continue
        child_open = con.execute(
            """
            SELECT COUNT(*) AS n
            FROM task_links l JOIN tasks c ON c.id = l.child_id
            WHERE l.parent_id = ? AND c.status NOT IN ('done','archived')
            """,
            (row["id"],),
        ).fetchone()["n"]
        if child_open == 0:
            yield Alert(
                "review-gate-deadlock",
                row["id"],
                row["title"],
                "review-required block is older than threshold and has no open child review card; route to ulrich or unblock after human review",
            )


def host_pressure_resume_alerts(con: sqlite3.Connection, cutoff: int) -> Iterable[Alert]:
    load1, swap = host_pressure()
    if load1 is None or swap is None:
        return []
    if load1 >= LOAD_STOP or swap >= SWAP_STOP_PERCENT:
        return []
    alerts: list[Alert] = []
    rows = con.execute(
        "SELECT id, title FROM tasks WHERE status = 'blocked' ORDER BY priority DESC, created_at"
    ).fetchall()
    for row in rows:
        ev = latest_event(con, row["id"], "blocked")
        if not ev or ev["created_at"] < cutoff:
            continue
        text = (ev["payload"] or "").lower()
        if any(h in text for h in PRESSURE_HINTS):
            alerts.append(
                Alert(
                    "host-pressure-resume",
                    row["id"],
                    row["title"],
                    f"host pressure is currently below gates (load1={load1:.2f}, swap={swap:.1f}%); human may resume one card if worktree is clean",
                )
            )
    return alerts


def inspect(db: Path, args: argparse.Namespace) -> list[Alert]:
    if not db.exists():
        return [Alert("guardrail-error", "board", "kanban db", f"database not found: {db}")]
    now = now_epoch()
    with connect(db) as con:
        alerts: list[Alert] = []
        alerts.extend(no_progress_alerts(con, now - args.no_progress_minutes * 60))
        alerts.extend(blocked_gate_rescue_alerts(con, now - args.blocked_gate_minutes * 60))
        alerts.extend(review_deadlock_alerts(con, now - args.review_gate_minutes * 60))
        alerts.extend(host_pressure_resume_alerts(con, now - args.pressure_lookback_minutes * 60))
        return alerts


def self_test() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kanban.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, priority INTEGER DEFAULT 0, created_at INTEGER, started_at INTEGER, last_heartbeat_at INTEGER, current_run_id INTEGER);
            CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT, created_at INTEGER);
            CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
            CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
            """
        )
        old = now_epoch() - 7200
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", ("t_run", "stale running", "running", 1, old, old, old, 1))
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", ("t_block", "blocked fixed", "blocked", 1, old, old, old, None))
        con.execute("INSERT INTO task_events(task_id, kind, payload, created_at) VALUES (?,?,?,?)", ("t_block", "blocked", '{"reason":"review-required: needs review"}', old))
        con.execute("INSERT INTO task_comments(task_id, author, body, created_at) VALUES (?,?,?,?)", ("t_block", "human", "tests pass; ready to resume", old + 10))
        con.commit()
        ns = argparse.Namespace(no_progress_minutes=30, blocked_gate_minutes=30, review_gate_minutes=30, pressure_lookback_minutes=120)
        alerts = inspect(db, ns)
        kinds = {a.kind for a in alerts}
        assert "no-progress" in kinds, kinds
        assert "blocked-gate-rescue" in kinds, kinds
        assert "review-gate-deadlock" in kinds, kinds
    print("self-test ok")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path(os.environ.get("HERMES_KANBAN_DB", DEFAULT_DB)))
    p.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    p.add_argument("--no-progress-minutes", type=int, default=DEFAULT_NO_PROGRESS_MINUTES)
    p.add_argument("--blocked-gate-minutes", type=int, default=DEFAULT_BLOCKED_GATE_MINUTES)
    p.add_argument("--review-gate-minutes", type=int, default=DEFAULT_REVIEW_GATE_MINUTES)
    p.add_argument("--pressure-lookback-minutes", type=int, default=DEFAULT_PRESSURE_LOOKBACK_MINUTES)
    p.add_argument("--json", action="store_true", help="emit JSON array instead of text")
    p.add_argument("--self-test", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return self_test()
    alerts = inspect(args.db, args)
    if not alerts:
        return 0
    if args.json:
        print(json.dumps([a.__dict__ for a in alerts], indent=2, sort_keys=True))
    else:
        print("MemoryV4 Kanban guardrail alerts (read-only; human-only escalation):")
        for alert in alerts:
            print(alert.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
