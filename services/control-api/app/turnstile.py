"""Cloudflare Turnstile verification, the anti-bot gate in front of /auth/login.

The widget in the browser only *produces* a token; it blocks nothing on its own.
The gate is this module: control-api hands the token to Cloudflare's siteverify
and refuses the login unless Cloudflare vouches for it.

Two deliberate choices:

- **Off unless `TURNSTILE_SECRET_KEY` is set.** Local dev, the mock terminal and
  the test suite all run without a Cloudflare account, and a captcha that can't
  be solved there would just be an unloggable-in stack. Production sets the
  secret in the VPS `.env`; the key's presence *is* the feature flag.
- **Fail closed once enabled.** A siteverify call that times out or returns
  garbage denies the login. The alternative — treating an unreachable
  Cloudflare as a pass — turns any outage into an open door, which is exactly
  what an attacker would provoke.
"""

from __future__ import annotations

import logging
import os

import httpx

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT_S = 10.0

logger = logging.getLogger(__name__)


def _secret() -> str:
    return os.getenv("TURNSTILE_SECRET_KEY", "").strip()


def is_enabled() -> bool:
    """True when a secret key is configured, i.e. logins must carry a token."""
    return bool(_secret())


async def verify(token: str | None) -> bool:
    """Ask Cloudflare whether `token` is a genuine, unspent challenge solution.

    Returns True immediately when the captcha is disabled. Note that `remoteip`
    is intentionally *not* sent: behind Cloudflare → Traefik → the Next.js proxy,
    the address FastAPI sees is a container IP, and a mismatched remoteip makes
    Cloudflare reject a perfectly valid token.
    """
    secret = _secret()
    if not secret:
        return True
    if not token:
        return False

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.post(
                VERIFY_URL, data={"secret": secret, "response": token}
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("turnstile siteverify unreachable, denying login: %s", exc)
        return False

    if not payload.get("success"):
        logger.info("turnstile rejected token: %s", payload.get("error-codes"))
        return False
    return True
