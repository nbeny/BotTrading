"""HTTP access to DefiLlama, plus the cache that makes unlocks affordable.

Two hosts are in play. `api.llama.fi` serves the bulk TVL and fee endpoints.
Unlock schedules are *not* on the free API — `api.llama.fi/emissions` answers
402 Payment Required and belongs to the paid Pro tier — so they come from
`defillama-datasets.llama.fi`, the dataset CDN the DefiLlama front-end itself
reads. That CDN serves one document per protocol at roughly 2.25 MB, of which
about 8 KB is useful.

Hence the cache: the extraction, never the body, is stored for 24 hours.
Schedules are near-static, so this loses nothing and turns a 2.25 MB fetch into
a Redis read for the rest of the day.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from cmi_common.cache import Cache
from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.unlocks import Unlock, next_unlock

logger = logging.getLogger(__name__)

SERVICE = "collector-defillama"
API_BASE = "https://api.llama.fi"
DATASETS_BASE = "https://defillama-datasets.llama.fi"
UNLOCK_KEY = "defillama:unlock:{coin_id}"
#: Schedules are published well in advance and change rarely.
UNLOCK_TTL_SECONDS = 86_400


class LlamaClient:
    def __init__(
        self,
        cache: Cache,
        *,
        timeout: float = 15.0,
        unlock_timeout: float = 30.0,
        rate_limit_per_min: int = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cache = cache
        self._unlock_timeout = unlock_timeout
        self._rate_limit = rate_limit_per_min
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, *, timeout: float | None = None) -> Any:
        if not await self._cache.allow("defillama", self._rate_limit, 60):
            raise RuntimeError("DefiLlama rate limit exceeded")
        # httpx reads an explicit timeout=None as "no timeout at all", not as
        # "use the client default", so the override is passed only when set.
        overrides = {"timeout": timeout} if timeout is not None else {}
        try:
            resp = await self._client.get(url, **overrides)
            resp.raise_for_status()
            UPSTREAM_REQUESTS.labels(SERVICE, "defillama", "ok").inc()
            return resp.json()
        except httpx.HTTPError:
            UPSTREAM_REQUESTS.labels(SERVICE, "defillama", "error").inc()
            raise

    async def protocols(self) -> list[dict[str, Any]]:
        """Every protocol with its TVL, 7d change, slug and gecko_id."""
        return await self._get(f"{API_BASE}/protocols")

    async def fees(self) -> dict[str, dict[str, Any]]:
        """Fee rows keyed by protocol **slug**.

        Not by gecko_id: the fees payload carries none — verified against the
        live API, 0 of 2,514 rows have one. The caller joins slug -> gecko_id
        through the protocols response.

        The two exclude parameters are not optional. Without them the response
        embeds full historical chart series and weighs 24.6 MB, which at a 600s
        cadence is 3.5 GB a day to read a handful of numbers. With them it is
        3.7 MB.
        """
        payload = await self._get(
            f"{API_BASE}/overview/fees"
            "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
        )
        rows = payload.get("protocols", []) if isinstance(payload, dict) else []
        return {row["slug"]: row for row in rows if row.get("slug")}

    async def emission_slugs(self) -> set[str]:
        """The protocol slugs that have a published unlock schedule (~4 KB)."""
        return set(await self._get(f"{DATASETS_BASE}/emissionsProtocolsList"))

    async def unlock(self, slug: str, coin_id: str) -> Unlock | None:
        """The pending unlock for a protocol, cached for a day.

        Returns None both when the schedule is known and empty and when the
        fetch failed. The caller distinguishes the two by whether the coin id is
        present in the map it builds — a failure must leave the key absent so the
        axis reports "unknown" rather than "nothing coming".
        """
        key = UNLOCK_KEY.format(coin_id=coin_id)
        cached = await self._cache.get_json(key)
        if cached is not None:
            return self._from_cache(cached)
        try:
            document = await self._get(
                f"{DATASETS_BASE}/emissions/{slug}", timeout=self._unlock_timeout
            )
        except Exception:
            logger.warning("unlock fetch failed for %s", slug, exc_info=True)
            return None
        unlock = next_unlock(document)
        await self._cache.set_json(
            key,
            {
                "at": unlock.at.isoformat() if unlock else None,
                "pct_supply": unlock.pct_supply if unlock else None,
            },
            ttl_seconds=UNLOCK_TTL_SECONDS,
        )
        return unlock

    @staticmethod
    def _from_cache(cached: dict[str, Any]) -> Unlock | None:
        at = cached.get("at")
        pct = cached.get("pct_supply")
        if not at or pct is None:
            return None
        return Unlock(at=datetime.fromisoformat(at), pct_supply=float(pct))
