"""FastAPI dependency enforcing a JWT bearer token (lenient in dev if no secret)."""
from __future__ import annotations

from fastapi import Header, HTTPException

from cmi_common.auth import InvalidTokenError, Principal, decode_token


async def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    try:
        return decode_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
