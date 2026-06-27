from fastapi.testclient import TestClient

from app.main import APP_NAME, APP_VERSION, create_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
    }
