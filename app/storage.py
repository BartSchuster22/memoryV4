"""Store port and SQLite adapter for the MemoryV4 governed foundation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from app.migrations import migrate
from app.schemas import Record, RecordCreate, ScopePath, SearchResult, utc_now


class Store(Protocol):
    def create_record(self, rec: RecordCreate, *, actor: str) -> Record: ...
    def get_record(self, record_id: str) -> Record | None: ...
    def list_records(self, *, scope_path: str, include_public: bool = False) -> list[Record]: ...
    def search_records(
        self,
        query: str,
        *,
        scope_path: str,
        actor: str,
        include_public: bool = False,
        limit: int = 25,
    ) -> list[SearchResult]: ...


class SqliteStore:
    """SQLite-only adapter. SQL/FTS details stay below this boundary."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        migrate(database_path)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _has_fts(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'"
        ).fetchone()
        return row is not None

    def create_record(self, rec: RecordCreate, *, actor: str) -> Record:
        record = Record(**rec.model_dump(), author_actor=actor)
        now = utc_now()
        record.created_at = now
        record.updated_at = now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO records(
                  id, title, content, role, lifecycle, scope_path, entity_type, topic,
                  source_refs_json, provenance_json, attrs_json, author_actor,
                  superseded_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.title,
                    record.content,
                    record.role.value,
                    record.lifecycle.value,
                    record.scope_path,
                    record.entity_type,
                    record.topic,
                    json.dumps(record.source_refs, sort_keys=True),
                    json.dumps(record.provenance, sort_keys=True),
                    json.dumps(record.attrs, sort_keys=True),
                    actor,
                    record.superseded_by,
                    record.created_at,
                    record.updated_at,
                ),
            )
            if self._has_fts(conn):
                conn.execute(
                    "INSERT INTO records_fts(rowid, id, title, content) "
                    "VALUES ((SELECT rowid FROM records WHERE id = ?), ?, ?, ?)",
                    (record.id, record.id, record.title, record.content),
                )
            self.write_audit(
                conn,
                action="record.create",
                object_type="record",
                object_id=record.id,
                actor=actor,
                scope_path=record.scope_path,
                detail={"role": record.role.value, "lifecycle": record.lifecycle.value},
            )
        return record

    def get_record(self, record_id: str) -> Record | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return self._record_from_row(row) if row else None

    def list_records(self, *, scope_path: str, include_public: bool = False) -> list[Record]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM records WHERE scope_path IN ({placeholders}) "
                "ORDER BY created_at, id",
                scopes,
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def search_records(
        self,
        query: str,
        *,
        scope_path: str,
        actor: str,
        include_public: bool = False,
        limit: int = 25,
    ) -> list[SearchResult]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        with self._connect() as conn:
            rows = self._search_rows(conn, query=query, scopes=scopes, limit=limit)
            records = [self._record_from_row(row) for row in rows]
            self.write_retrieval(
                conn,
                query=query,
                scope_path=scope_path,
                actor=actor,
                result_count=len(records),
                degraded=not self._has_fts(conn),
            )
        return [
            SearchResult(record=record, score=float(len(records) - idx))
            for idx, record in enumerate(records)
        ]

    def _search_rows(
        self, conn: sqlite3.Connection, *, query: str, scopes: list[str], limit: int
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in scopes)
        if self._has_fts(conn):
            try:
                return conn.execute(
                    f"""
                    SELECT records.*
                    FROM records_fts
                    JOIN records ON records.rowid = records_fts.rowid
                    WHERE records_fts MATCH ? AND records.scope_path IN ({placeholders})
                    ORDER BY bm25(records_fts), records.updated_at DESC
                    LIMIT ?
                    """,
                    [query, *scopes, limit],
                ).fetchall()
            except sqlite3.OperationalError:
                pass
        like = f"%{query}%"
        return conn.execute(
            f"""
            SELECT * FROM records
            WHERE scope_path IN ({placeholders}) AND (title LIKE ? OR content LIKE ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*scopes, like, like, limit],
        ).fetchall()

    def write_audit(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        object_type: str,
        object_id: str,
        actor: str,
        scope_path: str,
        detail: dict[str, object],
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_events(
              action, object_type, object_id, actor, scope_path, detail_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                object_type,
                object_id,
                actor,
                scope_path,
                json.dumps(detail, sort_keys=True),
                utc_now(),
            ),
        )

    def write_retrieval(
        self,
        conn: sqlite3.Connection,
        *,
        query: str,
        scope_path: str,
        actor: str,
        result_count: int,
        degraded: bool,
    ) -> None:
        conn.execute(
            """
            INSERT INTO retrieval_events(
              query, scope_path, actor, result_count, degraded, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (query, scope_path, actor, result_count, int(degraded), utc_now()),
        )

    def count_audit_events(self, *, action: str | None = None) -> int:
        with self._connect() as conn:
            if action is None:
                return int(conn.execute("SELECT count(*) FROM audit_events").fetchone()[0])
            return int(
                conn.execute(
                    "SELECT count(*) FROM audit_events WHERE action = ?", (action,)
                ).fetchone()[0]
            )

    def count_retrieval_events(self, *, query: str | None = None) -> int:
        with self._connect() as conn:
            if query is None:
                return int(conn.execute("SELECT count(*) FROM retrieval_events").fetchone()[0])
            return int(
                conn.execute(
                    "SELECT count(*) FROM retrieval_events WHERE query = ?", (query,)
                ).fetchone()[0]
            )

    def _record_from_row(self, row: sqlite3.Row) -> Record:
        return Record(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            role=row["role"],
            lifecycle=row["lifecycle"],
            scope_path=row["scope_path"],
            entity_type=row["entity_type"],
            topic=row["topic"],
            source_refs=json.loads(row["source_refs_json"]),
            provenance=json.loads(row["provenance_json"]),
            attrs=json.loads(row["attrs_json"]),
            author_actor=row["author_actor"],
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def probe_sqlite(database_path: Path) -> dict[str, str]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    migrate(database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        result = conn.execute("PRAGMA quick_check").fetchone()
    status = "ok" if result and result[0] == "ok" else "degraded"
    return {"backend": "sqlite", "path": str(database_path), "status": status}
