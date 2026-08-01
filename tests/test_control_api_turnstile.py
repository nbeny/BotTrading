"""Turnstile gate in front of control-api's /auth/login."""

import asyncio

import httpx
import pytest
from fastapi import HTTPException
from tests.control_api_helpers import load_module


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _stub_client(monkeypatch, ts, *, payload=None, raises=None) -> dict:
    """Replace httpx.AsyncClient with a stub; returns the captured POST body."""
    captured: dict = {}

    class _Client:
        def __init__(self, **_kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def post(self, url: str, data: dict):
            captured["url"] = url
            captured["data"] = data
            if raises is not None:
                raise raises
            return _StubResponse(payload or {"success": True})

    monkeypatch.setattr(ts.httpx, "AsyncClient", _Client)
    return captured


def test_disabled_without_secret_key(monkeypatch) -> None:
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    ts = load_module("turnstile")
    assert ts.is_enabled() is False
    # No secret -> no gate, so a token-less login must still pass.
    assert asyncio.run(ts.verify(None)) is True


def test_missing_token_rejected_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    ts = load_module("turnstile")
    assert ts.is_enabled() is True
    assert asyncio.run(ts.verify(None)) is False
    assert asyncio.run(ts.verify("")) is False


def test_valid_token_accepted_and_secret_sent(monkeypatch) -> None:
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    ts = load_module("turnstile")
    captured = _stub_client(monkeypatch, ts, payload={"success": True})
    assert asyncio.run(ts.verify("tok")) is True
    assert captured["url"] == ts.VERIFY_URL
    assert captured["data"] == {"secret": "sk", "response": "tok"}
    # remoteip is deliberately absent: behind the proxies it is a container IP.
    assert "remoteip" not in captured["data"]


def test_cloudflare_rejection_denies(monkeypatch) -> None:
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    ts = load_module("turnstile")
    rejected = {"success": False, "error-codes": ["invalid-input-response"]}
    _stub_client(monkeypatch, ts, payload=rejected)
    assert asyncio.run(ts.verify("tok")) is False


def test_fails_closed_when_cloudflare_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    ts = load_module("turnstile")
    _stub_client(monkeypatch, ts, raises=httpx.ConnectTimeout("timeout"))
    assert asyncio.run(ts.verify("tok")) is False


def test_login_rejects_before_checking_credentials(monkeypatch) -> None:
    """A failed captcha must 403 even when the password is correct."""
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("CONTROL_ADMIN_USER", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    ts = load_module("turnstile")
    auth = load_module("routers.auth")
    _stub_client(monkeypatch, ts, payload={"success": False})

    body = auth.LoginInput(username="admin", password="pw", turnstile_token="tok")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.login(body))
    assert exc.value.status_code == 403


def test_login_passes_with_solved_captcha(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("CONTROL_ADMIN_USER", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    ts = load_module("turnstile")
    auth = load_module("routers.auth")
    _stub_client(monkeypatch, ts, payload={"success": True})

    body = auth.LoginInput(username="admin", password="pw", turnstile_token="tok")
    result = asyncio.run(auth.login(body))
    assert result["token_type"] == "bearer" and result["access_token"]
