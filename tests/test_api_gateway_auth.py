"""The read plane requires a bearer token once JWT_SECRET is configured.

Next.js proxies /api/gateway/* straight from the public internet, so until this
landed, `curl https://<host>/api/gateway/data/content` returned raw collected
content, portfolio holdings and the day's AI spend to anyone who knew the path.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cmi_common.auth import encode_token, require_principal

SECRET = "test-secret"


def _app() -> FastAPI:
    """Mirrors how api-gateway/app/main.py mounts its routers."""
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/data/content")
    def content() -> dict[str, list[str]]:
        return {"items": []}

    app.include_router(router, dependencies=[Depends(require_principal)])
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def test_read_route_rejects_an_anonymous_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    assert client.get("/data/content").status_code == 401


def test_read_route_rejects_a_forged_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    forged = encode_token({"sub": "mallory"}, secret="not-the-real-secret")
    resp = client.get("/data/content", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_read_route_accepts_a_properly_signed_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    token = encode_token({"sub": "alesio", "role": "admin"}, secret=SECRET)
    resp = client.get("/data/content", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_health_stays_open_for_the_container_healthcheck(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Docker's healthcheck and Prometheus have no token; locking these would
    # make the container report unhealthy and drop out of the deploy.
    monkeypatch.setenv("JWT_SECRET", SECRET)
    assert client.get("/health").status_code == 200


def test_without_a_configured_secret_the_gate_is_permissive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deliberate, and the reason docker-compose.vps.yml declares JWT_SECRET with
    # `:?` so production cannot start without it: local dev and the offline test
    # suite run tokenless. This test pins the fail-open behaviour so nobody is
    # surprised by it.
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert client.get("/data/content").status_code == 200
