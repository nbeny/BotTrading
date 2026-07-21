import pytest

from cmi_common.auth import InvalidTokenError, decode_token, encode_token


def test_encode_decode_roundtrip_verified(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    token = encode_token({"sub": "admin", "role": "operator"}, secret="s3cret", ttl_seconds=60)
    p = decode_token(token)
    assert p.sub == "admin"
    assert p.role == "operator"
    assert p.verified is True


def test_bad_signature_rejected_when_secret_set(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    token = encode_token({"sub": "x"}, secret="different", ttl_seconds=60)
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_pending_execution_kind_exists() -> None:
    from cmi_common.events.execution import ExecutionKind
    assert ExecutionKind.PENDING.value == "pending"
