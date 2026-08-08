import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.contracts import OPERATIONS
from app.main import create_app
from app.schemas import (
    FindingCreate,
    FindingResolutionRequest,
    ObjectKind,
    ObjectRef,
    RecordCreate,
)
from app.storage import SqliteStore

GRANTS = {
    "worker-key": {
        "actor": "worker:a",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.search", "memory.create"],
    },
    "review-key": {
        "actor": "reviewer:a",
        "scope_path": "org:a",
        "permissions": ["memory.review"],
    },
    "audit-key": {
        "actor": "auditor:a",
        "scope_path": "org:a",
        "permissions": ["memory.audit.read"],
    },
    "admin-key": {
        "actor": "admin:a",
        "scope_path": "org:a",
        "permissions": ["memory.admin"],
    },
    "other-admin-key": {
        "actor": "admin:b",
        "scope_path": "org:b",
        "permissions": ["memory.admin"],
    },
}


def operational_client(tmp_path, monkeypatch):
    db_path = tmp_path / "operations.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps(GRANTS))
    return TestClient(create_app()), SqliteStore(db_path), db_path


def headers(key: str, idem: str | None = None, version: int | None = None, reason=None):
    result = {"Authorization": f"Bearer {key}"}
    if idem is not None:
        result["Idempotency-Key"] = idem
    if version is not None:
        result["If-Match"] = str(version)
    if reason is not None:
        result["X-MemoryV4-Reason"] = reason
    return result


def create_subject(store: SqliteStore, *, scope="org:a/project:p", title="subject"):
    return store.create_record(
        RecordCreate(
            title=title,
            content=f"review content {title}",
            role="active",
            lifecycle="working",
            scope_path=scope,
        ),
        actor="worker:a",
    )


def create_finding(
    store: SqliteStore,
    record_id: str,
    *,
    kind="candidate",
    scope="org:a/project:p",
):
    return store.create_finding(
        FindingCreate(
            finding_type=kind,
            subject=ObjectRef(kind=ObjectKind("record"), id=record_id),
            detail={"signal": kind},
            scope_path=scope,
        ),
        actor="worker:health",
    )


def test_findings_are_scoped_filterable_paginated_and_permissioned(tmp_path, monkeypatch):
    client, store, _ = operational_client(tmp_path, monkeypatch)
    subject = create_subject(store)
    first = create_finding(store, subject.id, kind="candidate")
    second = create_finding(store, subject.id, kind="stale")
    third = create_finding(store, subject.id, kind="health")
    sibling_subject = create_subject(store, scope="org:b/project:p", title="sibling")
    sibling = create_finding(store, sibling_subject.id, scope="org:b/project:p")

    denied = client.get("/review/findings", headers=headers("worker-key"))
    outside = client.get(
        "/review/findings",
        params={"scope_path": "org:b/project:p"},
        headers=headers("review-key"),
    )
    assert denied.status_code == outside.status_code == 403

    page_one = client.get(
        "/review/findings",
        params={"scope_path": "org:a/project:p", "limit": 2},
        headers=headers("review-key"),
    )
    assert page_one.status_code == 200
    assert len(page_one.json()["findings"]) == 2
    cursor = page_one.json()["next_cursor"]
    assert cursor
    page_two = client.get(
        "/review/findings",
        params={"scope_path": "org:a/project:p", "limit": 2, "cursor": cursor},
        headers=headers("review-key"),
    )
    listed_ids = {
        item["id"] for item in page_one.json()["findings"] + page_two.json()["findings"]
    }
    assert listed_ids == {first.id, second.id, third.id}
    assert sibling.id not in listed_ids

    stale = client.get(
        "/review/findings",
        params={
            "scope_path": "org:a/project:p",
            "finding_type": "stale",
            "status": "open",
            "subject_kind": "record",
            "subject_id": subject.id,
        },
        headers=headers("review-key"),
    )
    assert [item["id"] for item in stale.json()["findings"]] == [second.id]
    mismatched_cursor = client.get(
        "/review/findings",
        params={
            "scope_path": "org:a/project:p",
            "finding_type": "health",
            "limit": 2,
            "cursor": cursor,
        },
        headers=headers("review-key"),
    )
    assert mismatched_cursor.status_code == 422


def test_finding_resolution_is_governed_idempotent_versioned_and_audited(tmp_path, monkeypatch):
    client, store, _ = operational_client(tmp_path, monkeypatch)
    subject = create_subject(store)
    finding = create_finding(store, subject.id, kind="contradiction")

    endpoint = f"/review/findings/{finding.id}/resolve"
    body = {"status": "resolved", "resolution": {"record_id": subject.id}}
    assert client.post(endpoint, json=body, headers=headers("review-key")).status_code == 428
    assert (
        client.post(
            endpoint,
            json=body,
            headers=headers("review-key", "resolve-missing-version", reason="reviewed"),
        ).status_code
        == 428
    )
    assert (
        client.post(
            endpoint,
            json=body,
            headers=headers("review-key", "resolve-missing-reason", 1),
        ).status_code
        == 422
    )
    stale = client.post(
        endpoint,
        json=body,
        headers=headers("review-key", "resolve-stale-version", 9, "reviewed"),
    )
    assert stale.status_code == 412

    mutation_headers = headers(
        "review-key", "resolve-finding-exactly-once", 1, "contradiction reconciled"
    )
    resolved = client.post(endpoint, json=body, headers=mutation_headers)
    replay = client.post(endpoint, json=body, headers=mutation_headers)
    assert resolved.status_code == replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    payload = resolved.json()
    assert payload["status"] == "resolved"
    assert payload["resolution"] == {"record_id": subject.id}
    assert payload["resolved_by_actor"] == "reviewer:a"
    assert payload["resolved_at"] is not None
    assert payload["version"] == 2

    conflict = client.post(
        endpoint,
        json={"status": "dismissed", "resolution": {"note": "different"}},
        headers=mutation_headers,
    )
    already_closed = client.post(
        endpoint,
        json=body,
        headers=headers("review-key", "resolve-finding-again", 2, "second closure"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert already_closed.status_code == 409

    audit = client.get(
        "/audit/events",
        params={
            "scope_path": "org:a/project:p",
            "action": "finding.resolve",
            "object_id": finding.id,
        },
        headers=headers("audit-key"),
    )
    assert audit.status_code == 200
    assert len(audit.json()["events"]) == 1
    detail = audit.json()["events"][0]["detail"]
    assert detail["reason"] == "contradiction reconciled"
    assert "resolution" not in detail


def test_resolution_hides_sibling_findings_and_audits_denial(tmp_path, monkeypatch):
    client, store, _ = operational_client(tmp_path, monkeypatch)
    subject = create_subject(store, scope="org:b/project:p", title="sibling")
    finding = create_finding(store, subject.id, scope="org:b/project:p")
    response = client.post(
        f"/review/findings/{finding.id}/resolve",
        json={"status": "dismissed", "resolution": {}},
        headers=headers("review-key", "sibling-resolution-denied", 1, "must not leak"),
    )
    assert response.status_code == 404
    assert store.get_finding(finding.id).status.value == "open"


def test_audit_and_retrieval_event_apis_filter_scope_time_and_cursors(tmp_path, monkeypatch):
    client, store, _ = operational_client(tmp_path, monkeypatch)
    subject = create_subject(store)
    finding = create_finding(store, subject.id)
    sibling = create_subject(store, scope="org:b/project:p", title="sibling")
    create_finding(store, sibling.id, scope="org:b/project:p")

    search = client.get(
        "/search",
        params={"q": "review", "scope_path": "org:a/project:p"},
        headers=headers("worker-key"),
    )
    assert search.status_code == 200

    assert client.get("/audit/events", headers=headers("review-key")).status_code == 403
    audit_page = client.get(
        "/audit/events",
        params={"scope_path": "org:a/project:p", "limit": 1},
        headers=headers("audit-key"),
    )
    assert audit_page.status_code == 200
    assert len(audit_page.json()["events"]) == 1
    cursor = audit_page.json()["next_cursor"]
    assert cursor
    second_page = client.get(
        "/audit/events",
        params={"scope_path": "org:a/project:p", "limit": 1, "cursor": cursor},
        headers=headers("audit-key"),
    )
    assert second_page.status_code == 200
    assert all(event["scope_path"] != "org:b/project:p" for event in second_page.json()["events"])

    filtered = client.get(
        "/audit/events",
        params={
            "scope_path": "org:a/project:p",
            "action": "finding.create",
            "object_type": "finding",
            "object_id": finding.id,
            "actor": "worker:health",
            "from_time": "2020-01-01T00:00:00Z",
            "to_time": "2030-01-01T00:00:00Z",
        },
        headers=headers("audit-key"),
    )
    assert [event["object_id"] for event in filtered.json()["events"]] == [finding.id]
    bad_window = client.get(
        "/audit/events",
        params={"from_time": "2030-01-01T00:00:00Z", "to_time": "2020-01-01T00:00:00Z"},
        headers=headers("audit-key"),
    )
    assert bad_window.status_code == 422

    retrieval = client.get(
        "/retrieval-events",
        params={
            "scope_path": "org:a/project:p",
            "actor": "worker:a",
            "degraded": False,
            "query_contains": "review",
        },
        headers=headers("audit-key"),
    )
    assert retrieval.status_code == 200
    assert len(retrieval.json()["events"]) == 1
    assert retrieval.json()["events"][0]["query"] == "review"
    assert retrieval.json()["events"][0]["degraded"] is False


def test_usage_requires_admin_and_returns_governed_counts(tmp_path, monkeypatch):
    client, store, _ = operational_client(tmp_path, monkeypatch)
    subject = create_subject(store)
    finding = create_finding(store, subject.id, kind="health")
    client.get(
        "/search",
        params={"q": "review", "scope_path": "org:a/project:p"},
        headers=headers("worker-key"),
    )
    denied = client.get("/usage", headers=headers("audit-key"))
    outside = client.get(
        "/usage", params={"scope_path": "org:b/project:p"}, headers=headers("admin-key")
    )
    assert denied.status_code == outside.status_code == 403

    usage = client.get(
        "/usage",
        params={
            "scope_path": "org:a/project:p",
            "from_time": "2020-01-01T00:00:00Z",
            "to_time": "2030-01-01T00:00:00Z",
        },
        headers=headers("admin-key"),
    )
    assert usage.status_code == 200
    payload = usage.json()
    assert payload["scope_path"] == "org:a/project:p"
    assert payload["objects"]["records"]["total"] == 1
    assert payload["objects"]["records"]["by_lifecycle"] == {"working": 1}
    assert payload["objects"]["findings"]["total"] == 1
    assert payload["objects"]["findings"]["by_status"] == {"open": 1}
    assert payload["events"]["retrieval_events"] == 1
    assert payload["events"]["audit_events"] >= 2
    assert finding.id


def test_concurrent_finding_resolution_commits_once(tmp_path):
    store = SqliteStore(tmp_path / "concurrent-review.sqlite3")
    subject = create_subject(store, scope="org:a")
    finding = create_finding(store, subject.id, scope="org:a")
    resolution = FindingResolutionRequest(status="resolved", resolution={"note": "verified"})

    def resolve_once(_index):
        return store.resolve_finding_idempotent(
            finding.id,
            resolution,
            actor="reviewer:a",
            expected_version=1,
            reason="concurrent review",
            idempotency_key="concurrent-finding-resolution",
            request_hash="same-resolution-hash",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(resolve_once, range(8)))
    assert {item.id for item, _ in results} == {finding.id}
    assert sum(not replayed for _, replayed in results) == 1
    assert store.count_audit_events(action="finding.resolve") == 1
    assert store.get_finding(finding.id).version == 2


def test_review_migration_integrity_and_capability_truth(tmp_path, monkeypatch):
    client, _, db_path = operational_client(tmp_path, monkeypatch)
    capabilities = client.get("/capabilities", headers=headers("admin-key")).json()
    operations = {(item["method"], item["path"]): item for item in capabilities["operations"]}
    required = {
        ("GET", "/review/findings"),
        ("POST", "/review/findings/{id}/resolve"),
        ("GET", "/audit/events"),
        ("GET", "/retrieval-events"),
        ("GET", "/usage"),
    }
    assert all(operations[key]["status"] == "implemented" for key in required)
    assert all(operation.status.value == "implemented" for operation in OPERATIONS)

    with sqlite3.connect(db_path) as conn:
        migrations = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
        assert "0005_review_audit_operations" in migrations
        try:
            conn.execute(
                """
                INSERT INTO review_findings(
                  id,finding_type,status,subject_kind,subject_id,detail_json,resolution_json,
                  scope_path,created_by_actor,resolved_by_actor,resolved_at,version,created_at,updated_at
                ) VALUES (
                  'fnd_bad','health','open','record','rec_missing','{}','{}','org:a','worker',
                  NULL,NULL,1,'now','now'
                )
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("database accepted inconsistent open finding resolution state")
