import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import MAX_CONTENT_LENGTH, MAX_JSON_FIELD_BYTES, MAX_SOURCE_REFS

LEAF_SCOPE = "tenant:t1/project:p1/agent:a1/user:u1/session:s1"
GRANTS = {
    "global-admin-key": {
        "actor": "admin:global",
        "scope_path": "global",
        "permissions": ["memory.admin"],
    },
    "leaf-reader-key": {
        "actor": "user:leaf-reader",
        "scope_path": LEAF_SCOPE,
        "permissions": ["memory.read", "memory.search"],
    },
}


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(tmp_path / "production-qa.sqlite3"))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps(GRANTS))
    return TestClient(create_app())


def auth(key: str, idempotency_key: str | None = None):
    headers = {"Authorization": f"Bearer {key}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def record_payload(title: str, scope_path: str, **overrides):
    return {
        "title": title,
        "content": f"production isolation evidence for {title}",
        "role": "active",
        "lifecycle": "working",
        "write_policy": "author_only",
        "scope_path": scope_path,
        **overrides,
    }


def create_record(client: TestClient, title: str, scope_path: str, index: int):
    response = client.post(
        "/records",
        headers=auth("global-admin-key", f"production-scope-{index:04d}"),
        json=record_payload(title, scope_path),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_tenant_project_agent_user_and_session_isolation(tmp_path, monkeypatch) -> None:
    client = client_for(tmp_path, monkeypatch)
    visible_scopes = [
        "global",
        "tenant:t1",
        "tenant:t1/project:p1",
        "tenant:t1/project:p1/agent:a1",
        "tenant:t1/project:p1/agent:a1/user:u1",
        LEAF_SCOPE,
    ]
    sibling_scopes = [
        "tenant:t2",
        "tenant:t1/project:p2",
        "tenant:t1/project:p1/agent:a2",
        "tenant:t1/project:p1/agent:a1/user:u2",
        "tenant:t1/project:p1/agent:a1/user:u1/session:s2",
    ]
    visible = [
        create_record(client, f"visible-{index}", scope, index)
        for index, scope in enumerate(visible_scopes)
    ]
    siblings = [
        create_record(client, f"sibling-{index}", scope, index + 100)
        for index, scope in enumerate(sibling_scopes)
    ]

    listing = client.get(
        "/records",
        params={"scope_path": LEAF_SCOPE, "limit": 100},
        headers=auth("leaf-reader-key"),
    )
    assert listing.status_code == 200
    listed_ids = {item["id"] for item in listing.json()["records"]}
    assert listed_ids == {item["id"] for item in visible}
    assert listed_ids.isdisjoint({item["id"] for item in siblings})

    search = client.get(
        "/search",
        params={"q": "production isolation evidence", "scope_path": LEAF_SCOPE, "limit": 100},
        headers=auth("leaf-reader-key"),
    )
    assert search.status_code == 200
    assert {item["record"]["id"] for item in search.json()["results"]} == listed_ids

    for sibling in siblings:
        hidden = client.get(
            f"/records/{sibling['id']}",
            params={"scope_path": LEAF_SCOPE},
            headers=auth("leaf-reader-key"),
        )
        assert hidden.status_code == 404

    scope_escape = client.get(
        "/records",
        params={"scope_path": "tenant:t1/project:p1"},
        headers=auth("leaf-reader-key"),
    )
    assert scope_escape.status_code == 403


def test_content_and_structured_field_limits_fail_closed(tmp_path, monkeypatch) -> None:
    client = client_for(tmp_path, monkeypatch)
    baseline = record_payload("bounded", LEAF_SCOPE)
    cases = [
        {**baseline, "content": "x" * (MAX_CONTENT_LENGTH + 1)},
        {**baseline, "source_refs": [f"ref:{index}" for index in range(MAX_SOURCE_REFS + 1)]},
        {**baseline, "source_refs": ["x" * 2049]},
        {**baseline, "provenance": {"oversized": "x" * MAX_JSON_FIELD_BYTES}},
        {**baseline, "scope_path": f"tenant:t1/project:{'x' * 1000}"},
        {**baseline, "unexpected_secret": "must not be accepted"},
    ]
    for index, payload in enumerate(cases):
        response = client.post(
            "/records",
            headers=auth("global-admin-key", f"production-limit-{index:04d}"),
            json=payload,
        )
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "invalid_request"
        assert "must not be accepted" not in response.text

    malformed = client.post(
        "/records",
        headers={
            **auth("global-admin-key", "production-malformed-json"),
            "Content-Type": "application/json",
        },
        content=b'{"title":',
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_request"

    listing = client.get(
        "/records",
        params={"scope_path": LEAF_SCOPE},
        headers=auth("leaf-reader-key"),
    )
    assert listing.status_code == 200
    assert listing.json()["records"] == []
