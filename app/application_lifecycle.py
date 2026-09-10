"""Bounded Stage3 verified-extract-v1 logical erasure, not generic deletion.

create_app registers the route; every SqliteStore installs replay/creation guards.
Only hash-only operation/receipt tombstones survive logical scrub. Never expire
these tombstones independently or run older writers against a scrubbed database.

POST /applications/knowledge/scrub with existing auth/delegated actor headers,
X-MemoryV4-Reason, JSON scope_path/application_id/receipt_id/subject; no record IDs.
Parent derives exact scope from server-side registration/receipt, not model input.
One receipt per transaction. Cross-receipt corrections require owner coordination,
not silently deleting other receipts. Actual schema has no record_versions table:
history lives in superseded records and cached responses. Unknown tables deny.
GET /records emits no retrieval event; unexpected retrievals block, not broad erase.
Completion covers logical live SQLite only, NOT physical WAL/free pages, backups,
replicas or exports. Those require explicit operator retention/expiry policy.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from types import MethodType
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.contracts import Permission
from app.schemas import ScopePath
from app.storage import IdempotencyConflictError

POLICY = "verified-extract-v1"
MAX_BODY = 4096
MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
UUID = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
RECORD_ID = re.compile(r"rec_[a-f0-9]{32}")
OBJECT_ID = re.compile(r"(?:rec|art|rel|fnd)_[a-f0-9]{32}")
TABLES = {
    "schema_migrations",
    "sqlite_sequence",
    "records",
    "entities",
    "relations",
    "artifacts",
    "audit_events",
    "retrieval_events",
    "review_findings",
    "idempotency_requests",
    "records_fts",
    "records_fts_data",
    "records_fts_idx",
    "records_fts_content",
    "records_fts_docsize",
    "records_fts_config",
    "application_scrub_tombstones",
    "application_scrub_operations",
    "application_scrub_objects",
}


class ApplicationScrubRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope_path: str = Field(max_length=1000)
    application_id: str = Field(pattern="^" + UUID + "$")
    receipt_id: str = Field(pattern="^" + UUID + "$")
    subject: str = Field(min_length=1, max_length=500)
    allow_empty: bool = False  # Explicit trusted-owner request; never inferred from a 403.

    @model_validator(mode="after")
    def binding(self):
        ScopePath.validate(self.scope_path)
        if "/" in self.subject or self.subject != self.subject.strip():
            raise ValueError("invalid subject segment")
        suffix = "/application:" + self.application_id + "/subject:" + self.subject
        if not self.scope_path.endswith(suffix):
            raise ValueError("scope/application/subject mismatch")
        prefix = self.scope_path[: -len(suffix)]
        root, sep, project = prefix.rpartition("/project:")
        if not sep or root in {"global", "public", ""} or not re.fullmatch("[a-f0-9]{64}", project):
            raise ValueError("exact Stage3 project/application/subject scope required")
        ScopePath.validate(root)
        return self


def _fail(status=409, code="application_scrub_dependency"):
    raise HTTPException(
        status,
        detail={
            "code": code,
            "message": "bounded application scrub denied",
            "details": {"complete": False},
        },
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _binding(scope, attrs, actor):
    return _digest(
        [scope, attrs.get("applicationId"), attrs.get("receiptId"), attrs.get("subject"), actor]
    )


def _owned(value, req, actor):
    attrs = value.get("attrs", {})
    return (
        isinstance(attrs, dict)
        and value.get("scope_path") == req.scope_path
        and value.get("author_actor") == actor
        and attrs.get("policy") == POLICY
        and attrs.get("applicationId") == req.application_id
        and attrs.get("receiptId") == req.receipt_id
        and attrs.get("subject") == req.subject
    )


def _text(row):
    # Recursively decode JSON-in-JSON; escaped IDs cannot conceal dependencies.
    def normalize(value, depth=0):
        if depth > 32:
            _fail(409, "application_scrub_scan_bound")
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                return value
            return normalize(decoded, depth + 1)
        if isinstance(value, dict):
            return [[normalize(k, depth + 1), normalize(v, depth + 1)] for k, v in value.items()]
        if isinstance(value, list):
            return [normalize(v, depth + 1) for v in value]
        return value

    return json.dumps(normalize(row), ensure_ascii=False)


def guard_erased_binding(conn, scope, attrs, actor):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='application_scrub_tombstones'"
    ).fetchone()
    if (
        exists
        and conn.execute(
            "SELECT 1 FROM application_scrub_tombstones WHERE binding_hash=?",
            (_binding(scope, attrs, actor),),
        ).fetchone()
    ):
        raise IdempotencyConflictError


def guard_erased_references(conn, value):
    # Close the check/insert race for legacy non-idempotent owner callers too.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='application_scrub_objects'"
    ).fetchone()
    if exists:
        for reference in set(OBJECT_ID.findall(_text(value))):
            if conn.execute(
                "SELECT 1 FROM application_scrub_objects WHERE id_hash=? LIMIT 1",
                (_digest(reference),),
            ).fetchone():
                raise IdempotencyConflictError


def install_application_scrub_guard(store):
    """Installed by SqliteStore initialization before every writer can use the store.

    Runs in the original write transaction, including supersession insertion.
    Hash-only operation tombstones reject old-key replay; receipt tombstones
    reject fresh-key recreation, including after restart and racing requests.
    """
    if getattr(store, "_application_scrub_guard", False):
        return store
    original_replay = store._idempotency_replay

    def replay_guard(self, conn, **kwargs):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='application_scrub_operations'"
        ).fetchone()
        if (
            exists
            and conn.execute(
                "SELECT 1 FROM application_scrub_operations WHERE key_hash=?",
                (_digest([kwargs["actor"], kwargs["idempotency_key"]]),),
            ).fetchone()
        ):
            raise IdempotencyConflictError
        return original_replay(conn, **kwargs)

    store._idempotency_replay = MethodType(replay_guard, store)
    original_save = store._save_idempotency

    def save_guard(self, conn, **kwargs):
        guard_erased_references(conn, kwargs["result"].model_dump(mode="json"))
        return original_save(conn, **kwargs)

    store._save_idempotency = MethodType(save_guard, store)
    original = store._persist_record

    def guarded(self, conn, record, *, actor, audit_create):
        guard_erased_references(conn, record.model_dump(mode="json"))
        guard_erased_binding(conn, record.scope_path, record.attrs, actor)
        return original(conn, record, actor=actor, audit_create=audit_create)

    store._persist_record = MethodType(guarded, store)
    store._application_scrub_guard = True
    return store


@lru_cache(maxsize=1)
def _schema_columns():
    # Derive the supported schema from migrations, never trust hidden copy columns.
    from app.migrations import MIGRATIONS

    with closing(sqlite3.connect(":memory:")) as conn:
        for migration in MIGRATIONS:
            migration.up(conn)
        return {
            row[0]: tuple(tuple(col) for col in conn.execute(f"PRAGMA table_xinfo({row[0]})"))
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def scrub_application_records(store, req: ApplicationScrubRequest, auth, reason: str):
    """One BEGIN IMMEDIATE; denied dependency rolls back ALL writes and DDL."""
    from app.main import _authorize_effective_scope, _require_permission, _validate_reason

    _require_permission(auth, Permission("memory.admin"))
    _authorize_effective_scope(req.scope_path, auth)
    _validate_reason(reason, "application scrub")
    if auth.scope_path in {"global", "public"} or not auth.actor.startswith("unify:"):
        _fail(403, "application_scrub_ownership")
    install_application_scrub_guard(store)
    attrs = {
        "applicationId": req.application_id,
        "receiptId": req.receipt_id,
        "subject": req.subject,
    }
    binding_hash = _binding(req.scope_path, attrs, auth.actor)
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - TABLES:
            _fail(409, "application_scrub_unknown_schema")
        for table, columns in _schema_columns().items():
            if table.startswith("records_fts") and "records_fts" not in tables:
                continue
            actual_columns = tuple(
                tuple(col) for col in conn.execute(f"PRAGMA table_xinfo({table})")
            )
            if actual_columns != columns:
                _fail(409, "application_scrub_unknown_schema")
        for table, names in {
            "application_scrub_tombstones": ["binding_hash"],
            "application_scrub_operations": ["key_hash"],
            "application_scrub_objects": ["id_hash", "binding_hash"],
            "schema_migrations": ["version", "applied_at", "checksum"],
        }.items():
            if table in tables:
                columns = list(conn.execute(f"PRAGMA table_xinfo({table})"))
                if [col[1] for col in columns] != names or any(col[6] != 0 for col in columns):
                    _fail(409, "application_scrub_unknown_schema")
        if "records_fts" in tables:
            mismatch = conn.execute(
                "SELECT EXISTS(SELECT id,title,content FROM records "
                "EXCEPT SELECT id,title,content FROM records_fts) OR "
                "EXISTS(SELECT id,title,content FROM records_fts "
                "EXCEPT SELECT id,title,content FROM records)"
            ).fetchone()[0]
            if mismatch:
                _fail(409, "application_scrub_index_inconsistent")
        elif any(table.startswith("records_fts_") for table in tables):
            _fail(409, "application_scrub_unknown_schema")
        # Unknown triggers/views may introduce content copies or side effects.
        from app.migrations import _up_0004

        def normalize(sql):
            return " ".join(sql.rstrip(";").split())

        expected = {
            normalize(sql)
            for sql in re.findall(
                r"CREATE TRIGGER .*?END;",
                inspect.getsource(_up_0004),
                re.DOTALL,
            )
        }
        actual = {
            normalize(r[0])
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('trigger','view')",
            )
        }
        if actual != expected:
            _fail(409, "application_scrub_unknown_schema")
        data = {}
        total_bytes = 0
        for table in (
            "records",
            "entities",
            "artifacts",
            "relations",
            "review_findings",
            "retrieval_events",
            "audit_events",
            "idempotency_requests",
        ):
            rows = []
            for row in conn.execute(f"SELECT * FROM {table} LIMIT ?", (MAX_ROWS + 1,)):
                row = dict(row)
                total_bytes += len(_text(row).encode())
                if len(rows) >= MAX_ROWS or total_bytes > MAX_BYTES:
                    _fail(409, "application_scrub_scan_bound")
                rows.append(row)
            data[table] = rows
        if "application_scrub_objects" in tables:
            erased_hashes = {
                r[0]
                for r in conn.execute(
                    "SELECT id_hash FROM application_scrub_objects WHERE binding_hash=?",
                    (binding_hash,),
                )
            }
            for rows in data.values():
                for row in rows:
                    if any(_digest(ref) in erased_hashes for ref in OBJECT_ID.findall(_text(row))):
                        _fail()
        owned = []
        for row in data["records"]:
            try:
                row_attrs = json.loads(row["attrs_json"])
            except ValueError:
                _fail()
            value = {**row, "attrs": row_attrs}
            if isinstance(row_attrs, dict) and row_attrs.get("receiptId") == req.receipt_id:
                if not _owned(value, req, auth.actor):
                    _fail(403, "application_scrub_ownership")
                if (
                    row["entity_type"]
                    or row["entity_id"]
                    or row["topic"]
                    != "application-knowledge:"
                    + hashlib.sha256(req.scope_path.encode()).hexdigest()
                ):
                    _fail()
                owned.append(row)
        ids = {r["id"] for r in owned}
        known_tombstone = bool(
            "application_scrub_tombstones" in tables
            and conn.execute(
                "SELECT 1 FROM application_scrub_tombstones WHERE binding_hash=?",
                (binding_hash,),
            ).fetchone()
        )
        replayed = not ids and known_tombstone
        if not ids and not known_tombstone and not req.allow_empty:
            _fail(403, "application_scrub_unknown_binding")
        # Explicit scoped admin authority may close a never-written receipt.
        # Continue EVERY schema, ownership and dependency check below. Only then
        # atomically install the binding tombstone, blocking late/fresh-key writes.
        # This neither infers erasure from a missing row nor grants authority over
        # another actor's records or copies in dependent stores.
        # Outbound reuse is safe to remove, not authority over the source.
        # Resolve every reference to an existing same-scope/owner record.
        allowed_refs = set(ids)
        for row in data["records"]:
            attrs = json.loads(row["attrs_json"])
            if (
                row["scope_path"] == req.scope_path
                and row["author_actor"] == auth.actor
                and isinstance(attrs, dict)
                and attrs.get("policy") == POLICY
                and attrs.get("applicationId") == req.application_id
                and attrs.get("subject") == req.subject
            ):
                allowed_refs.add(row["id"])
        for row in owned:
            if set(RECORD_ID.findall(_text(row))) - allowed_refs:
                _fail()
        detached = set()
        for row in data["records"]:
            if row["id"] in ids:
                continue
            # A reciprocal supersession backlink is owner-generated metadata,
            # not a reuse claim. Archive predecessor without reviving its facts.
            backlink = row["superseded_by"]
            stripped = {**row, "superseded_by": None}
            if (
                row["id"] in allowed_refs
                and row["lifecycle"] == "superseded"
                and backlink in ids
                and any(r["id"] == backlink and r["supersedes"] == row["id"] for r in owned)
                and not ids.intersection(RECORD_ID.findall(_text(stripped)))
                and req.receipt_id not in _text(stripped)
            ):
                detached.add(row["id"])
                continue
            if ids.intersection(RECORD_ID.findall(_text(row))) or req.receipt_id in _text(row):
                _fail()
        # Delete attributable copies, never unrelated subjects or remote bytes.
        dependent = {"relations": [], "review_findings": [], "retrieval_events": []}
        dependent_ids = set()
        for table in ("entities", "artifacts", "relations", "review_findings", "retrieval_events"):
            for row in data[table]:
                refs = set(RECORD_ID.findall(_text(row)))
                relevant = (
                    row["scope_path"] == req.scope_path
                    or bool(ids.intersection(refs))
                    or req.receipt_id in _text(row)
                    or table == "retrieval_events"
                    and (
                        ScopePath.is_descendant_or_equal(req.scope_path, row["scope_path"])
                        or ScopePath.is_descendant_or_equal(row["scope_path"], req.scope_path)
                    )
                )
                if not relevant:
                    continue
                if row["scope_path"] != req.scope_path:
                    _fail()
                if table == "relations":
                    safe = (
                        row["author_actor"] == auth.actor
                        and row["from_id"] in ids
                        and row["to_id"] in ids
                        and refs <= ids
                    )
                elif table == "review_findings":
                    safe = (
                        row["subject_kind"] == "record"
                        and row["subject_id"] in ids
                        and row["created_by_actor"] == auth.actor
                        and row["resolved_by_actor"] in {None, auth.actor}
                        and refs <= ids
                    )
                elif table == "retrieval_events":
                    # These dedicated columns are written only by the owner store
                    # with a validated binding, NEVER inferred from query JSON.
                    bound = (
                        row["actor"] == auth.actor
                        and row["application_id"] == req.application_id
                        and row["subject"] == req.subject
                        and re.fullmatch(UUID, str(row["receipt_id"] or ""))
                    )
                    if (
                        bound
                        and row["receipt_id"] != req.receipt_id
                        and not ids.intersection(refs)
                        and req.receipt_id not in _text(row)
                    ):
                        continue
                    safe = bound and row["receipt_id"] == req.receipt_id and refs <= allowed_refs
                else:
                    # URI presence is NOT evidence of remote artifact erasure.
                    safe = False
                if not safe:
                    if (
                        table in {"relations", "review_findings"}
                        and not ids.intersection(refs)
                        and req.receipt_id not in _text(row)
                    ):
                        continue
                    _fail()
                dependent[table].append(row["id"])
                if table != "retrieval_events":
                    dependent_ids.add(row["id"])
        object_ids = ids | dependent_ids
        for row in owned:
            internal_refs = set(re.findall(r"(?:rec|art|rel|fnd)_[a-f0-9]{32}", _text(row)))
            if internal_refs - (allowed_refs | dependent_ids):
                _fail()
        for table, selected_ids in dependent.items():
            for row in data[table]:
                if row["id"] in selected_ids:
                    if set(OBJECT_ID.findall(_text(row))) - (allowed_refs | dependent_ids):
                        _fail()
        # Non-record dependent IDs must not hide in another object/cache.
        for table in ("records", "entities", "artifacts", "relations", "review_findings"):
            for row in data[table]:
                if row["id"] not in object_ids and any(x in _text(row) for x in dependent_ids):
                    _fail()
        audits = []
        for row in data["audit_events"]:
            linked = row["object_id"] in object_ids or row["object_id"] == req.receipt_id
            if row["object_id"] in detached and row["action"] == "record.supersede":
                detail = json.loads(row["detail_json"])
                linked = isinstance(detail, dict) and detail.get("replacement_id") in ids
            mentioned = any(x in _text(row) for x in object_ids)
            if linked or mentioned or req.receipt_id in _text(row):
                if (
                    not linked
                    or row["scope_path"] != req.scope_path
                    or row["actor"] != auth.actor
                    or set(OBJECT_ID.findall(_text(row))) - (allowed_refs | dependent_ids)
                ):
                    _fail()
                audits.append(row["id"])
        cached = []
        for row in data["idempotency_requests"]:
            linked = row["object_id"] in object_ids
            detached_copy = row["object_id"] in detached
            mentioned = any(x in _text(row) for x in object_ids)
            if linked or mentioned or req.receipt_id in _text(row):
                try:
                    response = json.loads(row["response_json"])
                except ValueError:
                    _fail()
                if (
                    not (linked or detached_copy)
                    or row["actor"] != auth.actor
                    or not isinstance(response, dict)
                    or not (
                        _owned(response, req, auth.actor)
                        if row["object_id"] in ids
                        else response.get("scope_path") == req.scope_path
                        and response.get("author_actor", response.get("created_by_actor"))
                        == auth.actor
                    )
                    or (
                        detached_copy
                        and not (
                            response.get("superseded_by") in ids
                            and isinstance(response.get("attrs"), dict)
                            and response["attrs"].get("policy") == POLICY
                            and response["attrs"].get("applicationId") == req.application_id
                            and response["attrs"].get("subject") == req.subject
                        )
                    )
                    or response.get("id") != row["object_id"]
                    or response.get("entity")
                    or set(OBJECT_ID.findall(_text(row))) - (allowed_refs | dependent_ids)
                ):
                    _fail()
                cached.append((row["actor"], row["idempotency_key"]))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS application_scrub_tombstones "
            "(binding_hash TEXT PRIMARY KEY)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO application_scrub_tombstones VALUES (?)", (binding_hash,)
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS application_scrub_objects "
            "(id_hash TEXT PRIMARY KEY, binding_hash TEXT NOT NULL)"
        )
        for object_id in object_ids:
            conn.execute(
                "INSERT OR IGNORE INTO application_scrub_objects VALUES (?, ?)",
                (_digest(object_id), binding_hash),
            )
        # Retain only one-way actor/key bindings, never caller-controlled key text,
        # request hashes, response bytes, object IDs, timestamps or operation text.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS application_scrub_operations " "(key_hash TEXT PRIMARY KEY)"
        )
        for actor, key in cached:
            conn.execute(
                "INSERT OR IGNORE INTO application_scrub_operations VALUES (?)",
                (_digest([actor, key]),),
            )
            conn.execute(
                "DELETE FROM idempotency_requests WHERE actor=? AND idempotency_key=?",
                (actor, key),
            )
        for table, row_ids in dependent.items():
            for row_id in row_ids:
                conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        for audit_id in audits:
            conn.execute("DELETE FROM audit_events WHERE id=?", (audit_id,))
        from app.schemas import utc_now

        for record_id in detached:
            conn.execute(
                "UPDATE records SET lifecycle='archived', superseded_by=NULL, deleted_at=?, "
                "lifecycle_changed_at=?, updated_at=?, version=version+1 WHERE id=?",
                (utc_now(), utc_now(), utc_now(), record_id),
            )
        # Cyclic supersedes/superseded_by references are checked at COMMIT.
        # No lifecycle trigger bypass or FK disabling is necessary.
        conn.execute("PRAGMA defer_foreign_keys=ON")
        for record_id in ids:
            if "records_fts" in tables:
                conn.execute("DELETE FROM records_fts WHERE id=?", (record_id,))
        for record_id in ids:
            conn.execute("DELETE FROM records WHERE id=?", (record_id,))
        # Assert cleanup before commit. A successful result is never a mere transition.
        for record_id in ids:
            if conn.execute("SELECT 1 FROM records WHERE id=?", (record_id,)).fetchone():
                _fail()
            if (
                "records_fts" in tables
                and conn.execute(
                    "SELECT 1 FROM records_fts WHERE id=?",
                    (record_id,),
                ).fetchone()
            ):
                _fail()
        return {
            "complete": True,
            "replayed": replayed,
            "records_scrubbed": len(ids),
            "erasure": "logical-live-store",
            "physical_erasure": False,
        }


def register_application_lifecycle(app, get_store, require_auth):
    """Parent-owned route registration; reuse existing auth, never a parallel token."""

    if getattr(app.state, "application_scrub_registered", False):
        return
    app.state.application_scrub_registered = True

    @app.middleware("http")
    async def scrub_reason_envelope(request: Request, call_next):
        if request.url.path == "/applications/knowledge/scrub":
            # main's denial envelope otherwise stores caller reason plaintext even
            # after auth/validation failure. Keep validation input in memory only.
            request.state.application_scrub_reason = request.headers.get("X-MemoryV4-Reason")
            request.scope["headers"] = [
                (k, b"application privacy scrub" if k.lower() == b"x-memoryv4-reason" else v)
                for k, v in request.scope["headers"]
                if k.lower() != b"x-request-id"
            ] + [(b"x-request-id", ("req_" + uuid4().hex).encode())]
            if hasattr(request, "_headers"):
                del request._headers
        return await call_next(request)

    @app.post("/applications/knowledge/scrub", tags=["applications"])
    async def application_scrub(
        request: Request, auth=Depends(require_auth), store=Depends(get_store)
    ):
        from app.main import _require_permission, _validate_reason

        _require_permission(auth, Permission("memory.admin"))
        reason = _validate_reason(request.state.application_scrub_reason, "application scrub")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY:
                _fail(413, "request_too_large")
            body.extend(chunk)
        try:

            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate field")
                    result[key] = value
                return result

            if request.query_params:
                raise ValueError("unexpected query")
            req = ApplicationScrubRequest.model_validate(
                json.loads(bytes(body), object_pairs_hook=unique_pairs)
            )
        except (ValidationError, ValueError, UnicodeDecodeError, RecursionError):
            _fail(422, "invalid_request")
        return scrub_application_records(store, req, auth, reason)
