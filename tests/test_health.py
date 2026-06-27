from fastapi.testclient import TestClient

from app.main import APP_NAME, APP_VERSION, create_app


def test_health_endpoint_returns_ok_and_sqlite_backend(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "memoryv4.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(db_path))
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "storage_backend": "sqlite",
    }
    assert db_path.exists()
