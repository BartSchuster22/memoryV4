"""SQLite implementation of the MemoryV4 Store port."""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.embeddings import EmbeddingProvider, EmbeddingUnavailable
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
                       author_actor, write_policy_json, scope_path, source_refs_json,
                       attrs_json, superseded_by
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
        terms = _tokenize(query)
        if not terms or k <= 0:
            return []
        clauses, params = _filter_sql(f)
        sql = """
            SELECT id, title, topic, content
            FROM records
            WHERE """ + " AND ".join(clauses) + " ORDER BY updated_at DESC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        docs = [(row["id"], _tokenize(" ".join([row["title"], row["topic"], row["content"]]))) for row in rows]
        ranked = _bm25_rank(terms, docs)
        return [rid for rid, _ in ranked[:k]]

    def vector_rank(self, qvec: Sequence[float], f: Filter, k: int) -> list[str]:
        if not qvec or k <= 0:
            return []
        clauses, params = _filter_sql(f)
        sql = """
            SELECT records.id, record_embeddings.embedding
            FROM records
            JOIN record_embeddings ON record_embeddings.record_id = records.id
            WHERE """ + " AND ".join(clauses)
        ranked: list[tuple[float, str]] = []
        with self._connect() as conn:
            for row in conn.execute(sql, params).fetchall():
                score = _cosine_similarity(qvec, _unpack_vector(row["embedding"]))
                if score > 0.0:
                    ranked.append((score, row["id"]))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [rid for _, rid in ranked[:k]]

    def backfill_embeddings(self, provider: EmbeddingProvider, f: Filter | None = None) -> int:
        """Embed records matching the optional filter that do not yet have a vector row."""
        filter_clauses, filter_params = _filter_sql(f or Filter())
        sql = """
            SELECT records.id, records.title, records.topic, records.content
            FROM records
            LEFT JOIN record_embeddings ON record_embeddings.record_id = records.id
            WHERE record_embeddings.record_id IS NULL AND """ + " AND ".join(filter_clauses) + " ORDER BY records.id"
        with self._connect() as conn:
            rows = conn.execute(sql, filter_params).fetchall()
            written = 0
            for row in rows:
                text = _record_embedding_text(row["title"], row["topic"], row["content"])
                try:
                    vector = provider.embed(text)
                except EmbeddingUnavailable:
                    continue
                conn.execute(
                    """
                    INSERT INTO record_embeddings(record_id, embedding, dimensions, model, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(record_id) DO UPDATE SET
                        embedding = excluded.embedding,
                        dimensions = excluded.dimensions,
                        model = excluded.model,
                        updated_at = excluded.updated_at
                    """,
                    (row["id"], _pack_vector(vector), len(vector), provider.model, _now()),
                )
                written += 1
        return written

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
                author_actor, write_policy_json, scope_path, source_refs_json,
                attrs_json, superseded_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.id, rec.entity_id, rec.entity_type, rec.title, rec.topic, rec.content,
                rec.role.value, rec.lifecycle.value, rec.author_actor,
                json.dumps(rec.write_policy, sort_keys=True), rec.scope_path,
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
        scope_clauses: list[str] = []
        for scope in f.scope_prefixes:
            scope_clauses.append("(scope_path = ? OR scope_path LIKE ?)")
            params.extend([scope, f"{scope}/%"])
        clauses.append("(" + " OR ".join(scope_clauses) + ")")
    return clauses, params


def _record_embedding_text(title: str, topic: str, content: str) -> str:
    return f"{title}\n{topic}\n{content}"


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for char in text.casefold():
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _bm25_rank(query_terms: list[str], docs: list[tuple[str, list[str]]]) -> list[tuple[str, float]]:
    if not docs:
        return []
    avgdl = sum(len(tokens) for _, tokens in docs) / len(docs)
    avgdl = avgdl or 1.0
    document_frequency = Counter({term: 0 for term in set(query_terms)})
    for _, tokens in docs:
        token_set = set(tokens)
        for term in document_frequency:
            if term in token_set:
                document_frequency[term] += 1
    k1 = 1.5
    b = 0.75
    ranked: list[tuple[str, float]] = []
    for record_id, tokens in docs:
        counts = Counter(tokens)
        length = len(tokens) or 1
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            df = document_frequency.get(term, 0)
            idf = math.log(1.0 + (len(docs) - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1.0 - b + b * length / avgdl)
            score += idf * (tf * (k1 + 1.0)) / denom
        if score > 0.0:
            ranked.append((record_id, score))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"!{len(vector)}d", *vector)


def _unpack_vector(blob: bytes) -> list[float]:
    if len(blob) % 8 != 0:
        return []
    return list(struct.unpack(f"!{len(blob) // 8}d", blob))


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _record_from_row(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"], entity_id=row["entity_id"], entity_type=row["entity_type"], title=row["title"], topic=row["topic"],
        content=row["content"], role=Role(row["role"]), lifecycle=Lifecycle(row["lifecycle"]),
        author_actor=row["author_actor"], write_policy=json.loads(row["write_policy_json"]),
        scope_path=row["scope_path"], source_refs=json.loads(row["source_refs_json"]),
        attrs=json.loads(row["attrs_json"]), superseded_by=row["superseded_by"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
