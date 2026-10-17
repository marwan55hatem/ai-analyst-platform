import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser
from app.core.errors import register_error_handlers
from app.core.middleware import RequestIdMiddleware
from tests.helpers import API, register


def test_unknown_route_and_method_use_error_envelope(client: TestClient) -> None:
    missing = client.get(f"{API}/nope")
    assert missing.status_code == 404
    assert missing.json() == {"code": "not_found", "message": "Not Found", "details": None}
    wrong_method = client.delete(f"{API}/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["code"] == "method_not_allowed"


def test_validation_error_shape_and_no_secret_echo(client: TestClient) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": "not-an-email", "password": "SuperSecret-Value-1"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"]
    assert "SuperSecret-Value-1" not in response.text


def test_invalid_uuid_path_is_validation_error(client: TestClient) -> None:
    owner = register(client)
    response = client.post(f"{API}/team/members/not-a-uuid/deactivate", headers=owner.headers)
    assert response.status_code == 422


def test_unhandled_exception_returns_500_envelope() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "Internal server error",
        "details": None,
    }


def test_log_context_contains_request_id_and_tenant(client: TestClient) -> None:
    probe = FastAPI()
    register_error_handlers(probe)
    probe.add_middleware(RequestIdMiddleware)

    @probe.get("/ctx")
    def ctx(user: CurrentUser) -> dict[str, str]:
        return {k: str(v) for k, v in structlog.contextvars.get_contextvars().items()}

    owner = register(client)
    response = TestClient(probe).get("/ctx", headers={**owner.headers, "X-Request-ID": "req-123"})
    assert response.status_code == 200
    assert response.json() == {
        "request_id": "req-123",
        "tenant_id": owner.tenant_id,
        "user_id": owner.user_id,
    }


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    ok = client.options(
        f"{API}/auth/login",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert ok.headers["access-control-allow-credentials"] == "true"
    bad = client.options(
        f"{API}/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers
