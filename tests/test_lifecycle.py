import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import (
    Lifecycle,
    RecordCreate,
    RecordSupersedeRequest,
    Role,
)
from app.storage import SqliteStore

GRANTS = {
    "creator-key": {
        "actor": "user:creator",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.create", "memory.edit"],
    },
    "editor-key": {
        "actor": "user:editor",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.edit"],
    },
    "publisher-key": {
        "actor": "user:publisher",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.create", "memory.promote", "memory.edit"],
    },
    "archiver-key": {
        "actor": "user:archiver",
        "scope_path": "org:a",
        "permissions": ["memory.read", "memory.search", "memory.archive"],
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


def lifecycle_client(tmp_path, monkeypatch):
    db_path = tmp_path / "lifecycle.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps(GRANTS))
    return TestClient(create_app()), db_path


def headers(
    key: str,
    idempotency_key: str | None = None,
    version: int | None = None,
    reason: str | None = None,
):
    result = {"Authorization": f"Bearer {key}"}
    if idempotency_key is not None:
        result["Idempotency-Key"] = idempotency_key
    if version is not None:
        result["If-Match"] = str(version)
    if reason is not None:
        result["X-MemoryV4-Reason"] = reason
    return result


def create_record(
    client: TestClient,
    *,
    key: str = "creator-key",
    idem: str,
    title: str,
    role: str = "active",
    lifecycle: str = "working",
    write_policy: str = "team_editable",
    scope_path: str = "org:a/project:p",
):
    response = client.post(
        "/records",
        headers=headers(key, idem),
        json={
            "title": title,
            "content": f"content for {title}",
            "role": role,
            "lifecycle": lifecycle,
            "write_policy": write_policy,
            "scope_path": scope_path,
            "tags": ["lifecycle"],
            "confidence": 0.8,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def transition(client, record_id: str, target: str, *, idem: str, version: int, reason: str):
    return client.post(
        f"/records/{record_id}/transition",
        headers=headers("archiver-key", idem, version, reason),
        json={"lifecycle": target},
    )


def test_transition_requires_authority_preconditions_reason_and_scope(tmp_path, monkeypatch):
    client, _ = lifecycle_client(tmp_path, monkeypatch)
    record = create_record(client, idem="lifecycle-preconditions-create", title="governed")

    denied = client.post(
        f"/records/{record['id']}/transition",
        headers=headers("editor-key", "lifecycle-denied-edit", 1, "not authorized"),
        json={"lifecycle": "archived"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"required_permission": "memory.archive"}

    missing_idempotency = client.post(
        f"/records/{record['id']}/transition",
        headers=headers("archiver-key", version=1, reason="archive it"),
        json={"lifecycle": "archived"},
    )
    missing_version = client.post(
        f"/records/{record['id']}/transition",
        headers=headers("archiver-key", "lifecycle-missing-version", reason="archive it"),
        json={"lifecycle": "archived"},
    )
    missing_reason = client.post(
        f"/records/{record['id']}/transition",
        headers=headers("archiver-key", "lifecycle-missing-reason", 1),
        json={"lifecycle": "archived"},
    )
    assert missing_idempotency.status_code == missing_version.status_code == 428
    assert missing_reason.status_code == 422

    sibling = create_record(
        client,
        key="other-admin-key",
        idem="lifecycle-sibling-create",
        title="sibling",
        scope_path="org:b/project:p",
    )
    hidden = transition(
        client,
        sibling["id"],
        "archived",
        idem="lifecycle-sibling-hidden",
        version=1,
        reason="should remain hidden",
    )
    assert hidden.status_code == 404


def test_archive_soft_delete_exact_restore_idempotency_and_audit(tmp_path, monkeypatch):
    client, db_path = lifecycle_client(tmp_path, monkeypatch)
    record = create_record(client, idem="archive-create-record", title="archive me")

    archive_headers = headers(
        "archiver-key", "archive-transition-key", 1, "retention window complete"
    )
    archived = client.post(
        f"/records/{record['id']}/transition",
        headers=archive_headers,
        json={"lifecycle": "archived"},
    )
    replay = client.post(
        f"/records/{record['id']}/transition",
        headers=archive_headers,
        json={"lifecycle": "archived"},
    )
    assert archived.status_code == replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = client.post(
        f"/records/{record['id']}/transition",
        headers=archive_headers,
        json={"lifecycle": "expired"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert archived.json()["lifecycle"] == "archived"
    assert archived.json()["previous_lifecycle"] == "working"
    assert archived.json()["deleted_at"] is not None
    assert archived.json()["lifecycle_changed_at"] is not None
    assert archived.json()["version"] == 2

    hidden = client.get(
        f"/records/{record['id']}",
        params={"scope_path": "org:a/project:p"},
        headers=headers("archiver-key"),
    )
    default_list = client.get(
        "/records",
        params={"scope_path": "org:a/project:p"},
        headers=headers("archiver-key"),
    )
    history = client.get(
        "/records",
        params={"include_deleted": True, "scope_path": "org:a/project:p"},
        headers=headers("archiver-key"),
    )
    denied_history = client.get(
        "/records",
        params={"include_deleted": True, "scope_path": "org:a/project:p"},
        headers=headers("creator-key"),
    )
    assert hidden.status_code == 404
    assert record["id"] not in {item["id"] for item in default_list.json()["records"]}
    assert record["id"] in {item["id"] for item in history.json()["records"]}
    assert denied_history.status_code == 403

    wrong_restore = transition(
        client,
        record["id"],
        "live",
        idem="archive-wrong-restore",
        version=2,
        reason="wrong prior state",
    )
    assert wrong_restore.status_code == 409
    restored = transition(
        client,
        record["id"],
        "working",
        idem="archive-correct-restore",
        version=2,
        reason="retention hold removed",
    )
    assert restored.status_code == 200
    assert restored.json()["lifecycle"] == "working"
    assert restored.json()["previous_lifecycle"] is None
    assert restored.json()["deleted_at"] is None
    assert restored.json()["version"] == 3
    visible = client.get(
        f"/records/{record['id']}",
        params={"scope_path": "org:a/project:p"},
        headers=headers("archiver-key"),
    )
    assert visible.status_code == 200

    with sqlite3.connect(db_path) as conn:
        events = conn.execute(
            "SELECT action, detail_json FROM audit_events "
            "WHERE action IN ('record.archive','record.restore') ORDER BY id"
        ).fetchall()
    assert [action for action, _ in events] == ["record.archive", "record.restore"]
    assert json.loads(events[0][1])["reason"] == "retention window complete"
    assert json.loads(events[1][1])["reason"] == "retention hold removed"


def test_expiry_direct_transition_matrix_and_canonical_invariant(tmp_path, monkeypatch):
    client, _ = lifecycle_client(tmp_path, monkeypatch)
    record = create_record(client, idem="expiry-create-record", title="expiry")

    stale = transition(
        client,
        record["id"],
        "live",
        idem="expiry-stale-version",
        version=9,
        reason="stale writer",
    )
    assert stale.status_code == 412
    made_live = transition(
        client,
        record["id"],
        "live",
        idem="expiry-working-live",
        version=1,
        reason="verified for active use",
    )
    assert made_live.status_code == 200
    expired = transition(
        client,
        record["id"],
        "expired",
        idem="expiry-live-expired",
        version=2,
        reason="freshness deadline passed",
    )
    assert expired.status_code == 200
    assert expired.json()["previous_lifecycle"] == "live"
    assert expired.json()["deleted_at"] is None

    wrong_target = transition(
        client,
        record["id"],
        "working",
        idem="expiry-wrong-restore",
        version=3,
        reason="must restore exact state",
    )
    assert wrong_target.status_code == 409
    restored = transition(
        client,
        record["id"],
        "live",
        idem="expiry-correct-restore",
        version=3,
        reason="freshness revalidated",
    )
    assert restored.status_code == 200

    same_state = transition(
        client,
        record["id"],
        "live",
        idem="expiry-same-state",
        version=4,
        reason="no-op forbidden",
    )
    governed_only = transition(
        client,
        record["id"],
        "superseded",
        idem="expiry-illegal-superseded",
        version=4,
        reason="wrong endpoint",
    )
    assert same_state.status_code == governed_only.status_code == 409

    canonical = create_record(
        client,
        key="publisher-key",
        idem="canonical-lifecycle-create",
        title="canonical",
        role="canonical",
        lifecycle="live",
    )
    demote = transition(
        client,
        canonical["id"],
        "working",
        idem="canonical-demotion-denied",
        version=1,
        reason="canonical cannot become working",
    )
    assert demote.status_code == 409


def test_atomic_canonical_supersession_links_history_and_replays(tmp_path, monkeypatch):
    client, db_path = lifecycle_client(tmp_path, monkeypatch)
    original = create_record(
        client,
        key="publisher-key",
        idem="supersede-canonical-create",
        title="canonical v1",
        role="canonical",
        lifecycle="live",
    )
    supersede_headers = headers(
        "editor-key", "supersede-canonical-key", 1, "new evidence corrected the title"
    )
    replacement = client.post(
        f"/records/{original['id']}/supersede",
        headers=supersede_headers,
        json={"title": "canonical v2", "confidence": 0.95},
    )
    replay = client.post(
        f"/records/{original['id']}/supersede",
        headers=supersede_headers,
        json={"title": "canonical v2", "confidence": 0.95},
    )
    assert replacement.status_code == replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    revised = replacement.json()
    assert revised["id"] != original["id"]
    assert revised["role"] == "canonical"
    assert revised["lifecycle"] == "live"
    assert revised["supersedes"] == original["id"]
    assert revised["author_actor"] == "user:editor"
    assert revised["content"] == original["content"]
    assert revised["tags"] == original["tags"]

    prior = client.get(
        f"/records/{original['id']}",
        params={"scope_path": "org:a/project:p"},
        headers=headers("editor-key"),
    )
    assert prior.status_code == 200
    assert prior.json()["lifecycle"] == "superseded"
    assert prior.json()["previous_lifecycle"] == "live"
    assert prior.json()["superseded_by"] == revised["id"]
    assert prior.json()["version"] == 2

    repeat_with_new_key = client.post(
        f"/records/{original['id']}/supersede",
        headers=headers("editor-key", "supersede-again-denied", 2, "cannot fork history"),
        json={"title": "canonical v3"},
    )
    assert repeat_with_new_key.status_code == 409

    with sqlite3.connect(db_path) as conn:
        record_count = conn.execute("SELECT count(*) FROM records").fetchone()[0]
        supersede_events = conn.execute(
            "SELECT detail_json FROM audit_events WHERE action='record.supersede'"
        ).fetchall()
    assert record_count == 2
    assert len(supersede_events) == 1
    assert json.loads(supersede_events[0][0])["replacement_id"] == revised["id"]


def test_supersession_enforces_policy_version_and_reference_atomicity(tmp_path, monkeypatch):
    client, db_path = lifecycle_client(tmp_path, monkeypatch)
    immutable = create_record(
        client,
        idem="immutable-supersede-create",
        title="immutable v1",
        write_policy="immutable",
    )
    direct_edit = client.patch(
        f"/records/{immutable['id']}",
        headers=headers("creator-key", "immutable-patch-denied", 1),
        json={"title": "forbidden overwrite"},
    )
    denied_editor = client.post(
        f"/records/{immutable['id']}/supersede",
        headers=headers("editor-key", "immutable-editor-denied", 1, "not author"),
        json={"title": "forbidden replacement"},
    )
    stale_author = client.post(
        f"/records/{immutable['id']}/supersede",
        headers=headers("creator-key", "immutable-stale-version", 9, "stale version"),
        json={"title": "stale replacement"},
    )
    missing_entity = client.post(
        f"/records/{immutable['id']}/supersede",
        headers=headers("creator-key", "immutable-missing-entity", 1, "bad reference"),
        json={"entity": {"entity_type": "Folder", "id": "missing"}},
    )
    assert direct_edit.status_code == 409
    assert denied_editor.status_code == 403
    assert stale_author.status_code == 412
    assert missing_entity.status_code == 404

    with sqlite3.connect(db_path) as conn:
        unchanged = conn.execute(
            "SELECT lifecycle, superseded_by, version FROM records WHERE id=?",
            (immutable["id"],),
        ).fetchone()
        count = conn.execute("SELECT count(*) FROM records").fetchone()[0]
    assert unchanged == ("working", None, 1)
    assert count == 1

    replacement = client.post(
        f"/records/{immutable['id']}/supersede",
        headers=headers("creator-key", "immutable-author-success", 1, "author revision"),
        json={"content": "new immutable snapshot"},
    )
    assert replacement.status_code == 200
    assert replacement.json()["write_policy"] == "immutable"


def test_concurrent_supersession_claim_creates_one_replacement(tmp_path):
    store = SqliteStore(tmp_path / "concurrent-lifecycle.sqlite3")
    original = store.create_record(
        RecordCreate(
            title="Concurrent original",
            content="version one",
            role=Role.active,
            lifecycle=Lifecycle.working,
            scope_path="org:a",
        ),
        actor="user:author",
    )
    request = RecordSupersedeRequest(content="version two")

    def supersede_once(_index: int):
        return store.supersede_record_idempotent(
            original.id,
            request,
            actor="user:author",
            expected_version=1,
            reason="concurrent correction",
            idempotency_key="concurrent-supersede-key",
            request_hash="same-supersession-request-hash",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(supersede_once, range(8)))

    assert len({record.id for record, _ in results}) == 1
    assert sum(not replayed for _, replayed in results) == 1
    assert store.get_record(original.id).lifecycle == Lifecycle.superseded
    assert store.count_audit_events(action="record.supersede") == 1
    with sqlite3.connect(store.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM records").fetchone()[0] == 2


def test_concurrent_transition_claim_mutates_once(tmp_path):
    store = SqliteStore(tmp_path / "concurrent-transition.sqlite3")
    original = store.create_record(
        RecordCreate(
            title="Concurrent transition",
            content="one state change",
            role=Role.active,
            lifecycle=Lifecycle.working,
            scope_path="org:a",
        ),
        actor="user:author",
    )

    def transition_once(_index: int):
        return store.transition_record_idempotent(
            original.id,
            Lifecycle("archived"),
            actor="user:archiver",
            expected_version=1,
            reason="concurrent retention action",
            idempotency_key="concurrent-transition-key",
            request_hash="same-transition-request-hash",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(transition_once, range(8)))

    assert {record.lifecycle for record, _ in results} == {Lifecycle.archived}
    assert sum(not replayed for _, replayed in results) == 1
    assert store.count_audit_events(action="record.archive") == 1
    archived = store.get_record(original.id)
    assert archived is not None
    assert archived.version == 2
    assert archived.deleted_at is not None


def test_lifecycle_capability_schema_and_database_state_integrity(tmp_path, monkeypatch):
    client, db_path = lifecycle_client(tmp_path, monkeypatch)
    capabilities = client.get("/capabilities", headers=headers("admin-key"))
    schema = client.get("/schema", headers=headers("admin-key"))
    assert capabilities.status_code == schema.status_code == 200
    operations = {
        (item["method"], item["path"]): item for item in capabilities.json()["operations"]
    }
    for path in ("/records/{id}/supersede", "/records/{id}/transition"):
        operation = operations[("POST", path)]
        assert operation["status"] == "implemented"
        assert operation["idempotency_required"] is True
        assert operation["version_precondition_required"] is True
        assert operation["reason_required"] is True
    record_schema = schema.json()["schemas"]["record"]
    assert record_schema["lifecycle_transitions"]["superseded"] == []
    assert "previous_lifecycle" in record_schema["fields"]

    SqliteStore(db_path)
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO records(id,title,content,role,lifecycle,scope_path,author_actor,"
                "previous_lifecycle,created_at,updated_at,write_policy,version) "
                "VALUES ('rec_bad','bad','bad','active','archived','org:a','tester',"
                "'archived','now','now','author_only',1)"
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("database accepted an invalid previous_lifecycle")
