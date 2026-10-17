from fastapi.testclient import TestClient

from app import __version__
from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_request_id_is_generated_and_echoed() -> None:
    assert client.get("/api/v1/health").headers["X-Request-ID"]
    echoed = client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})
    assert echoed.headers["X-Request-ID"] == "abc123"
