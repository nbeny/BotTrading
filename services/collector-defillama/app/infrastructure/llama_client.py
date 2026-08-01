"""HTTP access to DefiLlama, plus the cache that makes unlocks affordable.

Two hosts are in play. `api.llama.fi` serves the bulk TVL and fee endpoints.
Unlock schedules are *not* on the free API — `api.llama.fi/emissions` answers
402 Payment Required and belongs to the paid Pro tier — so they come from
`defillama-datasets.llama.fi`, the dataset CDN the DefiLlama front-end itself
reads. That CDN serves one document per protocol at roughly 2.25 MB, of which
about 8 KB is useful. There is no single `base_url` this client can be built
on top of the way the CoinGecko client is: the two hosts are genuinely
different services on different payment tiers, not two paths under one API.

Hence the cache: the extraction, never the body, is stored for 24 hours.
Schedules are near-static, so this loses nothing and turns a 2.25 MB fetch into
a Redis read for the rest of the day.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from cmi_common.cache import Cache
from cmi_common.observability import UPSTREAM_REQUESTS

from ..domain.unlocks import Unlock, next_unlock

logger = logging.getLogger(__name__)

SERVICE = "collector-defillama"
API_BASE = "https://api.llama.fi"
DATASETS_BASE = "https://defillama-datasets.llama.fi"
# Keyed by coin_id, not slug: the caller collapses every deployment of a
# protocol (Aave is seven rows on the live API) onto the one coin id they
# share. Keying this cache by slug instead would fetch and cache the same
# 2.25 MB document once per deployment for no new information.
UNLOCK_KEY = "defillama:unlock:{coin_id}"
#: Schedules are published well in advance and change rarely.
UNLOCK_TTL_SECONDS = 86_400


class LlamaClient:
    """Async wrapper over DefiLlama's free TVL/fees API and dataset CDN."""

    def __init__(
        self,
        cache: Cache,
        *,
        timeout: float = 15.0,
        unlock_timeout: float = 30.0,
        rate_limit_per_min: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cache = cache
        self._unlock_timeout = unlock_timeout
        self._rate_limit = rate_limit_per_min
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        # httpx read timeout override, not an asyncio cancellation scope.
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> Any:
        if not await self._cache.allow("defillama", self._rate_limit, 60):
            # Expected steady state, not exceptional: one shared "defillama"
            # bucket covers /protocols, /overview/fees, the emissions list and
            # every 2.25 MB per-protocol document. Label it distinctly from a
            # real fetch failure before raising, so a throttled cycle is
            # visible in metrics rather than looking identical to a clean one.
            UPSTREAM_REQUESTS.labels(SERVICE, "defillama", "throttled").inc()
            raise RuntimeError("DefiLlama rate limit exceeded")
        # An explicit timeout=None would tell httpx "no timeout at all" rather
        # than "use the client default", so USE_CLIENT_DEFAULT is the sentinel
        # that actually means "unset" here.
        effective_timeout = timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
        try:
            resp = await self._client.get(url, params=params, timeout=effective_timeout)
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
            f"{API_BASE}/overview/fees",
            params={
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
            },
        )
        rows = payload.get("protocols", []) if isinstance(payload, dict) else []
        return {row["slug"]: row for row in rows if row.get("slug")}

    async def emission_slugs(self) -> set[str]:
        """The protocol slugs that have a published unlock schedule (~4 KB)."""
        return set(await self._get(f"{DATASETS_BASE}/emissionsProtocolsList"))

    async def cached_unlock(self, coin_id: str) -> tuple[bool, Unlock | None]:
        """The cached reading for a coin id, without ever fetching.

        Returns ``(False, None)`` on a miss, which the caller must keep distinct
        from ``(True, None)`` — "read, and nothing is scheduled". Collapsing
        those is what makes a token with a live schedule report as untracked,
        and an untracked token scores *better* than a measured one because
        renormalisation drops an absent axis rather than penalising it.

        This exists so the caller can report every token it already knows about
        on every cycle, and spend its fetch budget only on the misses. The
        budget bounds 2.25 MB downloads; it has no business bounding Redis
        reads.
        """
        cached = await self._cache.get_json(UNLOCK_KEY.format(coin_id=coin_id))
        if cached is None:
            return False, None
        return self._from_cache(cached)

    async def unlock(self, slug: str, coin_id: str) -> Unlock | None:
        """The pending unlock for a protocol, cached for a day.

        Returns None only when the schedule was actually read and turned out
        to be empty. Any failure to read it — a network error, a throttled
        request, or `next_unlock` raising because an unlock is scheduled but
        cannot be sized — propagates instead of being swallowed here.

        That is deliberate: the caller's own try/except around this call is
        what leaves the coin id out of its result map, and that omission is
        the only signal that distinguishes "unknown" from "measured, nothing
        coming". If every failure became a `None` return here, the caller
        could no longer tell the two apart, and a throttled fetch would be
        reported as a clean, empty unlock schedule — the same inversion
        `next_unlock`'s own `ValueError` exists to prevent, reached through a
        different door.
        """
        key = UNLOCK_KEY.format(coin_id=coin_id)
        cached = await self._cache.get_json(key)
        if cached is not None:
            hit, unlock = self._from_cache(cached)
            if hit:
                return unlock
            logger.warning("unrecognised cache entry for %s, treating as a miss", key)
        document = await self._get(
            f"{DATASETS_BASE}/emissions/{quote(slug, safe='')}",
            timeout=self._unlock_timeout,
        )
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
    def _from_cache(cached: dict[str, Any]) -> tuple[bool, Unlock | None]:
        """Parse a cached extraction, or report a miss for anything unrecognised.

        Entries live 24h under a key that carries no schema version, so a
        future change to the payload shape would otherwise leave this reader
        parsing a day of the previous writer's rows — and, worst case, serving
        a confident absence for data it never actually read. Anything that is
        not recognisably one of the two shapes this module itself writes is
        therefore treated as a cache miss (falls through to a fetch), not as
        "nothing coming".
        """
        if "at" not in cached or "pct_supply" not in cached:
            return False, None
        at = cached["at"]
        pct = cached["pct_supply"]
        if at is None and pct is None:
            return True, None
        if isinstance(at, str) and isinstance(pct, (int, float)):
            return True, Unlock(at=datetime.fromisoformat(at), pct_supply=float(pct))
        return False, None
