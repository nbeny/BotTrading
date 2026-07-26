"""Read-only Kraken *spot* client (api.kraken.com).

Separate from KrakenFuturesClient on two counts, neither cosmetic:

* The signing schemes differ. Spot signs
  `HMAC-SHA512(b64decode(secret), path + SHA256(nonce + postdata))`; Futures
  signs `HMAC-SHA512(b64decode(secret), SHA256(postdata + nonce + path))`.
  Copying one for the other yields a rejected signature.
* This client is **not governed by the trading mode**. Reading a balance is
  always a real call, so the true portfolio shows correctly in dry_run. Only
  order placement is simulated. Mixing the two would make the reconciler close
  simulated positions against real ones.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

BASE_URL = "https://api.kraken.com"
BALANCE_PATH = "/0/private/Balance"
TRADE_BALANCE_PATH = "/0/private/TradeBalance"
# The asset TradeBalance values the account in. Kraken's own ledger symbol for
# US dollars.
QUOTE_ASSET = "ZUSD"
# What counts as spendable cash. A spot account can hold none of Kraken's own
# ZUSD and still be entirely in cash: this operator's whole balance is USDC, and
# reporting `cash_usd: 0.0` next to an equity of 97.21 reads as "fully invested"
# when nothing is invested at all. Dollar-pegged stablecoins are cash here.
CASH_ASSETS = ("ZUSD", "USD", "USDC", "USDT", "DAI", "PYUSD")
# Kraken keeps dust on old accounts; it adds nothing to a balance display and
# only lengthens the payload. The comparison is strict, so a balance of exactly
# one satoshi is dropped too -- deliberate, and worth stating because 1e-8 is
# the satoshi itself, not a value below it.
DUST = 1e-8


class KrakenApiError(RuntimeError):
    """Kraken answered 200 with a non-empty `error` array."""


def unwrap(payload: dict[str, Any]) -> Any:
    """Kraken signals failure *inside* a 200 response. Without this check an
    invalid key yields an empty `result`, which would render as a balance of 0 --
    worse than no balance, because it looks like an answer."""
    errors = payload.get("error") or []
    if errors:
        raise KrakenApiError("; ".join(errors))
    return payload.get("result", {})


def parse_balances(raw: dict[str, str]) -> dict[str, float]:
    out = {}
    for asset, amount in raw.items():
        value = float(amount)
        if abs(value) > DUST:
            out[asset] = value
    return out


def build_snapshot(
    *, trade_balance: dict[str, str], balances: dict[str, str]
) -> dict[str, Any]:
    """`eb` is the whole account valued in the quote currency; the cash lines of
    Balance are the spendable part alone. Reporting the first as the second
    would present the entire portfolio as spendable."""
    parsed = parse_balances(balances)
    return {
        "equity_usd": float(trade_balance.get("eb", 0.0)),
        "cash_usd": round(sum(parsed.get(a, 0.0) for a in CASH_ASSETS), 8),
        "balances": parsed,
    }


class KrakenSpotClient:
    def __init__(self, key: str, secret: str) -> None:
        self._key = key
        self._secret = secret
        self._http: httpx.AsyncClient | None = None
        self._last_nonce = 0

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0, base_url=BASE_URL)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def sign(self, path: str, nonce: str, postdata: str) -> str:
        message = path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
        mac = hmac.new(base64.b64decode(self._secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _nonce(self) -> str:
        """Strictly increasing. Kraken permanently rejects a nonce below the last
        one it saw, and several calls inside the same microsecond are routine, so
        the clock alone is not enough: it is latched against the previous value.
        A cyclic tiebreak counter would not do -- it wraps back to zero and emits
        a *lower* nonce than the call before it."""
        self._last_nonce = max(time.time_ns() // 1000, self._last_nonce + 1)
        return str(self._last_nonce)

    async def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert self._http is not None, "client not started"
        body = {"nonce": self._nonce(), **(params or {})}
        # Signed over the exact bytes sent: `postdata` is built once and reused.
        postdata = urlencode(body)
        resp = await self._http.post(
            path,
            content=postdata,
            headers={
                "API-Key": self._key,
                "API-Sign": self.sign(path, body["nonce"], postdata),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        # Transport-level failures (429, 5xx) first -- they carry no `error`
        # array, and httpx.HTTPStatusError keeps the status code so the caller
        # can tell a rate limit from a bad key.
        resp.raise_for_status()
        return unwrap(resp.json())

    async def snapshot(self) -> dict[str, Any]:
        return build_snapshot(
            trade_balance=await self._post(TRADE_BALANCE_PATH, {"asset": QUOTE_ASSET}),
            balances=await self._post(BALANCE_PATH),
        )
