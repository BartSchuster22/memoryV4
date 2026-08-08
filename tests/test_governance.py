import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import Lifecycle, RecordCreate, Role
from app.storage import SqliteStore

GRANTS = {
    "worker-key": {
        "actor": "agent:worker",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.search", "memory.create-working"],
    },
    "creator-key": {
        "actor": "user:creator",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.search", "memory.create", "memory.edit"],
    },
    "editor-key": {
        "actor": "user:editor",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.edit"],
    },
    "promoter-key": {
        "actor": "user:verifier",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.promote"],
    },
    "publisher-key": {
        "actor": "user:publisher",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.create", "memory.promote"],
    },
    "reader-key": {
        "actor": "user:reader",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.search"],
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
    "gateway-key": {
        "actor": "gateway:unify",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.create"],
        "allow_actor_delegation": True,
    },
}


def governance_client(tmp_path, monkeypatch, grants=None):
    db_path = tmp_path / "governance.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps(grants or GRANTS))
    return TestClient(create_app()), db_path


def auth(key: str, idempotency_key: str | None = None, **extra):
    result = {"Authorization": f"Bearer {key}", **extra}
    if idempotency_key is not None:
        result["Idempotency-Key"] = idempotency_key
    return result


def record_payload(
    title: str,
    *,
    scope_path: str = "org:a/project:p",
    role: str = "active",
    lifecycle: str = "working",
    write_policy: str = "author_only",
):
    return {
        "title": title,
        "content": f"content for {title}",
        "role": role,
        "lifecycle": lifecycle,
        "write_policy": write_policy,
        "scope_path": scope_path,
    }


def create_record(client, key: str, idem: str, title: str, **payload_overrides):
    payload = record_payload(title, **payload_overrides)
    response = client.post("/records", headers=auth(key, idem), json=payload)
    assert response.status_code == 201, response.text
    return response


def test_action_permissions_and_canonical_creation_boundary(tmp_path, monkeypatch) -> None:
    client, db_path = governance_client(tmp_path, monkeypatch)

    denied_read_only = client.post(
        "/records",
        headers=auth("reader-key", "reader-denied-0001"),
        json=record_payload("reader candidate"),
    )
    assert denied_read_only.status_code == 403
    assert denied_read_only.json()["error"]["details"] == {
        "required_permission": "memory.create"
    }

    candidate = create_record(client, "worker-key", "worker-create-0001", "worker candidate")
    assert candidate.json()["author_actor"] == "agent:worker"
    assert candidate.json()["write_policy"] == "author_only"

    wrong_worker_state = client.post(
        "/records",
        headers=auth("worker-key", "worker-create-0002"),
        json=record_payload("worker live", lifecycle="live"),
    )
    assert wrong_worker_state.status_code == 403

    ordinary_canonical = client.post(
        "/records",
        headers=auth("creator-key", "creator-canonical-0001"),
        json=record_payload("ordinary canonical", role="canonical", lifecycle="live"),
    )
    assert ordinary_canonical.status_code == 403
    assert ordinary_canonical.json()["error"]["details"] == {
        "required_permission": "memory.promote"
    }

    canonical = create_record(
        client,
        "publisher-key",
        "publisher-canonical-0001",
        "verified canonical",
        role="canonical",
        lifecycle="live",
    )
    assert canonical.json()["role"] == "canonical"

    store = SqliteStore(db_path)
    assert store.count_audit_events(action="record.create") == 2
    assert store.count_audit_events(action="request.denied") == 3


def test_legacy_scope_only_keys_are_least_privileged(tmp_path, monkeypatch) -> None:
    client, _ = governance_client(tmp_path, monkeypatch, {"legacy-secret": "org:a"})

    candidate = client.post(
        "/records",
        headers=auth("legacy-secret", "legacy-working-0001"),
        json=record_payload("legacy candidate"),
    )
    assert candidate.status_code == 201
    assert candidate.json()["author_actor"].startswith("api-key:")
    assert "legacy-secret" not in candidate.json()["author_actor"]

    canonical = client.post(
        "/records",
        headers=auth("legacy-secret", "legacy-canonical-0001"),
        json=record_payload("legacy canonical", role="canonical", lifecycle="live"),
    )
    assert canonical.status_code == 403


def test_authenticated_gateway_actor_delegation_is_explicit(tmp_path, monkeypatch) -> None:
    client, _ = governance_client(tmp_path, monkeypatch)

    missing_actor = client.post(
        "/records",
        headers=auth("gateway-key", "gateway-missing-actor"),
        json=record_payload("missing actor"),
    )
    assert missing_actor.status_code == 422

    delegated = client.post(
        "/records",
        headers=auth(
            "gateway-key",
            "gateway-delegated-01",
            **{"X-MemoryV4-Actor": "user:alice"},
        ),
        json=record_payload("delegated actor"),
    )
    assert delegated.status_code == 201
    assert delegated.json()["author_actor"] == "user:alice"

    forbidden_delegation = client.get(
        "/records",
        headers=auth("reader-key", **{"X-MemoryV4-Actor": "user:forged"}),
    )
    assert forbidden_delegation.status_code == 403


def test_idempotency_actor_derivation_and_denial_audit(tmp_path, monkeypatch) -> None:
    client, db_path = governance_client(tmp_path, monkeypatch)
    payload = record_payload("idempotent candidate")
    headers = auth("creator-key", "creator-idempotent-0001")

    first = client.post("/records", headers=headers, json=payload)
    replay = client.post("/records", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"

    conflict = client.post(
        "/records",
        headers=headers,
        json={**payload, "title": "different request"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    missing = client.post(
        "/records",
        headers=auth("creator-key"),
        json=record_payload("missing key"),
    )
    assert missing.status_code == 428

    spoof = client.post(
        "/records",
        headers=auth("creator-key", "creator-spoof-0001"),
        json={**record_payload("spoof"), "author_actor": "admin:forged"},
    )
    assert spoof.status_code == 422
    assert "admin:forged" not in spoof.text

    with sqlite3.connect(db_path) as conn:
        events = conn.execute(
            "SELECT actor, detail_json FROM audit_events ORDER BY id"
        ).fetchall()
        idempotency_count = conn.execute(
            "SELECT count(*) FROM idempotency_requests"
        ).fetchone()[0]
    assert idempotency_count == 1
    assert any(actor == "user:creator" for actor, _ in events)
    serialized = json.dumps(events)
    assert "creator-key" not in serialized
    assert "admin:forged" not in serialized


def test_concurrent_idempotency_claim_creates_once(tmp_path) -> None:
    store = SqliteStore(tmp_path / "concurrent.sqlite3")
    request = RecordCreate(
        title="Concurrent",
        content="one durable record",
        role=Role.active,
        lifecycle=Lifecycle.working,
        scope_path="org:a",
    )

    def create_once(_index: int):
        return store.create_record_idempotent(
            request,
            actor="agent:concurrent",
            idempotency_key="concurrent-create-0001",
            request_hash="same-canonical-request-hash",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create_once, range(8)))

    assert len({record.id for record, _ in results}) == 1
    assert sum(not replayed for _, replayed in results) == 1
    assert store.count_audit_events(action="record.create") == 1


def test_unified_visibility_for_list_get_and_search(tmp_path, monkeypatch) -> None:
    client, _ = governance_client(tmp_path, monkeypatch)
    parent = create_record(
        client,
        "admin-key",
        "scope-parent-0001",
        "parent knowledge",
        scope_path="org:a/project:p",
    ).json()
    child = create_record(
        client,
        "admin-key",
        "scope-child-0001",
        "child knowledge",
        scope_path="org:a/project:p/user:u1",
    ).json()
    sibling = create_record(
        client,
        "other-admin-key",
        "scope-sibling-0001",
        "sibling secret",
        scope_path="org:b/project:p/user:u1",
    ).json()

    listing = client.get(
        "/records",
        params={"scope_path": "org:a/project:p/user:u1"},
        headers=auth("reader-key"),
    )
    assert listing.status_code == 200
    assert {record["id"] for record in listing.json()["records"]} == {parent["id"], child["id"]}

    direct_parent = client.get(
        f"/records/{parent['id']}",
        params={"scope_path": "org:a/project:p/user:u1"},
        headers=auth("reader-key"),
    )
    direct_child = client.get(
        f"/records/{child['id']}",
        params={"scope_path": "org:a/project:p/user:u1"},
        headers=auth("reader-key"),
    )
    hidden_sibling = client.get(
        f"/records/{sibling['id']}",
        params={"scope_path": "org:a/project:p/user:u1"},
        headers=auth("reader-key"),
    )
    assert direct_parent.status_code == direct_child.status_code == 200
    assert hidden_sibling.status_code == 404

    search = client.get(
        "/search",
        params={"q": "knowledge", "scope_path": "org:a/project:p/user:u1"},
        headers=auth("reader-key"),
    )
    assert {item["record"]["id"] for item in search.json()["results"]} == {
        parent["id"],
        child["id"],
    }

    outside_request = client.get(
        "/records",
        params={"scope_path": "org:b/project:p"},
        headers=auth("reader-key"),
    )
    assert outside_request.status_code == 403


def test_write_policies_versions_and_canonical_immutability(tmp_path, monkeypatch) -> None:
    client, _ = governance_client(tmp_path, monkeypatch)
    author_only = create_record(
        client,
        "creator-key",
        "policy-author-0001",
        "author only",
        write_policy="author_only",
    ).json()
    team = create_record(
        client,
        "creator-key",
        "policy-team-0001",
        "team editable",
        write_policy="team_editable",
    ).json()
    admin_only = create_record(
        client,
        "creator-key",
        "policy-admin-0001",
        "admin only",
        write_policy="admin_only",
    ).json()
    immutable = create_record(
        client,
        "creator-key",
        "policy-immutable-0001",
        "immutable",
        write_policy="immutable",
    ).json()
    canonical = create_record(
        client,
        "publisher-key",
        "policy-canonical-0001",
        "canonical",
        role="canonical",
        lifecycle="live",
        write_policy="team_editable",
    ).json()

    denied_author = client.patch(
        f"/records/{author_only['id']}",
        headers=auth("editor-key", "patch-author-denied", **{"If-Match": "1"}),
        json={"title": "not allowed"},
    )
    assert denied_author.status_code == 403

    team_patch_headers = auth("editor-key", "patch-team-success", **{"If-Match": '"1"'})
    team_edit = client.patch(
        f"/records/{team['id']}",
        headers=team_patch_headers,
        json={"title": "team changed"},
    )
    replay = client.patch(
        f"/records/{team['id']}",
        headers=team_patch_headers,
        json={"title": "team changed"},
    )
    assert team_edit.status_code == replay.status_code == 200
    assert team_edit.json()["version"] == 2
    assert replay.headers["Idempotency-Replayed"] == "true"

    policy_takeover = client.patch(
        f"/records/{team['id']}",
        headers=auth("editor-key", "patch-policy-denied", **{"If-Match": "2"}),
        json={"write_policy": "author_only"},
    )
    assert policy_takeover.status_code == 403

    stale = client.patch(
        f"/records/{team['id']}",
        headers=auth("editor-key", "patch-team-stale-01", **{"If-Match": "1"}),
        json={"title": "stale"},
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "version_conflict"

    denied_admin = client.patch(
        f"/records/{admin_only['id']}",
        headers=auth("editor-key", "patch-admin-denied", **{"If-Match": "1"}),
        json={"title": "not allowed"},
    )
    assert denied_admin.status_code == 403
    allowed_admin = client.patch(
        f"/records/{admin_only['id']}",
        headers=auth("admin-key", "patch-admin-success", **{"If-Match": "1"}),
        json={"title": "admin changed"},
    )
    assert allowed_admin.status_code == 200

    immutable_edit = client.patch(
        f"/records/{immutable['id']}",
        headers=auth("admin-key", "patch-immutable-denied", **{"If-Match": "1"}),
        json={"title": "cannot change"},
    )
    canonical_edit = client.patch(
        f"/records/{canonical['id']}",
        headers=auth("admin-key", "patch-canonical-denied", **{"If-Match": "1"}),
        json={"title": "cannot change"},
    )
    assert immutable_edit.status_code == canonical_edit.status_code == 409


def test_promotion_requires_permission_version_reason_and_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    client, db_path = governance_client(tmp_path, monkeypatch)
    candidate = create_record(
        client,
        "creator-key",
        "promotion-candidate-0001",
        "candidate",
        write_policy="team_editable",
    ).json()
    path = f"/records/{candidate['id']}/promote"

    ordinary = client.post(
        path,
        headers=auth(
            "creator-key",
            "promotion-denied-0001",
            **{"If-Match": "1", "X-MemoryV4-Reason": "verified"},
        ),
    )
    assert ordinary.status_code == 403

    missing_reason = client.post(
        path,
        headers=auth("promoter-key", "promotion-no-reason", **{"If-Match": "1"}),
    )
    assert missing_reason.status_code == 422

    promote_headers = auth(
        "promoter-key",
        "promotion-success-0001",
        **{"If-Match": '"1"', "X-MemoryV4-Reason": "source independently verified"},
    )
    promoted = client.post(path, headers=promote_headers)
    replay = client.post(path, headers=promote_headers)
    assert promoted.status_code == replay.status_code == 200
    assert promoted.json()["role"] == "canonical"
    assert promoted.json()["lifecycle"] == "live"
    assert promoted.json()["version"] == 2
    assert replay.headers["Idempotency-Replayed"] == "true"

    reused = client.post(
        path,
        headers=auth(
            "promoter-key",
            "promotion-success-0001",
            **{"If-Match": "1", "X-MemoryV4-Reason": "different reason"},
        ),
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_conflict"

    with sqlite3.connect(db_path) as conn:
        detail = conn.execute(
            "SELECT detail_json FROM audit_events WHERE action = 'record.promote'"
        ).fetchone()[0]
    assert json.loads(detail)["reason"] == "source independently verified"
