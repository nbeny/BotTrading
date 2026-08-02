#!/usr/bin/env python3
"""Mint the TELEGRAM_SESSION string that collector-social's Telegram source runs on.

Run this once, locally, on a machine where you can receive the login code. It
asks Telegram for a code (and your 2FA password if you have one), then prints a
StringSession — paste that into `.env` as TELEGRAM_SESSION and deploy.

    TELEGRAM_API_ID=... TELEGRAM_API_HASH=... python scripts/telegram_session.py

Why a *user* session and not a bot: a bot only ever sees channels it administers,
and every channel we read is third-party. Why a string and not the usual
`.session` file: the collector runs in a container with no writable state and no
stdin — it cannot complete a login, so the session has to arrive already valid.

The printed string grants full access to the account. It is a credential, not a
config value: keep it out of git, out of images, out of chat.
"""

from __future__ import annotations

import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> int:
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not (api_id.isdigit() and api_hash):
        print(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH first "
            "(https://my.telegram.org/apps).",
            file=sys.stderr,
        )
        return 2

    # An empty StringSession() means "no session yet"; start() then drives the
    # phone / login-code / 2FA prompts on stdin.
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()
    try:
        me = await client.get_me()
        handle = getattr(me, "username", None) or getattr(me, "phone", "?")
        # Banner on stderr so `... > session.txt` captures only the credential.
        print(f"\nAuthorized as {handle}.\n", file=sys.stderr)
        print("TELEGRAM_SESSION=" + client.session.save())
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
