"""Store port and SQLite adapter for the MemoryV4 governed core."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.migrations import migrate
from app.pagination import decode_cursor, encode_cursor
from app.recovery import recover_interrupted_restore
from app.schemas import (
    Artifact,
    ArtifactCreate,
    AuditEvent,
    Entity,
    EntityContext,
    EntityCreate,
    EntityPatch,
    EntityRef,
    Finding,
    FindingCreate,
    FindingResolutionRequest,
    FindingStatus,
    FindingType,
    Lifecycle,
    ObjectKind,
    ObjectRef,
    Record,
    RecordCreate,
    RecordPatch,
    RecordSupersedeRequest,
    Relation,
    RelationCreate,
    RetrievalEvent,
    Role,
    ScopePath,
    SearchResult,
    SortOrder,
    utc_now,
)
from app.sqlite_runtime import DatabaseLock, assert_integrity, connect_sqlite

ModelT = TypeVar("ModelT", bound=BaseModel)


class IdempotencyConflictError(Exception):
    pass


class VersionConflictError(Exception):
    pass


class RecordStateConflictError(Exception):
    pass


class ObjectConflictError(Exception):
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

    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5000,
        integrity_check: str = "quick",
    ):
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms
        self._lease: DatabaseLock | None = None
        recover_interrupted_restore(database_path)
        lease = DatabaseLock(database_path, exclusive=False).acquire()
        try:
            migrate(
                database_path,
                integrity_check=integrity_check,
                busy_timeout_ms=busy_timeout_ms,
            )
        except Exception:
            lease.close()
            raise
        self._lease = lease

    def close(self) -> None:
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def __del__(self) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(
            self.database_path,
            busy_timeout_ms=self.busy_timeout_ms,
        )

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
                model=Record,
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
                result=record,
                object_id=record.id,
                status_code=201,
            )
            return record, False

    def _insert_record(self, conn: sqlite3.Connection, rec: RecordCreate, *, actor: str) -> Record:
        if rec.entity is not None:
            self._assert_reference_visible(
                conn,
                ObjectRef(
                    kind=ObjectKind.entity,
                    entity_type=rec.entity.entity_type,
                    id=rec.entity.id,
                ),
                rec.scope_path,
            )
        record = Record(**rec.model_dump(), author_actor=actor)
        self._persist_record(conn, record, actor=actor, audit_create=True)
        return record

    def _persist_record(
        self,
        conn: sqlite3.Connection,
        record: Record,
        *,
        actor: str,
        audit_create: bool,
    ) -> None:
        now = utc_now()
        record.created_at = now
        record.updated_at = now
        conn.execute(
            """
            INSERT INTO records(
              id, title, content, role, lifecycle, scope_path, entity_type, entity_id, topic,
              tags_json, confidence, source_refs_json, provenance_json, attrs_json, author_actor,
              supersedes, superseded_by, previous_lifecycle, lifecycle_changed_at, deleted_at,
              created_at, updated_at, write_policy, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.title,
                record.content,
                record.role.value,
                record.lifecycle.value,
                record.scope_path,
                record.entity.entity_type if record.entity else None,
                record.entity.id if record.entity else None,
                record.topic,
                json.dumps(record.tags, sort_keys=True),
                record.confidence,
                json.dumps(record.source_refs, sort_keys=True),
                json.dumps(record.provenance, sort_keys=True),
                json.dumps(record.attrs, sort_keys=True),
                actor,
                record.supersedes,
                record.superseded_by,
                record.previous_lifecycle.value if record.previous_lifecycle else None,
                record.lifecycle_changed_at,
                record.deleted_at,
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
        if audit_create:
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
                model=Record,
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
            if "entity" in changes:
                entity = changes.pop("entity")
                if entity is not None:
                    self._assert_reference_visible(
                        conn,
                        ObjectRef(
                            kind=ObjectKind.entity,
                            entity_type=entity["entity_type"],
                            id=entity["id"],
                        ),
                        current.scope_path,
                    )
                changes["entity_type"] = entity["entity_type"] if entity else None
                changes["entity_id"] = entity["id"] if entity else None
            for field in ("tags", "source_refs", "provenance", "attrs"):
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
                result=updated,
                object_id=updated.id,
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
                model=Record,
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
                result=updated,
                object_id=updated.id,
                status_code=200,
            )
            return updated, False

    def supersede_record_idempotent(
        self,
        record_id: str,
        replacement: RecordSupersedeRequest,
        *,
        actor: str,
        expected_version: int,
        reason: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Record, bool]:
        operation = f"record.supersede:{record_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Record,
            )
            if replay is not None:
                return replay, True
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                raise KeyError(record_id)
            current = self._record_from_row(row)
            if current.version != expected_version:
                raise VersionConflictError
            if current.lifecycle not in {Lifecycle.live, Lifecycle.working}:
                raise RecordStateConflictError

            changes = replacement.model_dump(exclude_unset=True)
            entity = changes.get("entity", current.entity)
            if entity is not None:
                if isinstance(entity, dict):
                    entity = EntityRef.model_validate(entity)
                self._assert_reference_visible(
                    conn,
                    ObjectRef(
                        kind=ObjectKind.entity,
                        entity_type=entity.entity_type,
                        id=entity.id,
                    ),
                    current.scope_path,
                )
            replacement_record = Record(
                title=changes.get("title", current.title),
                content=changes.get("content", current.content),
                role=current.role,
                lifecycle=current.lifecycle,
                write_policy=changes.get("write_policy", current.write_policy),
                scope_path=current.scope_path,
                entity=entity,
                topic=changes.get("topic", current.topic),
                tags=changes.get("tags", current.tags),
                confidence=changes.get("confidence", current.confidence),
                source_refs=changes.get("source_refs", current.source_refs),
                provenance=changes.get("provenance", current.provenance),
                attrs=changes.get("attrs", current.attrs),
                author_actor=actor,
                supersedes=current.id,
            )
            self._persist_record(
                conn,
                replacement_record,
                actor=actor,
                audit_create=False,
            )
            changed_at = utc_now()
            cursor = conn.execute(
                """
                UPDATE records
                SET lifecycle = 'superseded', superseded_by = ?,
                    previous_lifecycle = ?, lifecycle_changed_at = ?, updated_at = ?,
                    version = version + 1
                WHERE id = ? AND version = ?
                """,
                (
                    replacement_record.id,
                    current.lifecycle.value,
                    changed_at,
                    changed_at,
                    record_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError
            self.write_audit(
                conn,
                action="record.supersede",
                object_type="record",
                object_id=record_id,
                actor=actor,
                scope_path=current.scope_path,
                detail={
                    "outcome": "success",
                    "reason": reason,
                    "replacement_id": replacement_record.id,
                    "from_lifecycle": current.lifecycle.value,
                    "to_lifecycle": "superseded",
                },
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=replacement_record,
                object_id=replacement_record.id,
                status_code=200,
            )
            return replacement_record, False

    def transition_record_idempotent(
        self,
        record_id: str,
        target: Lifecycle,
        *,
        actor: str,
        expected_version: int,
        reason: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Record, bool]:
        operation = f"record.transition:{record_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Record,
            )
            if replay is not None:
                return replay, True
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                raise KeyError(record_id)
            current = self._record_from_row(row)
            if current.version != expected_version:
                raise VersionConflictError
            if current.lifecycle == target or target == Lifecycle.superseded:
                raise RecordStateConflictError
            if current.lifecycle == Lifecycle.superseded:
                raise RecordStateConflictError

            nonterminal = {Lifecycle.live, Lifecycle.working}
            terminal = {Lifecycle.archived, Lifecycle.expired}
            restoring = current.lifecycle in terminal
            if restoring:
                if current.previous_lifecycle is None or target != current.previous_lifecycle:
                    raise RecordStateConflictError
                previous_lifecycle = None
            elif current.lifecycle in nonterminal and target in terminal:
                previous_lifecycle = current.lifecycle
            elif current.lifecycle in nonterminal and target in nonterminal:
                previous_lifecycle = None
            else:
                raise RecordStateConflictError
            if current.role == Role.canonical and target == Lifecycle.working:
                raise RecordStateConflictError

            changed_at = utc_now()
            deleted_at = changed_at if target == Lifecycle.archived else None
            cursor = conn.execute(
                """
                UPDATE records
                SET lifecycle = ?, previous_lifecycle = ?, lifecycle_changed_at = ?,
                    deleted_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (
                    target.value,
                    previous_lifecycle.value if previous_lifecycle else None,
                    changed_at,
                    deleted_at,
                    changed_at,
                    record_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError
            updated_row = conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            updated = self._record_from_row(updated_row)
            if target == Lifecycle.archived:
                action = "record.archive"
            elif target == Lifecycle.expired:
                action = "record.expire"
            elif restoring:
                action = "record.restore"
            else:
                action = "record.transition"
            self.write_audit(
                conn,
                action=action,
                object_type="record",
                object_id=record_id,
                actor=actor,
                scope_path=current.scope_path,
                detail={
                    "outcome": "success",
                    "reason": reason,
                    "from_lifecycle": current.lifecycle.value,
                    "to_lifecycle": target.value,
                },
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=updated,
                object_id=updated.id,
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
        model: type[ModelT],
    ) -> ModelT | None:
        row = conn.execute(
            "SELECT * FROM idempotency_requests WHERE actor = ? AND idempotency_key = ?",
            (actor, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise IdempotencyConflictError
        return model.model_validate_json(row["response_json"])

    def _save_idempotency(
        self,
        conn: sqlite3.Connection,
        *,
        actor: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        result: BaseModel,
        object_id: str,
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
                result.model_dump_json(),
                status_code,
                object_id,
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
                f"SELECT * FROM records WHERE id = ? AND deleted_at IS NULL "
                f"AND scope_path IN ({placeholders})",
                [record_id, *scopes],
            ).fetchone()
        return self._record_from_row(row) if row else None

    def list_records(self, *, scope_path: str, include_public: bool = False) -> list[Record]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM records WHERE scope_path IN ({placeholders}) "
                "AND deleted_at IS NULL ORDER BY created_at, id",
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
        self,
        conn: sqlite3.Connection,
        *,
        query: str,
        scopes: list[str],
        limit: int,
        offset: int = 0,
        role: Role | None = None,
        lifecycle: Lifecycle | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        tag: str | None = None,
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"records.scope_path IN ({placeholders})", "records.deleted_at IS NULL"]
        filter_values: list[object] = list(scopes)
        for column, value in (
            ("role", role.value if role else None),
            ("lifecycle", lifecycle.value if lifecycle else None),
            ("entity_type", entity_type),
            ("entity_id", entity_id),
        ):
            if value is not None:
                clauses.append(f"records.{column} = ?")
                filter_values.append(value)
        if tag is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(records.tags_json) WHERE value = ?)"
            )
            filter_values.append(tag)
        filters = " AND ".join(clauses)
        if self._has_fts(conn):
            try:
                return conn.execute(
                    f"""
                    SELECT records.*
                    FROM records_fts
                    JOIN records ON records.rowid = records_fts.rowid
                    WHERE records_fts MATCH ? AND {filters}
                    ORDER BY bm25(records_fts), records.updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [query, *filter_values, limit, offset],
                ).fetchall()
            except sqlite3.OperationalError:
                pass
        like = f"%{query}%"
        return conn.execute(
            f"""
            SELECT records.* FROM records
            WHERE {filters} AND (records.title LIKE ? OR records.content LIKE ?)
            ORDER BY records.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            [*filter_values, like, like, limit, offset],
        ).fetchall()

    def search_records_page(
        self,
        query: str,
        *,
        scope_path: str,
        actor: str,
        include_public: bool,
        role: Role | None,
        lifecycle: Lifecycle | None,
        entity_type: str | None,
        entity_id: str | None,
        tag: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[SearchResult], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        context = {
            "kind": "search",
            "q": query,
            "scope": scope_path,
            "public": include_public,
            "role": role.value if role else None,
            "lifecycle": lifecycle.value if lifecycle else None,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "tag": tag,
        }
        offset = decode_cursor(cursor, context=context)
        with self._connect() as conn:
            rows = self._search_rows(
                conn,
                query=query,
                scopes=scopes,
                limit=limit + 1,
                offset=offset,
                role=role,
                lifecycle=lifecycle,
                entity_type=entity_type,
                entity_id=entity_id,
                tag=tag,
            )
            page_rows = rows[:limit]
            records = [self._record_from_row(row) for row in page_rows]
            self.write_retrieval(
                conn,
                query=query,
                scope_path=scope_path,
                actor=actor,
                result_count=len(records),
                degraded=not self._has_fts(conn),
            )
        results = [
            SearchResult(record=record, score=float(offset + len(records) - index))
            for index, record in enumerate(records)
        ]
        next_cursor = (
            encode_cursor(offset=offset + limit, context=context) if len(rows) > limit else None
        )
        return results, next_cursor

    def create_entity_idempotent(
        self,
        entity: EntityCreate,
        *,
        actor: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Entity, bool]:
        operation = "entity.create"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Entity,
            )
            if replay is not None:
                return replay, True
            created = Entity(**entity.model_dump())
            now = utc_now()
            created.created_at = created.updated_at = now
            try:
                conn.execute(
                    """
                    INSERT INTO entities(
                      id, entity_type, name, scope_path, attrs_json, version,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created.id,
                        created.entity_type,
                        created.name,
                        created.scope_path,
                        json.dumps(created.attrs, sort_keys=True),
                        created.version,
                        created.created_at,
                        created.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ObjectConflictError from exc
            self.write_audit(
                conn,
                action="entity.create",
                object_type="entity",
                object_id=f"{created.entity_type}:{created.id}",
                actor=actor,
                scope_path=created.scope_path,
                detail={"outcome": "success"},
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=created,
                object_id=f"{created.entity_type}:{created.id}",
                status_code=201,
            )
            return created, False

    def get_entity(self, entity_type: str, entity_id: str) -> Entity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND id = ?",
                (entity_type, entity_id),
            ).fetchone()
        return self._entity_from_row(row) if row else None

    def get_visible_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        scope_path: str,
        include_public: bool = False,
    ) -> Entity | None:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM entities
                WHERE entity_type = ? AND id = ? AND scope_path IN ({placeholders})
                """,
                [entity_type, entity_id, *scopes],
            ).fetchone()
        return self._entity_from_row(row) if row else None

    def update_entity_idempotent(
        self,
        entity_type: str,
        entity_id: str,
        patch: EntityPatch,
        *,
        actor: str,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Entity, bool]:
        operation = f"entity.patch:{entity_type}:{entity_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Entity,
            )
            if replay is not None:
                return replay, True
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND id = ?",
                (entity_type, entity_id),
            ).fetchone()
            if row is None:
                raise KeyError(entity_id)
            current = self._entity_from_row(row)
            if current.version != expected_version:
                raise VersionConflictError
            changes = patch.model_dump(exclude_unset=True)
            if "attrs" in changes:
                changes["attrs_json"] = json.dumps(changes.pop("attrs"), sort_keys=True)
            changes["updated_at"] = utc_now()
            changes["version"] = current.version + 1
            assignments = ", ".join(f"{field} = ?" for field in changes)
            cursor = conn.execute(
                f"""
                UPDATE entities SET {assignments}
                WHERE entity_type = ? AND id = ? AND version = ?
                """,
                [*changes.values(), entity_type, entity_id, expected_version],
            )
            if cursor.rowcount != 1:
                raise VersionConflictError
            updated_row = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND id = ?",
                (entity_type, entity_id),
            ).fetchone()
            updated = self._entity_from_row(updated_row)
            self.write_audit(
                conn,
                action="entity.patch",
                object_type="entity",
                object_id=f"{entity_type}:{entity_id}",
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
                result=updated,
                object_id=f"{entity_type}:{entity_id}",
                status_code=200,
            )
            return updated, False

    def page_entities(
        self,
        *,
        scope_path: str,
        include_public: bool,
        entity_type: str | None,
        name_contains: str | None,
        sort: str,
        order: SortOrder,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Entity], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        context = {
            "kind": "entities",
            "scope": scope_path,
            "public": include_public,
            "entity_type": entity_type,
            "name": name_contains,
            "sort": sort,
            "order": order.value,
        }
        offset = decode_cursor(cursor, context=context)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        if entity_type is not None:
            clauses.append("entity_type = ?")
            values.append(entity_type)
        if name_contains is not None:
            clauses.append("name LIKE ?")
            values.append(f"%{name_contains}%")
        sort_column = {
            "name": "name",
            "created_at": "created_at",
            "updated_at": "updated_at",
            "id": "id",
        }[sort]
        direction = "ASC" if order == SortOrder.asc else "DESC"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM entities WHERE {' AND '.join(clauses)}
                ORDER BY {sort_column} {direction}, entity_type {direction}, id {direction}
                LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._entity_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=context) if has_more else None
        return items, next_cursor

    def create_relation_idempotent(
        self,
        relation: RelationCreate,
        *,
        actor: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Relation, bool]:
        operation = "relation.create"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Relation,
            )
            if replay is not None:
                return replay, True
            self._assert_reference_visible(conn, relation.from_ref, relation.scope_path)
            self._assert_reference_visible(conn, relation.to_ref, relation.scope_path)
            created = Relation(**relation.model_dump(by_alias=False), author_actor=actor)
            now = utc_now()
            created.created_at = created.updated_at = now
            conn.execute(
                """
                INSERT INTO relations(
                  id, from_kind, from_entity_type, from_id, to_kind, to_entity_type,
                  to_id, relation_type, scope_path, provenance_json, author_actor,
                  version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created.id,
                    created.from_ref.kind.value,
                    created.from_ref.entity_type,
                    created.from_ref.id,
                    created.to_ref.kind.value,
                    created.to_ref.entity_type,
                    created.to_ref.id,
                    created.relation_type,
                    created.scope_path,
                    json.dumps(created.provenance, sort_keys=True),
                    actor,
                    created.version,
                    created.created_at,
                    created.updated_at,
                ),
            )
            self.write_audit(
                conn,
                action="relation.create",
                object_type="relation",
                object_id=created.id,
                actor=actor,
                scope_path=created.scope_path,
                detail={"outcome": "success", "relation_type": created.relation_type},
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=created,
                object_id=created.id,
                status_code=201,
            )
            return created, False

    def page_relations(
        self,
        *,
        scope_path: str,
        include_public: bool,
        relation_type: str | None,
        from_kind: ObjectKind | None,
        from_id: str | None,
        to_kind: ObjectKind | None,
        to_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Relation], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        context = {
            "kind": "relations",
            "scope": scope_path,
            "public": include_public,
            "type": relation_type,
            "from_kind": from_kind.value if from_kind else None,
            "from_id": from_id,
            "to_kind": to_kind.value if to_kind else None,
            "to_id": to_id,
        }
        offset = decode_cursor(cursor, context=context)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        for column, value in (
            ("relation_type", relation_type),
            ("from_kind", from_kind.value if from_kind else None),
            ("from_id", from_id),
            ("to_kind", to_kind.value if to_kind else None),
            ("to_id", to_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM relations WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._relation_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=context) if has_more else None
        return items, next_cursor

    def create_artifact_idempotent(
        self,
        artifact: ArtifactCreate,
        *,
        actor: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Artifact, bool]:
        operation = "artifact.create"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Artifact,
            )
            if replay is not None:
                return replay, True
            if artifact.record_id is not None:
                self._assert_reference_visible(
                    conn,
                    ObjectRef(kind=ObjectKind.record, id=artifact.record_id),
                    artifact.scope_path,
                )
            if artifact.entity is not None:
                self._assert_reference_visible(
                    conn,
                    ObjectRef(
                        kind=ObjectKind.entity,
                        entity_type=artifact.entity.entity_type,
                        id=artifact.entity.id,
                    ),
                    artifact.scope_path,
                )
            created = Artifact(**artifact.model_dump(), author_actor=actor)
            now = utc_now()
            created.created_at = created.updated_at = now
            conn.execute(
                """
                INSERT INTO artifacts(
                  id, record_id, entity_type, entity_id, artifact_type, uri, checksum,
                  scope_path, provenance_json, author_actor, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created.id,
                    created.record_id,
                    created.entity.entity_type if created.entity else None,
                    created.entity.id if created.entity else None,
                    created.artifact_type,
                    created.uri,
                    created.checksum,
                    created.scope_path,
                    json.dumps(created.provenance, sort_keys=True),
                    actor,
                    created.version,
                    created.created_at,
                    created.updated_at,
                ),
            )
            self.write_audit(
                conn,
                action="artifact.create",
                object_type="artifact",
                object_id=created.id,
                actor=actor,
                scope_path=created.scope_path,
                detail={"outcome": "success", "artifact_type": created.artifact_type},
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=created,
                object_id=created.id,
                status_code=201,
            )
            return created, False

    def page_artifacts(
        self,
        *,
        scope_path: str,
        include_public: bool,
        artifact_type: str | None,
        record_id: str | None,
        entity_type: str | None,
        entity_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Artifact], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        context = {
            "kind": "artifacts",
            "scope": scope_path,
            "public": include_public,
            "type": artifact_type,
            "record": record_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
        }
        offset = decode_cursor(cursor, context=context)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        for column, value in (
            ("artifact_type", artifact_type),
            ("record_id", record_id),
            ("entity_type", entity_type),
            ("entity_id", entity_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM artifacts WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._artifact_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=context) if has_more else None
        return items, next_cursor

    def page_records(
        self,
        *,
        scope_path: str,
        include_public: bool,
        role: Role | None,
        lifecycle: Lifecycle | None,
        entity_type: str | None,
        entity_id: str | None,
        topic: str | None,
        tag: str | None,
        min_confidence: float | None,
        include_deleted: bool,
        sort: str,
        order: SortOrder,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Record], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        context = {
            "kind": "records",
            "scope": scope_path,
            "public": include_public,
            "role": role.value if role else None,
            "lifecycle": lifecycle.value if lifecycle else None,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "topic": topic,
            "tag": tag,
            "confidence": min_confidence,
            "deleted": include_deleted,
            "sort": sort,
            "order": order.value,
        }
        offset = decode_cursor(cursor, context=context)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        for column, value in (
            ("role", role.value if role else None),
            ("lifecycle", lifecycle.value if lifecycle else None),
            ("entity_type", entity_type),
            ("entity_id", entity_id),
            ("topic", topic),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if tag is not None:
            clauses.append("EXISTS (SELECT 1 FROM json_each(records.tags_json) WHERE value = ?)")
            values.append(tag)
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            values.append(min_confidence)
        sort_column = {
            "title": "title",
            "created_at": "created_at",
            "updated_at": "updated_at",
            "confidence": "confidence",
            "id": "id",
        }[sort]
        direction = "ASC" if order == SortOrder.asc else "DESC"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM records WHERE {' AND '.join(clauses)}
                ORDER BY {sort_column} {direction}, id {direction}
                LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._record_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=context) if has_more else None
        return items, next_cursor

    def get_entity_context(
        self,
        entity_type: str,
        entity_id: str,
        *,
        scope_path: str,
        include_public: bool,
        limit: int,
    ) -> EntityContext | None:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as conn:
            entity_row = conn.execute(
                f"""
                SELECT * FROM entities WHERE entity_type = ? AND id = ?
                AND scope_path IN ({placeholders})
                """,
                [entity_type, entity_id, *scopes],
            ).fetchone()
            if entity_row is None:
                return None
            record_rows = conn.execute(
                f"""
                SELECT * FROM records WHERE entity_type = ? AND entity_id = ?
                AND deleted_at IS NULL AND scope_path IN ({placeholders})
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                [entity_type, entity_id, *scopes, limit + 1],
            ).fetchall()
            relation_rows = conn.execute(
                f"""
                SELECT * FROM relations WHERE scope_path IN ({placeholders}) AND (
                  (from_kind = 'entity' AND from_entity_type = ? AND from_id = ?) OR
                  (to_kind = 'entity' AND to_entity_type = ? AND to_id = ?)
                ) ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                [*scopes, entity_type, entity_id, entity_type, entity_id, limit + 1],
            ).fetchall()
            record_ids = [row["id"] for row in record_rows[:limit]]
            artifact_clauses = ["(entity_type = ? AND entity_id = ?)"]
            artifact_values: list[object] = [entity_type, entity_id]
            if record_ids:
                record_placeholders = ",".join("?" for _ in record_ids)
                artifact_clauses.append(f"record_id IN ({record_placeholders})")
                artifact_values.extend(record_ids)
            artifact_rows = conn.execute(
                f"""
                SELECT * FROM artifacts WHERE scope_path IN ({placeholders})
                AND ({' OR '.join(artifact_clauses)})
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                [*scopes, *artifact_values, limit + 1],
            ).fetchall()
        truncated = any(len(rows) > limit for rows in (record_rows, relation_rows, artifact_rows))
        return EntityContext(
            entity=self._entity_from_row(entity_row),
            records=[self._record_from_row(row) for row in record_rows[:limit]],
            relations=[self._relation_from_row(row) for row in relation_rows[:limit]],
            artifacts=[self._artifact_from_row(row) for row in artifact_rows[:limit]],
            truncated=truncated,
        )

    def create_finding(self, finding: FindingCreate, *, actor: str) -> Finding:
        """Persist a worker-produced review finding below the public API boundary."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_review_subject_visible(conn, finding.subject, finding.scope_path)
            created = Finding(**finding.model_dump(), created_by_actor=actor)
            now = utc_now()
            created.created_at = created.updated_at = now
            conn.execute(
                """
                INSERT INTO review_findings(
                  id, finding_type, status, subject_kind, subject_entity_type, subject_id,
                  detail_json, resolution_json, scope_path, created_by_actor,
                  resolved_by_actor, resolved_at, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, 1, ?, ?)
                """,
                (
                    created.id,
                    created.finding_type.value,
                    created.status.value,
                    created.subject.kind.value,
                    created.subject.entity_type,
                    created.subject.id,
                    json.dumps(created.detail, sort_keys=True),
                    created.scope_path,
                    actor,
                    now,
                    now,
                ),
            )
            self.write_audit(
                conn,
                action="finding.create",
                object_type="finding",
                object_id=created.id,
                actor=actor,
                scope_path=created.scope_path,
                detail={"outcome": "success", "finding_type": created.finding_type.value},
            )
            return created

    def get_finding(self, finding_id: str) -> Finding | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM review_findings WHERE id = ?", (finding_id,)
            ).fetchone()
        return self._finding_from_row(row) if row else None

    def page_findings(
        self,
        *,
        scope_path: str,
        include_public: bool,
        finding_type: FindingType | None,
        status: FindingStatus | None,
        subject_kind: ObjectKind | None,
        subject_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Finding], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        context = {
            "kind": "findings",
            "scope": scope_path,
            "public": include_public,
            "finding_type": finding_type.value if finding_type else None,
            "status": status.value if status else None,
            "subject_kind": subject_kind.value if subject_kind else None,
            "subject_id": subject_id,
        }
        offset = decode_cursor(cursor, context=context)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        for column, value in (
            ("finding_type", finding_type.value if finding_type else None),
            ("status", status.value if status else None),
            ("subject_kind", subject_kind.value if subject_kind else None),
            ("subject_id", subject_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM review_findings WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._finding_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=context) if has_more else None
        return items, next_cursor

    def resolve_finding_idempotent(
        self,
        finding_id: str,
        resolution: FindingResolutionRequest,
        *,
        actor: str,
        expected_version: int,
        reason: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Finding, bool]:
        operation = f"finding.resolve:{finding_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._idempotency_replay(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=Finding,
            )
            if replay is not None:
                return replay, True
            row = conn.execute(
                "SELECT * FROM review_findings WHERE id = ?", (finding_id,)
            ).fetchone()
            if row is None:
                raise KeyError(finding_id)
            current = self._finding_from_row(row)
            if current.version != expected_version:
                raise VersionConflictError(finding_id)
            if current.status != FindingStatus.open:
                raise RecordStateConflictError(finding_id)
            now = utc_now()
            cursor = conn.execute(
                """
                UPDATE review_findings
                SET status = ?, resolution_json = ?, resolved_by_actor = ?, resolved_at = ?,
                    updated_at = ?, version = version + 1
                WHERE id = ? AND version = ? AND status = 'open'
                """,
                (
                    resolution.status.value,
                    json.dumps(resolution.resolution, sort_keys=True),
                    actor,
                    now,
                    now,
                    finding_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError(finding_id)
            updated_row = conn.execute(
                "SELECT * FROM review_findings WHERE id = ?", (finding_id,)
            ).fetchone()
            if updated_row is None:
                raise KeyError(finding_id)
            updated = self._finding_from_row(updated_row)
            action = (
                "finding.resolve"
                if resolution.status == FindingStatus.resolved
                else "finding.dismiss"
            )
            self.write_audit(
                conn,
                action=action,
                object_type="finding",
                object_id=finding_id,
                actor=actor,
                scope_path=current.scope_path,
                detail={
                    "outcome": "success",
                    "reason": reason,
                    "status": resolution.status.value,
                    "source_version": expected_version,
                },
            )
            self._save_idempotency(
                conn,
                actor=actor,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=updated,
                object_id=finding_id,
                status_code=200,
            )
            return updated, False

    def page_audit_events(
        self,
        *,
        scope_path: str,
        include_public: bool,
        action: str | None,
        object_type: str | None,
        object_id: str | None,
        actor: str | None,
        from_time: str | None,
        to_time: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[AuditEvent], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        filters = {
            "kind": "audit_events",
            "scope": scope_path,
            "public": include_public,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "actor": actor,
            "from": from_time,
            "to": to_time,
        }
        offset = decode_cursor(cursor, context=filters)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        for column, value in (
            ("action", action),
            ("object_type", object_type),
            ("object_id", object_id),
            ("actor", actor),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        self._add_time_clauses(clauses, values, from_time, to_time)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM audit_events WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._audit_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=filters) if has_more else None
        return items, next_cursor

    def page_retrieval_events(
        self,
        *,
        scope_path: str,
        include_public: bool,
        actor: str | None,
        degraded: bool | None,
        query_contains: str | None,
        from_time: str | None,
        to_time: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[RetrievalEvent], str | None]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        filters = {
            "kind": "retrieval_events",
            "scope": scope_path,
            "public": include_public,
            "actor": actor,
            "degraded": degraded,
            "query_contains": query_contains,
            "from": from_time,
            "to": to_time,
        }
        offset = decode_cursor(cursor, context=filters)
        placeholders = ",".join("?" for _ in scopes)
        clauses = [f"scope_path IN ({placeholders})"]
        values: list[object] = list(scopes)
        if actor is not None:
            clauses.append("actor = ?")
            values.append(actor)
        if degraded is not None:
            clauses.append("degraded = ?")
            values.append(int(degraded))
        if query_contains is not None:
            clauses.append("query LIKE ? ESCAPE '\\'")
            escaped = query_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append(f"%{escaped}%")
        self._add_time_clauses(clauses, values, from_time, to_time)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM retrieval_events WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [*values, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._retrieval_from_row(row) for row in rows[:limit]]
        next_cursor = encode_cursor(offset=offset + limit, context=filters) if has_more else None
        return items, next_cursor

    def usage_summary(
        self,
        *,
        scope_path: str,
        include_public: bool,
        from_time: str | None,
        to_time: str | None,
    ) -> tuple[dict[str, object], dict[str, int]]:
        scopes = ScopePath.ancestors(scope_path, include_public=include_public)
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as conn:
            def count(table: str, extra: str = "", values: list[object] | None = None) -> int:
                sql = f"SELECT count(*) FROM {table} WHERE scope_path IN ({placeholders})"
                if extra:
                    sql += f" AND {extra}"
                return int(conn.execute(sql, [*scopes, *(values or [])]).fetchone()[0])

            def grouped(table: str, column: str) -> dict[str, int]:
                rows = conn.execute(
                    f"SELECT {column}, count(*) AS total FROM {table} "
                    f"WHERE scope_path IN ({placeholders}) GROUP BY {column}",
                    scopes,
                ).fetchall()
                return {str(row[0]): int(row[1]) for row in rows}

            objects: dict[str, object] = {
                "entities": {"total": count("entities")},
                "records": {
                    "total": count("records"),
                    "soft_deleted": count("records", "deleted_at IS NOT NULL"),
                    "by_role": grouped("records", "role"),
                    "by_lifecycle": grouped("records", "lifecycle"),
                },
                "relations": {"total": count("relations")},
                "artifacts": {"total": count("artifacts")},
                "findings": {
                    "total": count("review_findings"),
                    "by_type": grouped("review_findings", "finding_type"),
                    "by_status": grouped("review_findings", "status"),
                },
            }
            event_clauses = [f"scope_path IN ({placeholders})"]
            event_values: list[object] = list(scopes)
            self._add_time_clauses(event_clauses, event_values, from_time, to_time)
            where = " AND ".join(event_clauses)
            audit_total = int(
                conn.execute(f"SELECT count(*) FROM audit_events WHERE {where}", event_values)
                .fetchone()[0]
            )
            retrieval_total = int(
                conn.execute(
                    f"SELECT count(*) FROM retrieval_events WHERE {where}", event_values
                ).fetchone()[0]
            )
            degraded_total = int(
                conn.execute(
                    f"SELECT count(*) FROM retrieval_events WHERE {where} AND degraded = 1",
                    event_values,
                ).fetchone()[0]
            )
        return objects, {
            "audit_events": audit_total,
            "retrieval_events": retrieval_total,
            "degraded_retrievals": degraded_total,
        }

    @staticmethod
    def _add_time_clauses(
        clauses: list[str], values: list[object], from_time: str | None, to_time: str | None
    ) -> None:
        if from_time is not None:
            clauses.append("created_at >= ?")
            values.append(from_time)
        if to_time is not None:
            clauses.append("created_at <= ?")
            values.append(to_time)

    def _assert_review_subject_visible(
        self, conn: sqlite3.Connection, ref: ObjectRef, scope_path: str
    ) -> None:
        if ref.kind == ObjectKind.record:
            row = conn.execute(
                "SELECT scope_path FROM records WHERE id = ?", (ref.id,)
            ).fetchone()
            if row is None or row["scope_path"] not in ScopePath.ancestors(scope_path):
                raise KeyError(ref.id)
            return
        self._assert_reference_visible(conn, ref, scope_path)

    def reference_visible(self, ref: ObjectRef, *, scope_path: str) -> bool:
        with self._connect() as conn:
            try:
                self._assert_reference_visible(conn, ref, scope_path)
            except KeyError:
                return False
        return True

    def _assert_reference_visible(
        self, conn: sqlite3.Connection, ref: ObjectRef, scope_path: str
    ) -> None:
        if ref.kind == ObjectKind.entity:
            row = conn.execute(
                "SELECT scope_path FROM entities WHERE entity_type = ? AND id = ?",
                (ref.entity_type, ref.id),
            ).fetchone()
        elif ref.kind == ObjectKind.record:
            row = conn.execute(
                "SELECT scope_path FROM records WHERE id = ? AND deleted_at IS NULL", (ref.id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT scope_path FROM artifacts WHERE id = ?", (ref.id,)
            ).fetchone()
        if row is None or row["scope_path"] not in ScopePath.ancestors(scope_path):
            raise KeyError(ref.id)

    def _entity_from_row(self, row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"],
            entity_type=row["entity_type"],
            name=row["name"],
            scope_path=row["scope_path"],
            attrs=json.loads(row["attrs_json"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _relation_from_row(self, row: sqlite3.Row) -> Relation:
        return Relation(
            id=row["id"],
            from_ref=ObjectRef(
                kind=row["from_kind"],
                entity_type=row["from_entity_type"],
                id=row["from_id"],
            ),
            to_ref=ObjectRef(
                kind=row["to_kind"], entity_type=row["to_entity_type"], id=row["to_id"]
            ),
            relation_type=row["relation_type"],
            scope_path=row["scope_path"],
            provenance=json.loads(row["provenance_json"]),
            author_actor=row["author_actor"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _artifact_from_row(self, row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=row["id"],
            record_id=row["record_id"],
            entity=(
                EntityRef(entity_type=row["entity_type"], id=row["entity_id"])
                if row["entity_type"] is not None and row["entity_id"] is not None
                else None
            ),
            artifact_type=row["artifact_type"],
            uri=row["uri"],
            checksum=row["checksum"],
            scope_path=row["scope_path"],
            provenance=json.loads(row["provenance_json"]),
            author_actor=row["author_actor"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _finding_from_row(self, row: sqlite3.Row) -> Finding:
        return Finding(
            id=row["id"],
            finding_type=row["finding_type"],
            status=row["status"],
            subject=ObjectRef(
                kind=row["subject_kind"],
                entity_type=row["subject_entity_type"],
                id=row["subject_id"],
            ),
            detail=json.loads(row["detail_json"]),
            resolution=json.loads(row["resolution_json"]) if row["resolution_json"] else None,
            scope_path=row["scope_path"],
            created_by_actor=row["created_by_actor"],
            resolved_by_actor=row["resolved_by_actor"],
            resolved_at=row["resolved_at"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            action=row["action"],
            object_type=row["object_type"],
            object_id=row["object_id"],
            actor=row["actor"],
            scope_path=row["scope_path"],
            detail=json.loads(row["detail_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _retrieval_from_row(row: sqlite3.Row) -> RetrievalEvent:
        return RetrievalEvent(
            id=row["id"],
            query=row["query"],
            scope_path=row["scope_path"],
            actor=row["actor"],
            result_count=row["result_count"],
            degraded=bool(row["degraded"]),
            created_at=row["created_at"],
        )

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
            entity=(
                EntityRef(entity_type=row["entity_type"], id=row["entity_id"])
                if row["entity_type"] is not None and row["entity_id"] is not None
                else None
            ),
            topic=row["topic"],
            tags=json.loads(row["tags_json"]),
            confidence=row["confidence"],
            source_refs=json.loads(row["source_refs_json"]),
            provenance=json.loads(row["provenance_json"]),
            attrs=json.loads(row["attrs_json"]),
            author_actor=row["author_actor"],
            supersedes=row["supersedes"],
            superseded_by=row["superseded_by"],
            previous_lifecycle=row["previous_lifecycle"],
            lifecycle_changed_at=row["lifecycle_changed_at"],
            deleted_at=row["deleted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )


def probe_sqlite(database_path: Path, *, busy_timeout_ms: int = 5000) -> dict[str, str]:
    try:
        with connect_sqlite(
            database_path,
            busy_timeout_ms=busy_timeout_ms,
            readonly=True,
        ) as conn:
            assert_integrity(conn, full=False)
    except (sqlite3.DatabaseError, OSError, RuntimeError):
        return {"backend": "sqlite", "path": str(database_path), "status": "degraded"}
    return {"backend": "sqlite", "path": str(database_path), "status": "ok"}
