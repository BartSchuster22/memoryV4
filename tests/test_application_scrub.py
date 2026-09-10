# Real migrated tempfile SQLite tests; no containers or live services.
import hashlib
import json
from contextlib import closing
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.application_lifecycle import (
    ApplicationScrubRequest,
    install_application_scrub_guard,
    register_application_lifecycle,
    scrub_application_records,
)
from app.contracts import Permission
from app.main import AuthContext, create_app
from app.schemas import ArtifactCreate, RecordCreate, RecordPatch, RecordSupersedeRequest
from app.settings import ApiKeyGrant
from app.storage import IdempotencyConflictError, SqliteStore

SECRET = "SECRETSTAGE3MARKER"
APP = "11111111-1111-4111-8111-111111111111"
RECEIPT = "22222222-2222-4222-8222-222222222222"
SCOPE = "org:test/project:" + "a" * 64 + "/application:" + APP + "/subject:alice"
ACTOR = "unify:owner"


def auth(scope="org:test", actor=ACTOR, admin=True):
    return AuthContext(
        ApiKeyGrant(
            actor,
            scope,
            frozenset(
                {
                    Permission("memory.admin" if admin else "memory.read"),
                }
            ),
        )
    )


def request(**kw):
    return ApplicationScrubRequest(
        **{
            "scope_path": SCOPE,
            "application_id": APP,
            "receipt_id": RECEIPT,
            "subject": "alice",
            **kw,
        }
    )


def payload(scope=SCOPE, receipt=RECEIPT, content=SECRET):
    return RecordCreate(
        title="Application plan",
        content=content,
        role="exhaust",
        lifecycle="working",
        write_policy="immutable",
        scope_path=scope,
        topic="application-knowledge:" + hashlib.sha256(scope.encode()).hexdigest(),
        tags=["receipt:" + receipt, "kind:plan"],
        attrs={
            "policy": "verified-extract-v1",
            "applicationId": APP,
            "receiptId": receipt,
            "subject": scope.rsplit("subject:", 1)[-1],
        },
    )


def create(store, **kw):
    rec = payload(**kw)
    key = "ak:" + hashlib.sha256(str(uuid4()).encode()).hexdigest()
    result, _ = store.create_record_idempotent(
        rec, actor=ACTOR, idempotency_key=key, request_hash="original"
    )
    return result, rec, key


@pytest.fixture
def store(tmp_path):
    result = SqliteStore(tmp_path / "fresh.sqlite3")
    yield result
    result.close()


def scrub(store, req=None, who=None):
    return scrub_application_records(store, req or request(), who or auth(), "privacy request")


def logical_dump(store):
    with closing(store._connect()) as conn:
        tables = ["records", "idempotency_requests", "audit_events"]
        if store._has_fts(conn):
            tables += ["records_fts"]
        return json.dumps(
            {t: [dict(r) for r in conn.execute("SELECT * FROM " + t)] for t in tables}
        )


def test_current_historical_versions_fts_cache_audit_repeat_and_replay(store):
    old, original, key = create(store)
    updated, _ = store.update_record_idempotent(
        old.id,
        RecordPatch(content="new clean text"),
        actor=ACTOR,
        expected_version=1,
        idempotency_key="ak:" + "b" * 64,
        request_hash="patch",
    )
    replacement, _ = store.supersede_record_idempotent(
        updated.id,
        RecordSupersedeRequest(content="replacement text"),
        actor=ACTOR,
        expected_version=updated.version,
        idempotency_key="ak:" + "c" * 64,
        request_hash="supersede",
        reason=SECRET,
    )
    assert updated.version > old.version
    assert SECRET in logical_dump(store)
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM records").fetchone()[0] == 2
        assert store._has_fts(conn), "test runtime must exercise real FTS5"
    result = scrub(store)
    assert result == {
        "complete": True,
        "replayed": False,
        "records_scrubbed": 2,
        "erasure": "logical-live-store",
        "physical_erasure": False,
    }
    assert SECRET not in logical_dump(store)
    assert store.get_record(old.id) is None
    assert store.get_record(replacement.id) is None
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM records_fts").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM records_fts WHERE records_fts MATCH ?", (SECRET,)
            ).fetchone()[0]
            == 0
        )
        rows = conn.execute("SELECT * FROM idempotency_requests").fetchall()
        assert rows == []
        assert conn.execute("SELECT count(*) FROM application_scrub_operations").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0
        assert all(r[0] == "{}" for r in conn.execute("SELECT detail_json FROM audit_events"))
    assert scrub(store)["replayed"] is True
    other = SqliteStore(store.database_path)
    try:
        with pytest.raises(IdempotencyConflictError):
            other.create_record_idempotent(
                original, actor=ACTOR, idempotency_key=key, request_hash="original"
            )
    finally:
        other.close()
    with pytest.raises(IdempotencyConflictError):
        create(store)
    assert store.get_record(old.id) is None


@pytest.mark.parametrize(
    "dimension", ["project", "application", "subject", "receipt", "grant", "actor", "permission"]
)
def test_foreign_binding_denied_without_touching_data(store, dimension):
    create(store)
    req, who = request(), auth()
    if dimension == "project":
        req = request(scope_path=SCOPE.replace("a" * 64, "b" * 64))
    elif dimension == "application":
        new = "33333333-3333-4333-8333-333333333333"
        req = request(application_id=new, scope_path=SCOPE.replace(APP, new))
    elif dimension == "subject":
        req = request(subject="bob", scope_path=SCOPE.replace("subject:alice", "subject:bob"))
    elif dimension == "receipt":
        req = request(receipt_id="33333333-3333-4333-8333-333333333333")
    elif dimension == "grant":
        who = auth(scope="org:foreign")
    elif dimension == "actor":
        who = auth(actor="unify:other")
    else:
        who = auth(admin=False)
    before = logical_dump(store)
    with pytest.raises(HTTPException) as exc:
        scrub(store, req, who)
    assert exc.value.status_code == 403
    assert logical_dump(store) == before


def test_unrelated_receipt_same_subject_is_not_deleted(store):
    create(store)
    other, _, _ = create(store, receipt=str(uuid4()), content="unrelated receipt data")
    foreign, _, _ = create(
        store,
        scope=SCOPE.replace("a" * 64, "b" * 64),
        receipt=str(uuid4()),
        content="foreign project data",
    )
    assert scrub(store)["complete"]
    assert store.get_record(other.id).content == "unrelated receipt data"
    assert store.get_record(foreign.id).content == "foreign project data"


def test_unexpected_linked_artifact_denies_atomically(store):
    rec, _, _ = create(store)
    store.create_artifact_idempotent(
        ArtifactCreate(
            record_id=rec.id,
            artifact_type="external",
            uri="https://example.org/file",
            scope_path=SCOPE,
        ),
        actor=ACTOR,
        idempotency_key="artifact-test-key",
        request_hash="artifact",
    )
    before = logical_dump(store)
    with pytest.raises(HTTPException) as exc:
        scrub(store)
    assert exc.value.detail["details"]["complete"] is False
    assert logical_dump(store) == before
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='application_scrub_tombstones'"
        ).fetchone()


@pytest.mark.parametrize(
    "dependency",
    ["foreign_record", "escaped_reference", "retrieval", "foreign_cache", "unknown_version_table"],
)
def test_unresolved_dependencies_fail_closed(store, dependency):
    rec, _, _ = create(store)
    with closing(store._connect()) as conn, conn:
        if dependency == "retrieval":
            store.write_retrieval(
                conn, query=SECRET, scope_path=SCOPE, actor=ACTOR, result_count=1, degraded=False
            )
        elif dependency == "foreign_cache":
            response = {"id": "rec_" + "f" * 32, "content": SECRET, "priorIds": [rec.id]}
            conn.execute(
                "INSERT INTO idempotency_requests VALUES (?,?,?,?,?,?,?,?)",
                (
                    "unify:foreign",
                    "record.create",
                    "foreign-key",
                    "hash",
                    json.dumps(response),
                    201,
                    response["id"],
                    "now",
                ),
            )
        elif dependency == "unknown_version_table":
            conn.execute("CREATE TABLE record_versions (content TEXT)")
            conn.execute("INSERT INTO record_versions VALUES (?)", (SECRET,))
    if dependency in {"foreign_record", "escaped_reference"}:
        content = json.dumps({"priorIds": [rec.id]})
        if dependency == "escaped_reference":
            content = content.replace("rec_", r"\u0072ec_")
        create(
            store, receipt=str(uuid4()), scope=SCOPE.replace("a" * 64, "b" * 64), content=content
        )
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store)
    assert logical_dump(store) == before


def test_mid_transaction_failure_rolls_back_content_and_tombstones(store):
    rec, _, _ = create(store)
    original = store._connect
    import sqlite3

    def connection():
        conn = original()
        conn.set_authorizer(
            lambda action, table, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_DELETE and table == "records"
            else sqlite3.SQLITE_OK
        )
        return conn

    store._connect = connection
    with pytest.raises(sqlite3.DatabaseError):
        scrub(store)
    store._connect = original
    assert SECRET in logical_dump(store)
    assert store.get_record(rec.id)
    with closing(store._connect()) as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='application_scrub_tombstones'"
        ).fetchone()


def test_real_main_auth_registration_reason_size_and_replay(store, monkeypatch):
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(store.database_path))
    monkeypatch.setenv("MEMORYV4_API_KEY", "scrub-test-token")
    monkeypatch.setenv("MEMORYV4_API_ACTOR", ACTOR)
    monkeypatch.setenv("MEMORYV4_API_SCOPE", "org:test")
    monkeypatch.setenv("MEMORYV4_API_PERMISSIONS", "memory.admin")
    monkeypatch.delenv("MEMORYV4_API_KEYS_JSON", raising=False)
    app = create_app()
    route = next(
        r for r in app.routes if getattr(r, "path", "") == "/records" and "POST" in r.methods
    )
    deps = {d.name: d.call for d in route.dependant.dependencies}
    register_application_lifecycle(app, deps["store"], deps["auth"])
    install_application_scrub_guard(store)
    app.dependency_overrides[deps["store"]] = lambda: store
    create(store)
    path = "/applications/knowledge/scrub"
    headers = {
        "Authorization": "Bearer scrub-test-token",
        "X-MemoryV4-Reason": SECRET,
        "X-Request-ID": SECRET,
    }
    with TestClient(app) as client:
        assert client.post(path, json=request().model_dump()).status_code == 401
        assert (
            client.post(
                path,
                json=request().model_dump(),
                headers={
                    "Authorization": headers["Authorization"],
                },
            ).status_code
            == 422
        )
        assert client.post(path, content=b"x" * 4097, headers=headers).status_code == 413
        assert (
            client.post(
                path, json={**request().model_dump(), "record_ids": ["arbitrary"]}, headers=headers
            ).status_code
            == 422
        )
        response = client.post(path, json=request().model_dump(), headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["complete"] is True
        assert client.post(path, json=request().model_dump(), headers=headers).json()["replayed"]
    assert SECRET not in logical_dump(store)


def test_current_secret_content_provenance_attrs_and_fts_erased(store):
    rec = payload().model_copy(
        update={
            "title": SECRET,
            "provenance": {"question": SECRET},
            "attrs": {**payload().attrs, "question": SECRET},
        }
    )
    record, _ = store.create_record_idempotent(
        rec,
        actor=ACTOR,
        idempotency_key="ak:" + "d" * 64,
        request_hash="hash",
    )
    with closing(store._connect()) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM records_fts WHERE records_fts MATCH ?", (SECRET,)
            ).fetchone()[0]
            == 1
        )
    assert scrub(store)["complete"]
    assert SECRET not in logical_dump(store)
    with closing(store._connect()) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM records_fts WHERE records_fts MATCH ?", (SECRET,)
            ).fetchone()[0]
            == 0
        )
    assert store.get_record(record.id) is None


@pytest.mark.parametrize("field", ["applicationId", "subject", "policy"])
def test_record_attrs_cannot_be_overridden_by_request(store, field):
    record, _, _ = create(store)
    with closing(store._connect()) as conn, conn:
        attrs = dict(payload().attrs)
        attrs[field] = "foreign"
        conn.execute("UPDATE records SET attrs_json=? WHERE id=?", (json.dumps(attrs), record.id))
    before = logical_dump(store)
    with pytest.raises(HTTPException) as exc:
        scrub(store)
    assert exc.value.status_code == 403
    assert logical_dump(store) == before


def test_cross_receipt_correction_is_not_implicit_authority(store):
    first, _, _ = create(store)
    replacement = RecordSupersedeRequest(
        content="foreign receipt correction",
        attrs={**payload().attrs, "receiptId": str(uuid4())},
    )
    store.supersede_record_idempotent(
        first.id,
        replacement,
        actor=ACTOR,
        expected_version=1,
        idempotency_key="ak:" + "e" * 64,
        request_hash="hash",
        reason="correction",
    )
    before = logical_dump(store)
    with pytest.raises(HTTPException) as exc:
        scrub(store)
    assert exc.value.status_code == 409
    assert logical_dump(store) == before


def test_reopened_guard_blocks_fresh_key(store):
    create(store)
    scrub(store)
    other = install_application_scrub_guard(SqliteStore(store.database_path))
    try:
        with pytest.raises(IdempotencyConflictError):
            create(other)
    finally:
        other.close()


def test_scan_limit_denies_without_deleting_unrelated_data(store, monkeypatch):
    import app.application_lifecycle as lifecycle

    create(store)
    create(store, receipt=str(uuid4()), content="unrelated")
    monkeypatch.setattr(lifecycle, "MAX_ROWS", 1)
    before = logical_dump(store)
    with pytest.raises(HTTPException) as exc:
        scrub(store)
    assert exc.value.detail["code"] == "application_scrub_scan_bound"
    assert logical_dump(store) == before


def test_replay_does_not_hide_new_unresolved_dependency(store):
    create(store)
    assert scrub(store)["complete"]
    with closing(store._connect()) as conn, conn:
        store.write_retrieval(
            conn, query=SECRET, scope_path=SCOPE, actor=ACTOR, result_count=0, degraded=False
        )
    with pytest.raises(HTTPException) as exc:
        scrub(store)
    assert exc.value.detail["details"]["complete"] is False
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT query FROM retrieval_events").fetchone()[0] == SECRET


def test_reuse_receipts_delete_dependents_first_then_source(store):
    source, _, _ = create(store)
    child_id = str(uuid4())
    child, _, _ = create(
        store,
        receipt=child_id,
        content=json.dumps({"knowledge": [{"recordId": source.id, "quote": SECRET}]}),
    )
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store)
    assert logical_dump(store) == before
    assert scrub(store, request(receipt_id=child_id))["complete"]
    assert store.get_record(source.id)
    assert store.get_record(child.id) is None
    assert scrub(store)["complete"]
    assert SECRET not in logical_dump(store)


def test_owned_relation_retrieval_and_idempotency_copies_removed(store):
    from app.schemas import RelationCreate

    record, _, _ = create(store)
    relation, _ = store.create_relation_idempotent(
        RelationCreate(
            from_ref={"kind": "record", "id": record.id},
            to_ref={"kind": "record", "id": record.id},
            relation_type="supports",
            scope_path=SCOPE,
            provenance={"quote": SECRET},
        ),
        actor=ACTOR,
        idempotency_key="private-key-" + SECRET,
        request_hash=SECRET,
    )
    with closing(store._connect()) as conn, conn:
        query = {**payload().attrs, "query": SECRET, "recordId": record.id}
        store.write_retrieval(
            conn,
            query=json.dumps(query),
            scope_path=SCOPE,
            actor=ACTOR,
            result_count=1,
            degraded=False,
            application_binding=request(),
        )
        other = {**payload().attrs, "receiptId": str(uuid4()), "query": "unrelated"}
        store.write_retrieval(
            conn,
            query=json.dumps(other),
            scope_path=SCOPE,
            actor=ACTOR,
            result_count=0,
            degraded=False,
            application_binding=request(receipt_id=other["receiptId"]),
        )
    assert scrub(store)["complete"]
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM relations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM retrieval_events").fetchone()[0] == 1
        assert "unrelated" in conn.execute("SELECT query FROM retrieval_events").fetchone()[0]
        for table in [
            "records",
            "records_fts",
            "audit_events",
            "idempotency_requests",
            "application_scrub_tombstones",
            "application_scrub_operations",
        ]:
            dump = json.dumps([tuple(r) for r in conn.execute("SELECT * FROM " + table)])
            assert SECRET not in dump
            assert relation.id not in dump


@pytest.mark.parametrize("scope", ["global", "public"])
def test_no_privileged_global_grant_bypass(store, scope):
    create(store)
    before = logical_dump(store)
    with pytest.raises(HTTPException) as exc:
        scrub(store, who=auth(scope=scope))
    assert exc.value.status_code == 403
    assert logical_dump(store) == before


@pytest.mark.parametrize(
    "dependency",
    [
        "hidden_column",
        "unknown_trigger",
        "unknown_reference",
        "receipt_reference",
        "ancestor_retrieval",
    ],
)
def test_additional_fail_closed_surfaces(store, dependency):
    record, _, _ = create(store)
    with closing(store._connect()) as conn, conn:
        if dependency == "hidden_column":
            conn.execute("ALTER TABLE idempotency_requests ADD COLUMN hidden_copy TEXT")
            conn.execute("UPDATE idempotency_requests SET hidden_copy=?", (SECRET,))
        elif dependency == "unknown_trigger":
            conn.execute("CREATE TRIGGER unexpected AFTER DELETE ON records BEGIN SELECT 1; END")
        elif dependency == "ancestor_retrieval":
            store.write_retrieval(
                conn,
                query=SECRET,
                scope_path="org:test",
                actor=ACTOR,
                result_count=1,
                degraded=False,
            )
    if dependency == "unknown_reference":
        create(store, content=json.dumps({"artifactId": "art_" + "f" * 32}))
    if dependency == "receipt_reference":
        create(store, receipt=str(uuid4()), content=json.dumps({"corrects": RECEIPT}))
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store)
    assert logical_dump(store) == before
    assert store.get_record(record.id)


def test_owner_route_registered_without_parent_hook_and_strict_json(store, monkeypatch):
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(store.database_path))
    monkeypatch.setenv(
        "MEMORYV4_API_KEYS",
        json.dumps(
            {
                "test-key": {
                    "actor": ACTOR,
                    "scope_path": "org:test",
                    "permissions": ["memory.admin"],
                }
            }
        ),
    )
    create(store)
    headers = {"Authorization": "Bearer test-key", "X-MemoryV4-Reason": "privacy"}
    path = "/applications/knowledge/scrub"
    with TestClient(create_app()) as client:
        body = json.dumps(request().model_dump())
        for invalid in [
            body[:-1] + ',"receipt_id":"' + RECEIPT + '"}',
            json.dumps({**request().model_dump(), "subject": 12}),
            "[]",
            "null",
        ]:
            assert client.post(path, content=invalid, headers=headers).status_code == 422
        assert client.post(path + "?all=true", content=body, headers=headers).status_code == 422
        assert client.post(path, content=body, headers=headers).status_code == 200
    reopened = SqliteStore(store.database_path)
    try:
        with pytest.raises(IdempotencyConflictError):
            create(reopened)
    finally:
        reopened.close()


def test_correction_chain_deletes_newest_first_without_reviving_predecessor(store):
    old, _, _ = create(store, content="preserved historical fact")
    child_id = str(uuid4())
    child, _ = store.supersede_record_idempotent(
        old.id,
        RecordSupersedeRequest(content=SECRET, attrs={**payload().attrs, "receiptId": child_id}),
        actor=ACTOR,
        expected_version=1,
        idempotency_key="child-create",
        request_hash="child",
        reason="verified correction",
    )
    with closing(store._connect()) as conn, conn:
        snapshot = store.get_record(old.id).model_dump(mode="json")
        conn.execute(
            "INSERT INTO idempotency_requests VALUES (?,?,?,?,?,?,?,?)",
            (
                ACTOR,
                "record.patch:" + old.id,
                "old-backlink-cache",
                "hash",
                json.dumps(snapshot),
                200,
                old.id,
                "now",
            ),
        )
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store)
    assert before == logical_dump(store)
    assert scrub(store, request(receipt_id=child_id))["complete"]
    assert store.get_record(child.id) is None
    preserved = store.get_record(old.id)
    assert preserved.content == "preserved historical fact"
    assert preserved.lifecycle.value == "archived"
    assert preserved.superseded_by is None
    assert preserved.version == 3
    assert SECRET not in logical_dump(store)
    assert child.id not in logical_dump(store)
    assert scrub(store)["complete"]
    assert store.get_record(old.id) is None


def test_query_json_cannot_forge_owner_retrieval_binding(store):
    create(store)
    with closing(store._connect()) as conn, conn:
        store.write_retrieval(
            conn,
            query=json.dumps({**payload().attrs, "query": SECRET}),
            scope_path=SCOPE,
            actor=ACTOR,
            result_count=1,
            degraded=False,
        )
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store)
    assert before == logical_dump(store)


def test_owner_retrieval_binding_rejects_cross_scope(store):
    with closing(store._connect()) as conn, conn:
        with pytest.raises(ValueError):
            store.write_retrieval(
                conn,
                query=SECRET,
                scope_path="org:foreign",
                actor=ACTOR,
                result_count=1,
                degraded=False,
                application_binding=request(),
            )
        assert conn.execute("SELECT count(*) FROM retrieval_events").fetchone()[0] == 0


def test_stale_cross_receipt_writer_cannot_copy_erased_reference_after_commit(store):
    record, _, _ = create(store)
    stale_content = json.dumps({"recordId": record.id, "quote": SECRET})
    assert scrub(store)["complete"]
    reopened = SqliteStore(store.database_path)
    try:
        with pytest.raises(IdempotencyConflictError):
            create(reopened, receipt=str(uuid4()), content=stale_content)
        with closing(reopened._connect()) as conn, conn:
            with pytest.raises(IdempotencyConflictError):
                reopened.write_retrieval(
                    conn,
                    query=stale_content,
                    scope_path=SCOPE,
                    actor=ACTOR,
                    result_count=1,
                    degraded=False,
                )
        assert SECRET not in logical_dump(reopened)
    finally:
        reopened.close()


def test_replay_detects_late_unmanaged_copy_by_hashed_reference(store):
    record, _, _ = create(store)
    scrub(store)
    # Simulated out-of-band writer, NOT an approved API. Replay must not hide it.
    with closing(store._connect()) as conn, conn:
        conn.execute(
            "INSERT INTO audit_events(action,object_type,object_id,actor,scope_path,"
            "detail_json,created_at) VALUES (?,?,?,?,?,?,?)",
            ("rogue", "record", record.id, ACTOR, SCOPE, json.dumps({"quote": SECRET}), "now"),
        )
    with pytest.raises(HTTPException):
        scrub(store)


def test_nested_escaped_reference_dependency_is_not_hidden(store):
    record, _, _ = create(store)
    content = json.dumps(json.dumps({"priorIds": [record.id]})).replace("rec_", r"\u0072ec_")
    create(store, receipt=str(uuid4()), content=content)
    with pytest.raises(HTTPException):
        scrub(store)


def test_fts_unknown_copy_fails_closed(store):
    create(store)
    with closing(store._connect()) as conn, conn:
        conn.execute(
            "INSERT INTO records_fts(id,title,content) VALUES (?,?,?)",
            ("rec_" + "f" * 32, "orphan", SECRET),
        )
    with pytest.raises(HTTPException) as exc:
        scrub(store)
    assert exc.value.detail["code"] == "application_scrub_index_inconsistent"


def test_concurrent_scrub_is_atomic_and_idempotent(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    create(store)
    barrier = Barrier(2)

    def erase():
        other = SqliteStore(store.database_path)
        try:
            barrier.wait(timeout=5)
            return scrub(other)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(erase) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert all(result["complete"] for result in results)
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert SECRET not in logical_dump(store)


def test_owned_review_finding_resolution_cache_and_audit_are_scrubbed(store):
    from app.schemas import FindingCreate, FindingResolutionRequest

    record, _, _ = create(store)
    finding = store.create_finding(
        FindingCreate(
            finding_type="candidate",
            subject={"kind": "record", "id": record.id},
            detail={"quote": SECRET},
            scope_path=SCOPE,
        ),
        actor=ACTOR,
    )
    store.resolve_finding_idempotent(
        finding.id,
        FindingResolutionRequest(status="resolved", resolution={"quote": SECRET}),
        actor=ACTOR,
        expected_version=1,
        reason=SECRET,
        idempotency_key="finding-resolution-" + SECRET,
        request_hash=SECRET,
    )
    assert scrub(store)["complete"]
    assert store.get_finding(finding.id) is None
    assert SECRET not in logical_dump(store)
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM review_findings").fetchone()[0] == 0
        hashes = [r[0] for r in conn.execute("SELECT key_hash FROM application_scrub_operations")]
        assert len(hashes) == 2
        assert all(
            len(value) == 64 and all(c in "0123456789abcdef" for c in value) for value in hashes
        )


def test_dependent_relation_with_unresolved_external_reference_blocks(store):
    from app.schemas import RelationCreate

    record, _, _ = create(store)
    store.create_relation_idempotent(
        RelationCreate(
            from_ref={"kind": "record", "id": record.id},
            to_ref={"kind": "record", "id": record.id},
            relation_type="supports",
            scope_path=SCOPE,
            provenance={"artifactId": "art_" + "f" * 32},
        ),
        actor=ACTOR,
        idempotency_key="relation-key",
        request_hash="relation-hash",
    )
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store)
    assert logical_dump(store) == before


def test_erased_receipt_cannot_recreate_bound_retrieval_without_record_reference(store):
    create(store)
    scrub(store)
    with closing(store._connect()) as conn, conn:
        with pytest.raises(IdempotencyConflictError):
            store.write_retrieval(
                conn,
                query=SECRET,
                scope_path=SCOPE,
                actor=ACTOR,
                result_count=0,
                degraded=False,
                application_binding=request(),
            )
        assert conn.execute("SELECT count(*) FROM retrieval_events").fetchone()[0] == 0


def test_explicit_empty_scrub_closes_binding_and_prevents_late_writes(store):
    with pytest.raises(HTTPException) as denied:
        scrub(store)
    assert denied.value.status_code == 403  # Default unknown binding still denies.
    result = scrub(store, request(allow_empty=True))
    assert result == {
        "complete": True,
        "replayed": False,
        "records_scrubbed": 0,
        "erasure": "logical-live-store",
        "physical_erasure": False,
    }
    assert scrub(store)["replayed"] is True
    restarted = SqliteStore(store.database_path)
    try:
        with pytest.raises(IdempotencyConflictError):
            create(restarted)
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "dimension", ["project", "application", "subject", "actor", "permission", "grant"]
)
def test_empty_opt_in_never_bypasses_existing_ownership(store, dimension):
    create(store)
    req, who = request(allow_empty=True), auth()
    if dimension == "project":
        req = request(allow_empty=True, scope_path=SCOPE.replace("a" * 64, "b" * 64))
    elif dimension == "application":
        other = "33333333-3333-4333-8333-333333333333"
        req = request(allow_empty=True, application_id=other, scope_path=SCOPE.replace(APP, other))
    elif dimension == "subject":
        req = request(
            allow_empty=True,
            subject="bob",
            scope_path=SCOPE.replace("subject:alice", "subject:bob"),
        )
    elif dimension == "actor":
        who = auth(actor="unify:other")
    elif dimension == "permission":
        who = auth(admin=False)
    else:
        who = auth(scope="org:other")
    before = logical_dump(store)
    with pytest.raises(HTTPException):
        scrub(store, req, who)
    assert logical_dump(store) == before


def test_empty_scrub_still_denies_unowned_receipt_copy(store):
    create(
        store,
        receipt="33333333-3333-4333-8333-333333333333",
        content="foreign receipt reference " + RECEIPT,
    )
    before = logical_dump(store)
    with pytest.raises(HTTPException) as denied:
        scrub(store, request(allow_empty=True))
    assert denied.value.status_code == 409
    assert logical_dump(store) == before
    with closing(store._connect()) as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='application_scrub_tombstones'"
        ).fetchone()
