from fastapi.testclient import TestClient

from app.contracts import CONTRACT_VERSION, INVARIANTS, OPERATIONS, Permission
from app.main import create_app


def contract_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(tmp_path / "contract.sqlite3"))
    monkeypatch.setenv("MEMORYV4_API_KEYS", '{"contract-key":"org:aquiero"}')
    return TestClient(create_app())


def headers(**extra):
    return {"Authorization": "Bearer contract-key", **extra}


def test_contract_discovery_is_authenticated_and_versioned(tmp_path, monkeypatch) -> None:
    client = contract_client(tmp_path, monkeypatch)

    denied = client.get("/capabilities", headers={"X-Request-ID": "req_test_denied"})
    assert denied.status_code == 401
    assert denied.json() == {
        "error": {
            "code": "unauthorized",
            "message": "bearer token required",
            "status": 401,
            "request_id": "req_test_denied",
            "details": {},
        }
    }
    assert denied.headers["X-Request-ID"] == "req_test_denied"
    assert denied.headers["X-MemoryV4-Contract-Version"] == CONTRACT_VERSION

    response = client.get("/capabilities", headers=headers())
    assert response.status_code == 200
    assert response.headers["X-MemoryV4-Contract-Version"] == CONTRACT_VERSION
    payload = response.json()
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["architecture"] == {
        "tier": "tier-3",
        "gateway": "UNIFY",
        "human_interface": "UNIUI",
        "storage": "sqlite",
        "deployment": "single-private-container",
    }
    assert payload["governance"]["autonomous_default"] == {
        "role": "active",
        "lifecycle": "working",
    }
    assert set(payload["governance"]["permissions"]) == {item.value for item in Permission}


def test_capability_inventory_is_unique_complete_and_truthful(tmp_path, monkeypatch) -> None:
    client = contract_client(tmp_path, monkeypatch)
    payload = client.get("/capabilities", headers=headers()).json()
    operations = payload["operations"]
    identities = [(item["method"], item["path"]) for item in operations]

    assert len(identities) == len(set(identities)) == len(OPERATIONS)
    assert ("GET", "/capabilities") in identities
    assert ("GET", "/schema") in identities
    assert ("POST", "/records/{id}/promote") in identities
    assert ("GET", "/audit/events") in identities

    by_identity = {(item["method"], item["path"]): item for item in operations}
    assert by_identity[("GET", "/capabilities")]["status"] == "implemented"
    assert by_identity[("POST", "/records")]["status"] == "implemented"
    assert by_identity[("POST", "/records/{id}/promote")] == {
        "method": "POST",
        "path": "/records/{id}/promote",
        "permission": "memory.promote",
        "status": "implemented",
        "mutation": True,
        "idempotency_required": True,
        "version_precondition_required": True,
        "reason_required": True,
    }


def test_schema_discovery_locks_objects_enums_and_invariants(tmp_path, monkeypatch) -> None:
    client = contract_client(tmp_path, monkeypatch)
    response = client.get("/schema", headers=headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == CONTRACT_VERSION
    assert set(payload["schemas"]) == {
        "entity",
        "record",
        "relation",
        "artifact",
        "finding",
        "error",
    }
    record_fields = payload["schemas"]["record"]["fields"]
    assert record_fields["role"] == ["canonical", "active", "evidence", "exhaust"]
    assert record_fields["lifecycle"] == [
        "live",
        "working",
        "superseded",
        "archived",
        "expired",
    ]
    assert record_fields["write_policy"] == [
        "team_editable",
        "author_only",
        "admin_only",
        "immutable",
    ]
    assert payload["invariants"] == INVARIANTS


def test_validation_errors_use_locked_envelope(tmp_path, monkeypatch) -> None:
    client = contract_client(tmp_path, monkeypatch)
    response = client.get(
        "/search",
        params={"q": "", "limit": 0},
        headers=headers(**{"X-Request-ID": "req_validation"}),
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["status"] == 422
    assert error["request_id"] == "req_validation"
    assert error["details"]["violations"]


def test_openapi_publishes_discovery_and_error_contract(tmp_path, monkeypatch) -> None:
    client = contract_client(tmp_path, monkeypatch)
    document = client.get("/openapi.json").json()

    assert "/capabilities" in document["paths"]
    assert "/schema" in document["paths"]
    schemas = document["components"]["schemas"]
    assert "CapabilitiesResponse" in schemas
    assert "SchemaContractResponse" in schemas
    assert "ErrorResponse" in schemas
