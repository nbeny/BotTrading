"""Login endpoint minting an HS256 JWT for the configured admin operator."""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cmi_common.auth import encode_token

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthError(Exception):
    pass


class LoginInput(BaseModel):
    username: str
    password: str


async def issue_token(username: str, password: str) -> str:
    exp_user = os.getenv("CONTROL_ADMIN_USER", "admin")
    exp_pw = os.getenv("CONTROL_ADMIN_PASSWORD", "")
    ok = hmac.compare_digest(username, exp_user) and hmac.compare_digest(password, exp_pw)
    if not ok:
        raise AuthError("invalid credentials")
    secret = os.getenv("JWT_SECRET", "")
    # The sole hardcoded account is the platform admin: grant the admin role so
    # the terminal's RBAC allows mode switching and settings edits (operator
    # cannot). See frontend rbac.ts.
    return encode_token({"sub": username, "role": "admin"}, secret=secret, ttl_seconds=3600)


@router.post("/login")
async def login(body: LoginInput) -> dict[str, str]:
    try:
        token = await issue_token(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="invalid credentials") from exc
    return {"access_token": token, "token_type": "bearer"}
