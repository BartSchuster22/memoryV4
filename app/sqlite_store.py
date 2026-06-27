"""SQLite implementation of the MemoryV4 Store port."""

from __future__ import annotations

from dataclasses import replace
import json
import re
import sqlite3
from typing import Any

from app.migrations import migrate_up
from app.schemas import (
    AuditEvent,
    Lifecycle,
    MemoryRecord,
    MemoryRole,
    RankedRecord,
    RetrievalEvent,
    SourceRef,
    Supersession,
    validate_lifecycle_transition,
)


class SqliteStore:
    """SQLite-only Store adapter for the C1 governance spine."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma foreign_keys = on")
        migrate_up(self.conn)

    @classmethod
    def in_memory(cls) -> "SqliteStore":
        return cls(sqlite3.connect(":memory:"))

    def create_record(self, record: MemoryRecord, *, actor: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into records(
                    id, role, lifecycle, scope, content, source_refs_json,
                    author_actor, write_policy, superseded_by, metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_values(record),
            )
            self._replace_source_refs(record)
            self._refresh_lexical_terms(record)
            self._audit("record.create", actor=actor, record_id=record.id)

    def get_record(self, record_id: str) -> MemoryRecord:
        row = self.conn.execute("select * from records where id = ?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return _record_from_row(row)

    def update_record(self, record: MemoryRecord, *, actor: str) -> None:
        self.get_record(record.id)
        with self.conn:
            self.conn.execute(
                """
                update records
                set role = ?, lifecycle = ?, scope = ?, content = ?,
                    source_refs_json = ?, author_actor = ?, write_policy = ?,
                    superseded_by = ?, metadata_json = ?, updated_at = current_timestamp
                where id = ?
                """,
                (
                    record.role.value,
                    record.lifecycle.value,
                    record.scope,
                    record.content,
                    _json([ref_to_dict(ref) for ref in record.source_refs]),
                    record.author_actor,
                    record.write_policy,
                    record.superseded_by,
                    _json(record.metadata),
                    record.id,
                ),
            )
            self._replace_source_refs(record)
            self._refresh_lexical_terms(record)
            self._audit("record.update", actor=actor, record_id=record.id)

    def transition_lifecycle(self, record_id: str, target: Lifecycle, *, actor: str) -> None:
        record = self.get_record(record_id)
        validate_lifecycle_transition(record.lifecycle, target)
        if record.lifecycle == target:
            return
        updated = replace(record, lifecycle=target)
        with self.conn:
            self.conn.execute(
                "update records set lifecycle = ?, updated_at = current_timestamp where id = ?",
                (target.value, record_id),
            )
            self._audit(
                "record.lifecycle_transition",
                actor=actor,
                record_id=record_id,
                details={"from": record.lifecycle.value, "to": updated.lifecycle.value},
            )

    def supersede_record(self, old_record_id: str, new_record_id: str, *, actor: str, reason: str) -> None:
        if old_record_id == new_record_id:
            raise ValueError("A record cannot supersede itself")
        old = self.get_record(old_record_id)
        self.get_record(new_record_id)
        validate_lifecycle_transition(old.lifecycle, Lifecycle.SUPERSEDED)
        with self.conn:
            self.conn.execute(
                """
                insert into supersessions(old_record_id, new_record_id, actor, reason)
                values (?, ?, ?, ?)
                """,
                (old_record_id, new_record_id, actor, reason),
            )
            self.conn.execute(
                """
                update records
                set lifecycle = ?, superseded_by = ?, updated_at = current_timestamp
                where id = ?
                """,
                (Lifecycle.SUPERSEDED.value, new_record_id, old_record_id),
            )
            self._audit(
                "record.supersede",
                actor=actor,
                record_id=old_record_id,
                details={"new_record_id": new_record_id, "reason": reason},
            )

    def lexical_rank(self, query: str, *, scope: str | None, limit: int) -> list[RankedRecord]:
        terms = _terms(query)
        if not terms:
            return []
        params: list[Any] = terms[:]
        scope_clause = ""
        if scope is not None:
            scope_clause = " and r.scope = ?"
            params.append(scope)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            select r.id, sum(t.frequency) as score
            from lexical_terms t
            join records r on r.id = t.record_id
            where t.term in ({','.join('?' for _ in terms)}){scope_clause}
            group by r.id
            order by score desc, r.id asc
            limit ?
            """,
            params,
        ).fetchall()
        self._retrieval_event(query, lane="lexical", scope=scope, degraded=False)
        return [RankedRecord(record_id=row["id"], score=float(row["score"]), lane="lexical") for row in rows]

    def vector_rank(self, embedding: list[float], *, scope: str | None, limit: int) -> list[RankedRecord]:
        """Placeholder for future sqlite-vec lane.

        C1 intentionally exposes degraded behavior instead of silently pretending
        vector search exists before C2 wires sqlite-vec/embeddings.
        """

        self._retrieval_event(
            "<embedding>",
            lane="vector",
            scope=scope,
            degraded=True,
            details={"reason": "vector index not configured in C1", "dimensions": len(embedding), "limit": limit},
        )
        return []

    def list_audit_events(self, *, record_id: str | None = None) -> list[AuditEvent]:
        params: tuple[Any, ...] = ()
        where = ""
        if record_id is not None:
            where = " where record_id = ?"
            params = (record_id,)
        rows = self.conn.execute(
            f"select * from audit_events{where} order by id", params
        ).fetchall()
        return [
            AuditEvent(
                id=row["id"],
                action=row["action"],
                actor=row["actor"],
                record_id=row["record_id"],
                details=json.loads(row["details_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_retrieval_events(self) -> list[RetrievalEvent]:
        rows = self.conn.execute("select * from retrieval_events order by id").fetchall()
        return [
            RetrievalEvent(
                id=row["id"],
                query=row["query"],
                lane=row["lane"],
                scope=row["scope"],
                degraded=bool(row["degraded"]),
                details=json.loads(row["details_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_supersessions(self) -> list[Supersession]:
        rows = self.conn.execute("select * from supersessions order by created_at, old_record_id").fetchall()
        return [
            Supersession(
                old_record_id=row["old_record_id"],
                new_record_id=row["new_record_id"],
                actor=row["actor"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def assert_supersession_integrity(self) -> None:
        dangling = self.conn.execute(
            """
            select s.old_record_id
            from supersessions s
            left join records old on old.id = s.old_record_id
            left join records new on new.id = s.new_record_id
            where old.id is null or new.id is null
               or old.lifecycle <> 'superseded'
               or old.superseded_by <> s.new_record_id
               or s.old_record_id = s.new_record_id
            """
        ).fetchall()
        if dangling:
            ids = ", ".join(row["old_record_id"] for row in dangling)
            raise ValueError(f"Invalid supersession rows: {ids}")
        return None

    def _audit(self, action: str, *, actor: str, record_id: str | None, details: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "insert into audit_events(action, actor, record_id, details_json) values (?, ?, ?, ?)",
            (action, actor, record_id, _json(details or {})),
        )

    def _retrieval_event(
        self,
        query: str,
        *,
        lane: str,
        scope: str | None,
        degraded: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into retrieval_events(query, lane, scope, degraded, details_json)
                values (?, ?, ?, ?, ?)
                """,
                (query, lane, scope, int(degraded), _json(details or {})),
            )

    def _replace_source_refs(self, record: MemoryRecord) -> None:
        self.conn.execute("delete from source_refs where record_id = ?", (record.id,))
        self.conn.executemany(
            """
            insert into source_refs(record_id, kind, uri, fragment, metadata_json)
            values (?, ?, ?, ?, ?)
            """,
            [
                (record.id, ref.kind, ref.uri, ref.fragment, _json(ref.metadata))
                for ref in record.source_refs
            ],
        )

    def _refresh_lexical_terms(self, record: MemoryRecord) -> None:
        self.conn.execute("delete from lexical_terms where record_id = ?", (record.id,))
        counts: dict[str, int] = {}
        for term in _terms(record.content):
            counts[term] = counts.get(term, 0) + 1
        self.conn.executemany(
            "insert into lexical_terms(record_id, term, frequency) values (?, ?, ?)",
            [(record.id, term, frequency) for term, frequency in counts.items()],
        )


def _record_values(record: MemoryRecord) -> tuple[Any, ...]:
    return (
        record.id,
        record.role.value,
        record.lifecycle.value,
        record.scope,
        record.content,
        _json([ref_to_dict(ref) for ref in record.source_refs]),
        record.author_actor,
        record.write_policy,
        record.superseded_by,
        _json(record.metadata),
    )


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        role=MemoryRole(row["role"]),
        lifecycle=Lifecycle(row["lifecycle"]),
        scope=row["scope"],
        content=row["content"],
        source_refs=[source_ref_from_dict(item) for item in json.loads(row["source_refs_json"])],
        author_actor=row["author_actor"],
        write_policy=row["write_policy"],
        superseded_by=row["superseded_by"],
        metadata=json.loads(row["metadata_json"]),
    )


def ref_to_dict(ref: SourceRef) -> dict[str, Any]:
    return {"kind": ref.kind, "uri": ref.uri, "fragment": ref.fragment, "metadata": ref.metadata}


def source_ref_from_dict(data: dict[str, Any]) -> SourceRef:
    return SourceRef(
        kind=data["kind"],
        uri=data["uri"],
        fragment=data.get("fragment"),
        metadata=data.get("metadata", {}),
    )


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())
