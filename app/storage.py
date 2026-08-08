"""Store port and SQLite adapter for the MemoryV4 governed core."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from app.migrations import migrate
from app.schemas import Record, RecordCreate, RecordPatch, ScopePath, SearchResult, utc_now


class IdempotencyConflictError(Exception):
    pass


class VersionConflictError(Exception):
    pass


class RecordStateConflictError(Exception):
    pass


class Store(Protocol):
    def create_record(self, rec: RecordCreate, *, actor: str) -> Record: ...
    def create_record_idempotent(
        self,
        rec: RecordCreate,
        *,
        actor: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Record, bool]: ...
    def get_record(self, record_id: str) -> Record | None: ...
    def get_visible_record(
        self,
        record_id: str,
        *,
        scope_path: str,
        include_public: bool = False,
    ) -> Record | None: ...
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
    """SQLite-only adapter. SQL/FTS and transaction details stay below this boundary."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        migrate(database_path)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _has_fts(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'"
        ).fetchone()
        return row is not None

    def create_record(self, rec: RecordCreate, *, actor: str) -> Record:
        with self._connect() as conn:
            return self._insert_record(conn, rec, actor=actor)

    def create_record_idempotent(
        self,
        rec: RecordCreate,
        *,
        actor: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Record, bool]:
        operation = "record.create"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay, True
            record = self._insert_record(conn, rec, actor=actor)
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                record=record,
                status_code=201,
            )
            return record, False

    def _insert_record(self, conn: sqlite3.Connection, rec: RecordCreate, *, actor: str) -> Record:
        record = Record(**rec.model_dump(), author_actor=actor)
        now = utc_now()
        record.created_at = now
        record.updated_at = now
        conn.execute(
            """
            INSERT INTO records(
              id, title, content, role, lifecycle, scope_path, entity_type, topic,
              source_refs_json, provenance_json, attrs_json, author_actor,
              superseded_by, created_at, updated_at, write_policy, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                record.write_policy.value,
                record.version,
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
            detail={
                "outcome": "success",
                "role": record.role.value,
                "lifecycle": record.lifecycle.value,
                "write_policy": record.write_policy.value,
            },
        )
        return record

    def update_record_idempotent(
        self,
        record_id: str,
        patch: RecordPatch,
        *,
        actor: str,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Record, bool]:
        operation = f"record.patch:{record_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay, True
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                raise KeyError(record_id)
            current = self._record_from_row(row)
            if current.version != expected_version:
                raise VersionConflictError

            changes = patch.model_dump(exclude_unset=True)
            for field in ("source_refs", "provenance", "attrs"):
                if field in changes:
                    changes[f"{field}_json"] = json.dumps(changes.pop(field), sort_keys=True)
            if "write_policy" in changes:
                changes["write_policy"] = changes["write_policy"].value
            changes["updated_at"] = utc_now()
            changes["version"] = current.version + 1
            assignments = ", ".join(f"{field} = ?" for field in changes)
            values = [*changes.values(), record_id, expected_version]
            cursor = conn.execute(
                f"UPDATE records SET {assignments} WHERE id = ? AND version = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise VersionConflictError
            updated_row = conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            updated = self._record_from_row(updated_row)
            if self._has_fts(conn) and ({"title", "content"} & patch.model_fields_set):
                conn.execute(
                    "UPDATE records_fts SET title = ?, content = ? "
                    "WHERE rowid = (SELECT rowid FROM records WHERE id = ?)",
                    (updated.title, updated.content, record_id),
                )
            self.write_audit(
                conn,
                action="record.patch",
                object_type="record",
                object_id=record_id,
                actor=actor,
                scope_path=updated.scope_path,
                detail={"outcome": "success", "fields": sorted(patch.model_fields_set)},
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                record=updated,
                status_code=200,
            )
            return updated, False

    def promote_record_idempotent(
        self,
        record_id: str,
        *,
        actor: str,
        expected_version: int,
        reason: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Record, bool]:
        operation = f"record.promote:{record_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay, True
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                raise KeyError(record_id)
            current = self._record_from_row(row)
            if current.role.value != "active" or current.lifecycle.value != "working":
                raise RecordStateConflictError
            if current.version != expected_version:
                raise VersionConflictError
            cursor = conn.execute(
                """
                UPDATE records
                SET role = 'canonical', lifecycle = 'live', updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (utc_now(), record_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError
            updated_row = conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            updated = self._record_from_row(updated_row)
            self.write_audit(
                conn,
                action="record.promote",
                object_type="record",
                object_id=record_id,
                actor=actor,
                scope_path=updated.scope_path,
                detail={"outcome": "success", "reason": reason},
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                record=updated,
                status_code=200,
            )
            return updated, False

    def _idempotency_replay(
        self,
        conn: sqlite3.Connection,
        *,
        actor: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> Record | None:
        row = conn.execute(
            "SELECT * FROM idempotency_requests WHERE actor = ? AND idempotency_key = ?",
            (actor, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise IdempotencyConflictError
        return Record.model_validate_json(row["response_json"])

    def _save_idempotency(
        self,
        conn: sqlite3.Connection,
        *,
        actor: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        record: Record,
        status_code: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO idempotency_requests(
              actor, operation, idempotency_key, request_hash, response_json,
              status_code, object_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor,
                operation,
                idempotency_key,
                request_hash,
                record.model_dump_json(),
                status_code,
                record.id,
                utc_now(),
            ),
        )

    def get_record(self, record_id: str) -> Record | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return self._record_from_row(row) if row else None

    def get_visible_record(
        self,
        record_id: str,
        *,
        scope_path: str,
        include_public: bool = False,
    ) -> Record | None:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM records WHERE id = ? AND scope_path IN ({placeholders})",
                [record_id, *scopes],
            ).fetchone()
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
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

    def write_denial(
        self,
        *,
        operation: str,
        actor: str,
        scope_path: str,
        request_id: str,
        status_code: int,
        reason: str | None,
    ) -> None:
        with self._connect() as conn:
            self.write_audit(
                conn,
                action="request.denied",
                object_type="request",
                object_id=request_id,
                actor=actor,
                scope_path=scope_path,
                detail={
                    "outcome": "denied",
                    "operation": operation,
                    "status": status_code,
                    "reason": reason,
                },
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
            ) VALUES (?, ?, ?, ?, ?, ?)
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
            write_policy=row["write_policy"],
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
            version=row["version"],
        )


def probe_sqlite(database_path: Path) -> dict[str, str]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    migrate(database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        result = conn.execute("PRAGMA quick_check").fetchone()
    status = "ok" if result and result[0] == "ok" else "degraded"
    return {"backend": "sqlite", "path": str(database_path), "status": status}
