import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.migrations import migrate, rollback_all
from app.schemas import Lifecycle, RecordCreate, Role, ScopePath
from app.storage import SqliteStore


def client_for(tmp_path, monkeypatch, keys='{"tenant-a-key":"org:a","tenant-b-key":"org:b"}'):
    db_path = tmp_path / "memoryv4.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", keys)
    return TestClient(create_app()), db_path


def auth(key="tenant-a-key"):
    return {"Authorization": f"Bearer {key}"}


def test_record_validators_reject_bad_role_lifecycle_and_scope() -> None:
    with pytest.raises(ValueError):
        RecordCreate(
            title="x", content="y", role="draft", lifecycle=Lifecycle.working, scope_path="global"
        )
    with pytest.raises(ValueError):
        RecordCreate(
            title="x", content="y", role=Role.active, lifecycle="deleted", scope_path="global"
        )
    with pytest.raises(ValueError):
        ScopePath.validate("org:a//project:b")
    assert ScopePath.ancestors("org:a/project:p/agent:alice") == [
        "global",
        "org:a",
        "org:a/project:p",
        "org:a/project:p/agent:alice",
    ]


def test_migrations_are_idempotent_and_reversible(tmp_path) -> None:
    db_path = tmp_path / "memoryv4.sqlite3"
    first = migrate(db_path)
    second = migrate(db_path)
    assert first.applied
    assert second.applied == []
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "records",
        "entities",
        "relations",
        "artifacts",
        "audit_events",
        "retrieval_events",
        "schema_migrations",
    } <= tables
    rollback_all(db_path)
    with sqlite3.connect(db_path) as conn:
        remaining = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "records" not in remaining


def test_store_crud_audit_search_and_scope_isolation(tmp_path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    acme = store.create_record(
        RecordCreate(
            title="Acme decision",
            content="Credits never expire",
            role=Role.canonical,
            lifecycle=Lifecycle.live,
            scope_path="org:acme/project:psi",
        ),
        actor="tester",
    )
    sibling = store.create_record(
        RecordCreate(
            title="Sibling decision",
            content="Credits expire yearly",
            role=Role.canonical,
            lifecycle=Lifecycle.live,
            scope_path="org:other/project:psi",
        ),
        actor="tester",
    )
    child = store.create_record(
        RecordCreate(
            title="Child evidence",
            content="session evidence",
            role=Role.evidence,
            lifecycle=Lifecycle.working,
            scope_path="org:acme/project:psi/user:u1",
        ),
        actor="tester",
    )

    visible = store.list_records(scope_path="org:acme/project:psi/user:u1")
    assert {record.id for record in visible} == {acme.id, child.id}
    assert sibling.id not in {record.id for record in visible}

    found = store.search_records(
        "Credits", scope_path="org:acme/project:psi/user:u1", actor="tester"
    )
    assert [result.record.id for result in found] == [acme.id]
    assert store.count_audit_events(action="record.create") == 3
    assert store.count_retrieval_events(query="Credits") == 1


def test_health_public_but_records_require_scoped_auth(tmp_path, monkeypatch) -> None:
    client, _ = client_for(tmp_path, monkeypatch)
    assert client.get("/health").status_code == 200
    assert client.get("/records").status_code == 401
    assert client.get("/records", headers=auth()).status_code == 200


def test_api_enforces_key_scope_and_negative_sibling_leak(tmp_path, monkeypatch) -> None:
    client, _ = client_for(tmp_path, monkeypatch)
    create_a = client.post(
        "/records",
        headers=auth("tenant-a-key"),
        json={
            "title": "A",
            "content": "tenant a secret",
            "role": "canonical",
            "lifecycle": "live",
            "scope_path": "org:a/project:p",
        },
    )
    assert create_a.status_code == 201
    create_b = client.post(
        "/records",
        headers=auth("tenant-b-key"),
        json={
            "title": "B",
            "content": "tenant b secret",
            "role": "canonical",
            "lifecycle": "live",
            "scope_path": "org:b/project:p",
        },
    )
    assert create_b.status_code == 201

    forbidden = client.post(
        "/records",
        headers=auth("tenant-a-key"),
        json={
            "title": "bad",
            "content": "bad",
            "role": "active",
            "lifecycle": "working",
            "scope_path": "org:b/project:p",
        },
    )
    assert forbidden.status_code == 403

    listing = client.get(
        "/records?scope_path=org:a/project:p/user:u1", headers=auth("tenant-a-key")
    )
    assert listing.status_code == 200
    payload = listing.json()["records"]
    assert [item["title"] for item in payload] == ["A"]

    search = client.get(
        "/search?q=secret&scope_path=org:a/project:p/user:u1", headers=auth("tenant-a-key")
    )
    assert search.status_code == 200
    assert [item["record"]["title"] for item in search.json()["results"]] == ["A"]
