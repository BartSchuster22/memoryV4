"""SQLite implementation of the MemoryV4 Store port."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.migrations import run_migrations
from app.models import AuditEvent, Filter, Lifecycle, Record, Role


@dataclass(frozen=True)
class StorageHealth:
    backend: str
    path: str
    status: str


class SqliteStore:
    """SQLite-only Store adapter for P1."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_record(self, rec: Record, *, actor: str) -> Record:
        with self._connect() as conn:
            self._insert_record(conn, rec)
            self._write_audit(conn, AuditEvent(action="create_record", actor=actor, record_id=rec.id))
        return rec

    def get_record(self, rid: str) -> Record | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, entity_id, entity_type, title, topic, content, role, lifecycle,
                       scope_path, source_refs_json, attrs_json, superseded_by
                FROM records
                WHERE id = ?
                """,
                (rid,),
            ).fetchone()
        return _record_from_row(row) if row else None

    def supersede(self, old_id: str, new: Record, *, actor: str) -> Record:
        with self._connect() as conn:
            old = conn.execute("SELECT id FROM records WHERE id = ?", (old_id,)).fetchone()
            if old is None:
                raise KeyError(f"record not found: {old_id}")
            self._insert_record(conn, new)
            conn.execute(
                "UPDATE records SET lifecycle = ?, superseded_by = ?, updated_at = ? WHERE id = ?",
                (Lifecycle.SUPERSEDED.value, new.id, _now(), old_id),
            )
            self._write_audit(conn, AuditEvent(action="create_record", actor=actor, record_id=new.id))
            self._write_audit(
                conn,
                AuditEvent(action="supersede", actor=actor, record_id=old_id, detail={"superseded_by": new.id}),
            )
        return new

    def transition(self, rid: str, lifecycle: Lifecycle, *, actor: str) -> Record:
        with self._connect() as conn:
            existing = conn.execute("SELECT id FROM records WHERE id = ?", (rid,)).fetchone()
            if existing is None:
                raise KeyError(f"record not found: {rid}")
            conn.execute(
                "UPDATE records SET lifecycle = ?, updated_at = ? WHERE id = ?",
                (lifecycle.value, _now(), rid),
            )
            self._write_audit(
                conn,
                AuditEvent(action="transition", actor=actor, record_id=rid, detail={"lifecycle": lifecycle.value}),
            )
        record = self.get_record(rid)
        if record is None:
            raise KeyError(f"record not found after transition: {rid}")
        return record

    def lexical_rank(self, query: str, f: Filter, k: int) -> list[str]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms or k <= 0:
            return []
        clauses, params = _filter_sql(f)
        sql = """
            SELECT id, title, topic, content
            FROM records
            WHERE """ + " AND ".join(clauses) + " ORDER BY updated_at DESC, id ASC"
        ranked: list[tuple[int, str]] = []
        with self._connect() as conn:
            for row in conn.execute(sql, params).fetchall():
                haystack = " ".join([row["title"], row["topic"], row["content"]]).casefold()
                score = sum(1 for term in terms if term in haystack)
                if score:
                    ranked.append((score, row["id"]))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [rid for _, rid in ranked[:k]]

    def vector_rank(self, qvec: bytes, f: Filter, k: int) -> list[str]:
        # P1 reserves the vector lane behind the Store port but does not build an
        # embeddings backend. Returning no candidates preserves baseline behavior.
        return []

    def write_audit(self, ev: AuditEvent) -> None:
        with self._connect() as conn:
            self._write_audit(conn, ev)

    def write_finding(self, finding: dict[str, Any]) -> None:
        refs_json = json.dumps(finding.get("refs", []), sort_keys=True)
        detail_json = json.dumps(finding.get("detail", {}), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO health_findings(kind, refs_json, detail_json, status, created_at)
                VALUES (?, ?, ?, 'open', ?)
                """,
                (finding["kind"], refs_json, detail_json, _now()),
            )

    def quick_check(self) -> StorageHealth:
        with self._connect() as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
        status = "ok" if result and result[0] == "ok" else "degraded"
        return StorageHealth(backend="sqlite", path=str(self.database_path), status=status)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            run_migrations(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_record(self, conn: sqlite3.Connection, rec: Record) -> None:
        now = _now()
        conn.execute(
            """
            INSERT INTO records(
                id, entity_id, entity_type, title, topic, content, role, lifecycle,
                scope_path, source_refs_json, attrs_json, superseded_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.id, rec.entity_id, rec.entity_type, rec.title, rec.topic, rec.content,
                rec.role.value, rec.lifecycle.value, rec.scope_path,
                json.dumps(rec.source_refs, sort_keys=True), json.dumps(rec.attrs, sort_keys=True),
                rec.superseded_by, now, now,
            ),
        )

    def _write_audit(self, conn: sqlite3.Connection, ev: AuditEvent) -> None:
        conn.execute(
            """
            INSERT INTO audit_events(action, actor, record_id, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ev.action, ev.actor, ev.record_id, json.dumps(ev.detail, sort_keys=True), _now()),
        )


def probe_sqlite(database_path: Path) -> StorageHealth:
    return SqliteStore(database_path).quick_check()


def _filter_sql(f: Filter) -> tuple[list[str], list[str]]:
    clauses = ["1 = 1"]
    params: list[str] = []
    if f.entity_type is not None:
        clauses.append("entity_type = ?")
        params.append(f.entity_type)
    if f.role is not None:
        clauses.append("role = ?")
        params.append(f.role)
    if f.lifecycle is not None:
        clauses.append("lifecycle = ?")
        params.append(f.lifecycle)
    if f.topic is not None:
        clauses.append("topic = ?")
        params.append(f.topic)
    if f.scope_prefixes:
        clauses.append("scope_path IN (" + ", ".join("?" for _ in f.scope_prefixes) + ")")
        params.extend(f.scope_prefixes)
    return clauses, params


def _record_from_row(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"], entity_id=row["entity_id"], entity_type=row["entity_type"], title=row["title"], topic=row["topic"],
        content=row["content"], role=Role(row["role"]), lifecycle=Lifecycle(row["lifecycle"]),
        scope_path=row["scope_path"], source_refs=json.loads(row["source_refs_json"]),
        attrs=json.loads(row["attrs_json"]), superseded_by=row["superseded_by"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
