"""Minimal async Kraken Futures REST client.

Signs private requests with the Kraken Futures scheme and routes to the demo or
live host. In dry_run mode it performs NO network I/O and returns deterministic
simulated responses so the whole pipeline can be exercised safely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Mode, TradingConfig

logger = logging.getLogger(__name__)

_HOSTS = {
    Mode.LIVE: "https://futures.kraken.com/derivatives",
    Mode.DEMO: "https://demo-futures.kraken.com/derivatives",
    Mode.DRY_RUN: "https://futures.kraken.com/derivatives",  # unused in dry_run
}


class KrakenFuturesClient:
    def __init__(self, config: TradingConfig, *, mode_provider=None) -> None:
        self._config = config
        self._mode_provider = mode_provider or (lambda: config.mode)
        self._secret = config.api_secret
        self._key = config.api_key
        self._http: httpx.AsyncClient | None = None

    def _mode(self) -> Mode:
        return self._mode_provider()

    def current_base_url(self) -> str:
        return _HOSTS[self._mode()]

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- signing --------------------------------------------------------
    def sign(self, endpoint_path: str, nonce: str, post_data: str) -> str:
        """Authent = base64(HMAC-SHA512(secret, SHA256(postData+nonce+path)))."""
        message = (post_data + nonce + endpoint_path).encode("utf-8")
        sha256 = hashlib.sha256(message).digest()
        secret = base64.b64decode(self._secret)
        mac = hmac.new(secret, sha256, hashlib.sha512).digest()
        return base64.b64encode(mac).decode("utf-8")

    def _nonce(self) -> str:
        return str(int(time.time() * 1000))

    async def _post(self, endpoint_path: str, params: dict[str, Any]) -> dict[str, Any]:
        post_data = urlencode(params)
        nonce = self._nonce()
        headers = {
            "APIKey": self._key,
            "Nonce": nonce,
            "Authent": self.sign(endpoint_path, nonce, post_data),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        assert self._http is not None, "client not started"
        resp = await self._http.post(
            self.current_base_url() + endpoint_path, content=post_data, headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    # --- public API -----------------------------------------------------
    async def send_order(
        self,
        *,
        pair: str,
        side: str,            # "buy" | "sell"
        order_type: str,      # "lmt" | "mkt" | "stp" | "take_profit"
        size: float,
        limit_price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        cli_ord_id: str | None = None,
    ) -> dict[str, Any]:
        if self._mode() is Mode.DRY_RUN:
            logger.info(
                "[DRY_RUN] send_order %s %s %s size=%s lmt=%s stop=%s ro=%s cli=%s",
                pair, side, order_type, size, limit_price, stop_price,
                reduce_only, cli_ord_id,
            )
            return {
                "result": "success",
                "order_id": f"DRYRUN-{cli_ord_id or pair}",
                "dry_run": True,
            }
        params: dict[str, Any] = {
            "orderType": order_type,
            "symbol": pair,
            "side": side,
            "size": size,
            "reduceOnly": str(reduce_only).lower(),
        }
        if limit_price is not None:
            params["limitPrice"] = limit_price
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if cli_ord_id is not None:
            params["cliOrdId"] = cli_ord_id
        return await self._post("/api/v3/sendorder", params)

    async def cancel_order(self, *, cli_ord_id: str) -> dict[str, Any]:
        if self._mode() is Mode.DRY_RUN:
            logger.info("[DRY_RUN] cancel_order cli=%s", cli_ord_id)
            return {"result": "success", "dry_run": True}
        return await self._post("/api/v3/cancelorder", {"cliOrdId": cli_ord_id})

    async def get_accounts(self) -> dict[str, Any]:
        if self._mode() is Mode.DRY_RUN:
            return {"accounts": {"flex": {"portfolioValue": 10_000.0}}, "dry_run": True}
        return await self._post("/api/v3/accounts", {})

    async def get_open_positions(self) -> dict[str, Any]:
        if self._mode() is Mode.DRY_RUN:
            return {"openPositions": [], "dry_run": True}
        return await self._post("/api/v3/openpositions", {})

    async def get_open_orders(self) -> dict[str, Any]:
        if self._mode() is Mode.DRY_RUN:
            return {"openOrders": [], "dry_run": True}
        return await self._post("/api/v3/openorders", {})
