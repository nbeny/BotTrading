import asyncio

from tests.control_api_helpers import load_module

from cmi_common.auth import decode_token


def test_login_issues_decodable_token(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("CONTROL_ADMIN_USER", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "pw")
    auth = load_module("routers.auth")
    token = asyncio.run(auth.issue_token("admin", "pw"))
    p = decode_token(token)
    assert p.sub == "admin" and p.verified is True


def test_login_rejects_bad_credentials(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_ADMIN_USER", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "pw")
    auth = load_module("routers.auth")
    import pytest

    with pytest.raises(auth.AuthError):
        asyncio.run(auth.issue_token("admin", "wrong"))
