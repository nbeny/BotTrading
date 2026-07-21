"""Lightweight JWT decode for the operator-terminal auth hook.

This is intentionally minimal: the terminal passes ``?token=<jwt>`` on the
WebSocket handshake. If ``JWT_SECRET`` is configured we verify the HS256
signature; otherwise we decode the payload without verification (dev mode) and
log that we did so. Either way we surface ``sub``/``role`` for logging and never
hard-fail unless a configured secret is present and the token is invalid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class InvalidTokenError(Exception):
    """Raised only when a JWT_SECRET is configured and verification fails."""


@dataclass(frozen=True)
class Principal:
    sub: str | None
    role: str | None
    verified: bool


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _verify_hs256(
    header_b64: str, payload_b64: str, signature_b64: str, secret: str
) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided = _b64url_decode(signature_b64)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, provided)


def decode_token(token: str | None) -> Principal:
    """Decode (and optionally verify) a JWT, returning its principal.

    Returns an anonymous, unverified principal when no token is supplied. Raises
    :class:`InvalidTokenError` only when ``JWT_SECRET`` is set and the token fails
    structural or signature checks.
    """
    secret = os.getenv("JWT_SECRET") or None

    if not token:
        if secret:
            raise InvalidTokenError("token required")
        logger.info("ws auth: no token supplied (dev mode, accepting)")
        return Principal(sub=None, role=None, verified=False)

    parts = token.split(".")
    if len(parts) != 3:
        if secret:
            raise InvalidTokenError("malformed token")
        logger.warning("ws auth: malformed token, accepting in dev mode")
        return Principal(sub=None, role=None, verified=False)

    header_b64, payload_b64, signature_b64 = parts
    try:
        claims: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError) as exc:
        if secret:
            raise InvalidTokenError("undecodable payload") from exc
        logger.warning("ws auth: undecodable payload, accepting in dev mode")
        return Principal(sub=None, role=None, verified=False)

    sub = claims.get("sub")
    role = claims.get("role")

    if secret:
        if not _verify_hs256(header_b64, payload_b64, signature_b64, secret):
            raise InvalidTokenError("bad signature")
        logger.info("ws auth: verified token sub=%s role=%s", sub, role)
        return Principal(sub=sub, role=role, verified=True)

    logger.info("ws auth: decoded (unverified) token sub=%s role=%s", sub, role)
    return Principal(sub=sub, role=role, verified=False)


def encode_token(claims: dict[str, Any], *, secret: str, ttl_seconds: int = 3600) -> str:
    """Mint an HS256 JWT. `exp` is added from ttl_seconds (informational; decode_token
    does not enforce expiry, matching the existing lenient decoder)."""
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(claims)
    body.setdefault("iat", int(time.time()))
    body["exp"] = int(time.time()) + ttl_seconds
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"
