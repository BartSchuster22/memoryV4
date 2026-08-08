import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.migrations import MIGRATIONS, migrate, rollback_all
from app.schemas import Lifecycle, RecordCreate, Role, ScopePath
from app.storage import SqliteStore


def client_for(
    tmp_path,
    monkeypatch,
    keys=(
        '{"tenant-a-key":{"actor":"tenant-a","scope_path":"org:a",'
        '"permissions":["memory.admin"]},'
        '"tenant-b-key":{"actor":"tenant-b","scope_path":"org:b",'
        '"permissions":["memory.admin"]}}'
    ),
):
    db_path = tmp_path / "memoryv4.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", keys)
    return TestClient(create_app()), db_path


def auth(key="tenant-a-key"):
    return {
        "Authorization": f"Bearer {key}",
        "Idempotency-Key": f"foundation-{key}",
    }


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
        "idempotency_requests",
        "schema_migrations",
    } <= tables
    with sqlite3.connect(db_path) as conn:
        record_columns = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    assert {
        "write_policy", "version", "previous_lifecycle", "lifecycle_changed_at"
    } <= record_columns
    rollback_all(db_path)
    with sqlite3.connect(db_path) as conn:
        remaining = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "records" not in remaining


def test_governance_migration_upgrades_existing_foundation_records(tmp_path) -> None:
    db_path = tmp_path / "foundation-upgrade.sqlite3"
    with sqlite3.connect(db_path) as conn:
        MIGRATIONS[0].up(conn)
        conn.execute(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0001_foundation", "2026-08-08T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO records(
              id, title, content, role, lifecycle, scope_path, entity_type, topic,
              source_refs_json, provenance_json, attrs_json, author_actor,
              superseded_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rec_existing",
                "Existing",
                "Foundation content",
                "active",
                "working",
                "org:a",
                None,
                None,
                "[]",
                "{}",
                "{}",
                "user:existing",
                None,
                "2026-08-08T00:00:00+00:00",
                "2026-08-08T00:00:00+00:00",
            ),
        )

    result = migrate(db_path)
    assert result.applied == [
        "0002_governance", "0003_core_objects", "0004_record_lifecycle"
    ]
    with sqlite3.connect(db_path) as conn:
        upgraded = conn.execute(
            "SELECT write_policy, version FROM records WHERE id = 'rec_existing'"
        ).fetchone()
    assert upgraded == ("author_only", 1)


def test_core_object_migration_preserves_foundation_graph_rows(tmp_path) -> None:
    db_path = tmp_path / "core-upgrade.sqlite3"
    with sqlite3.connect(db_path) as conn:
        MIGRATIONS[0].up(conn)
        MIGRATIONS[1].up(conn)
        conn.execute(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            [
                ("0001_foundation", "2026-08-08T00:00:00+00:00"),
                ("0002_governance", "2026-08-08T00:00:01+00:00"),
            ],
        )
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-entity",
                "Folder",
                "Legacy",
                "org:a",
                "{}",
                "2026-08-08T00:00:00+00:00",
                "2026-08-08T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "rel_legacy",
                "rec_a",
                "rec_b",
                "supports",
                "org:a",
                "{}",
                "user:legacy",
                "2026-08-08T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "art_legacy",
                None,
                "document",
                "s3://legacy/file",
                "sha256:abc",
                "org:a",
                "{}",
                "user:legacy",
                "2026-08-08T00:00:00+00:00",
            ),
        )

    assert migrate(db_path).applied == ["0003_core_objects", "0004_record_lifecycle"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        entity = conn.execute(
            "SELECT * FROM entities WHERE entity_type='Folder' AND id='legacy-entity'"
        ).fetchone()
        relation = conn.execute("SELECT * FROM relations WHERE id='rel_legacy'").fetchone()
        artifact = conn.execute("SELECT * FROM artifacts WHERE id='art_legacy'").fetchone()
        record_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()
        }
    assert entity["version"] == 1
    assert relation["from_kind"] == "record"
    assert relation["to_kind"] == "record"
    assert relation["version"] == 1
    assert artifact["record_id"] is None
    assert artifact["version"] == 1
    assert {"entity_id", "tags_json", "confidence", "supersedes", "deleted_at"} <= record_columns


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
