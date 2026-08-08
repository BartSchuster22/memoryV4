import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

GRANTS = {
    "admin": {
        "actor": "admin:core",
        "scope_path": "global",
        "permissions": ["memory.admin"],
    },
    "writer": {
        "actor": "user:writer",
        "scope_path": "org:a",
        "permissions": [
            "memory.read",
            "memory.search",
            "memory.create",
            "memory.edit",
        ],
    },
    "sibling": {
        "actor": "user:sibling",
        "scope_path": "org:b",
        "permissions": ["memory.read", "memory.search", "memory.create", "memory.edit"],
    },
}


def auth(token: str = "writer", **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


@pytest.fixture
def core_client(tmp_path, monkeypatch) -> tuple[TestClient, object]:
    db_path = tmp_path / "core.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps(GRANTS))
    return TestClient(create_app()), db_path


def create_entity(
    client: TestClient,
    *,
    entity_type: str = "Folder",
    entity_id: str = "shared-id",
    name: str = "Alpha",
    scope_path: str = "org:a",
    key: str = "entity-key",
    token: str = "writer",
):
    return client.post(
        "/entities",
        headers=auth(token, **{"Idempotency-Key": key}),
        json={
            "entity_type": entity_type,
            "id": entity_id,
            "name": name,
            "scope_path": scope_path,
            "attrs": {"rank": 1},
        },
    )


def create_record(
    client: TestClient,
    *,
    key: str,
    title: str,
    entity_type: str = "Folder",
    entity_id: str = "shared-id",
    scope_path: str = "org:a",
):
    return client.post(
        "/records",
        headers=auth(**{"Idempotency-Key": key}),
        json={
            "title": title,
            "content": f"Memory content for {title}",
            "role": "evidence",
            "lifecycle": "working",
            "scope_path": scope_path,
            "entity": {"entity_type": entity_type, "id": entity_id},
            "tags": ["core", "verified"],
            "confidence": 0.91,
            "topic": "objects",
        },
    )


def test_entity_composite_identity_pagination_update_and_visibility(core_client) -> None:
    client, _ = core_client
    first = create_entity(client)
    assert first.status_code == 201, first.text
    assert first.json()["version"] == 1

    replay = create_entity(client)
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == first.json()

    conflict = create_entity(client, name="Different")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    same_id_other_type = create_entity(
        client,
        entity_type="Collection",
        name="Collection Alpha",
        key="entity-key-2",
    )
    assert same_id_other_type.status_code == 201, same_id_other_type.text

    page1 = client.get(
        "/entities?limit=1&sort=name&order=asc", headers=auth()
    )
    assert page1.status_code == 200
    assert len(page1.json()["entities"]) == 1
    cursor = page1.json()["next_cursor"]
    assert cursor
    page2 = client.get(
        "/entities",
        headers=auth(),
        params={"cursor": cursor, "limit": 1, "sort": "name", "order": "asc"},
    )
    assert page2.status_code == 200
    assert page2.json()["entities"][0]["entity_type"] != page1.json()["entities"][0][
        "entity_type"
    ]
    mismatched = client.get(
        "/entities",
        headers=auth(),
        params={"cursor": cursor, "sort": "updated_at"},
    )
    assert mismatched.status_code == 422

    patched = client.patch(
        "/entities/Folder/shared-id",
        headers=auth(**{"Idempotency-Key": "entity-patch", "If-Match": "1"}),
        json={"name": "Renamed", "attrs": {"rank": 2}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["version"] == 2
    stale = client.patch(
        "/entities/Folder/shared-id",
        headers=auth(**{"Idempotency-Key": "entity-patch-stale", "If-Match": "1"}),
        json={"name": "Stale"},
    )
    assert stale.status_code == 412

    assert client.get("/entities/Folder/shared-id", headers=auth("sibling")).status_code == 404


def test_record_relation_artifact_search_and_context_graph(core_client) -> None:
    client, db_path = core_client
    assert create_entity(client).status_code == 201
    record1 = create_record(client, key="record-1", title="Core object alpha")
    record2 = create_record(client, key="record-2", title="Core object beta")
    assert record1.status_code == 201, record1.text
    assert record2.status_code == 201, record2.text
    record_id = record1.json()["id"]

    records = client.get(
        "/records",
        headers=auth(),
        params={
            "entity_type": "Folder",
            "entity_id": "shared-id",
            "tag": "verified",
            "min_confidence": "0.9",
            "sort": "title",
            "order": "asc",
            "limit": 1,
        },
    )
    assert records.status_code == 200, records.text
    assert len(records.json()["records"]) == 1
    assert records.json()["next_cursor"]

    patched = client.patch(
        f"/records/{record_id}",
        headers=auth(**{"Idempotency-Key": "record-patch", "If-Match": "1"}),
        json={"tags": ["core", "updated"], "confidence": 0.97},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["tags"] == ["core", "updated"]
    assert patched.json()["version"] == 2

    relation = client.post(
        "/relations",
        headers=auth(**{"Idempotency-Key": "relation-1"}),
        json={
            "from": {"kind": "entity", "entity_type": "Folder", "id": "shared-id"},
            "to": {"kind": "record", "id": record_id},
            "relation_type": "contains",
            "scope_path": "org:a",
            "provenance": {"source": "test"},
        },
    )
    assert relation.status_code == 201, relation.text
    assert relation.json()["from"]["kind"] == "entity"
    assert relation.json()["to"]["id"] == record_id
    listed_relations = client.get(
        "/relations", headers=auth(), params={"from_kind": "entity", "from_id": "shared-id"}
    )
    assert [item["id"] for item in listed_relations.json()["relations"]] == [
        relation.json()["id"]
    ]

    artifact = client.post(
        "/artifacts",
        headers=auth(**{"Idempotency-Key": "artifact-1"}),
        json={
            "record_id": record_id,
            "entity": {"entity_type": "Folder", "id": "shared-id"},
            "artifact_type": "document",
            "uri": "s3://private-bucket/core.pdf",
            "checksum": "sha256:abcdef0123456789",
            "scope_path": "org:a",
            "provenance": {"scanner": "qa"},
        },
    )
    assert artifact.status_code == 201, artifact.text
    artifacts = client.get(
        "/artifacts",
        headers=auth(),
        params={"entity_type": "Folder", "entity_id": "shared-id"},
    )
    assert artifacts.status_code == 200
    assert [item["id"] for item in artifacts.json()["artifacts"]] == [artifact.json()["id"]]

    search = client.get(
        "/search",
        headers=auth(),
        params={"q": "Core object", "entity_type": "Folder", "entity_id": "shared-id", "limit": 1},
    )
    assert search.status_code == 200, search.text
    assert len(search.json()["results"]) == 1
    assert search.json()["next_cursor"]

    context = client.get("/context/Folder/shared-id", headers=auth())
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["entity"]["id"] == "shared-id"
    assert {item["id"] for item in body["records"]} == {record1.json()["id"], record2.json()["id"]}
    assert [item["id"] for item in body["relations"]] == [relation.json()["id"]]
    assert [item["id"] for item in body["artifacts"]] == [artifact.json()["id"]]

    with sqlite3.connect(db_path) as conn:
        actions = {row[0] for row in conn.execute("SELECT action FROM audit_events")}
    assert {
        "entity.create",
        "record.create",
        "record.patch",
        "relation.create",
        "artifact.create",
    } <= actions


def test_reference_integrity_and_scope_boundaries(core_client) -> None:
    client, _ = core_client
    assert create_entity(client).status_code == 201
    sibling = create_entity(
        client,
        entity_id="sibling-id",
        scope_path="org:b",
        key="sibling-entity",
        token="sibling",
    )
    assert sibling.status_code == 201

    missing_record_link = create_record(
        client,
        key="orphan-record",
        title="Orphan",
        entity_id="missing",
    )
    assert missing_record_link.status_code == 404

    cross_scope_relation = client.post(
        "/relations",
        headers=auth(**{"Idempotency-Key": "cross-edge"}),
        json={
            "from": {"kind": "entity", "entity_type": "Folder", "id": "shared-id"},
            "to": {"kind": "entity", "entity_type": "Folder", "id": "sibling-id"},
            "relation_type": "crosses",
            "scope_path": "org:a",
        },
    )
    assert cross_scope_relation.status_code == 404

    invalid_artifact = client.post(
        "/artifacts",
        headers=auth(**{"Idempotency-Key": "no-target"}),
        json={
            "artifact_type": "document",
            "uri": "s3://bucket/no-target",
            "checksum": "sha256:abcdef",
            "scope_path": "org:a",
        },
    )
    assert invalid_artifact.status_code == 422

    orphan_artifact = client.post(
        "/artifacts",
        headers=auth(**{"Idempotency-Key": "orphan-artifact"}),
        json={
            "record_id": "rec_missing",
            "artifact_type": "document",
            "uri": "s3://bucket/missing",
            "checksum": "sha256:abcdef",
            "scope_path": "org:a",
        },
    )
    assert orphan_artifact.status_code == 404

    outside_context = client.get("/context/Folder/shared-id", headers=auth("sibling"))
    assert outside_context.status_code == 404
