"""Provider protocol + rate-limit header parsing.

A provider is one platform's poller. It knows how to call its API and map the
result to ``RawItem`` list, declares its rate-limit budget, and raises
``RateLimitedError`` when throttled. It owns no DB/Kafka knowledge — the
``AdaptivePollLoop`` drives it and persists its output.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from .raw import RawItem


@runtime_checkable
class Provider(Protocol):
    #: stable platform id (also the ``raw_content.source`` value + throttle key)
    name: str
    #: "social" | "news"
    kind: str
    #: (max_calls, window_seconds) proactive budget for the Redis token bucket
    rate_limit: tuple[int, int]

    async def fetch(self) -> list[RawItem]: ...

    async def close(self) -> None: ...


# A rate-limit pause is never legitimately longer than this. Header values are
# capped at it so an epoch-style ``X-RateLimit-Reset`` (a huge absolute
# timestamp, not a delta) can't pause a source for years — the loop re-probes
# after the cap instead.
_MAX_RETRY_AFTER = 3600.0


def parse_retry_after(response: httpx.Response, *, default: float) -> float:
    """Seconds to wait before retrying, learned from the API's own headers.

    Priority: ``Retry-After`` (delta-seconds) → ``X-RateLimit-Reset``
    (delta-seconds) → ``default``. HTTP-date ``Retry-After`` is not emitted by
    the crypto APIs we use, so only the numeric form is handled; anything
    unparseable falls back to ``default``. Header-derived values are capped at
    ``_MAX_RETRY_AFTER`` (1h) to guard against epoch-style reset values.
    """
    ra = response.headers.get("retry-after")
    if ra is not None:
        try:
            return min(float(ra), _MAX_RETRY_AFTER)
        except ValueError:
            return default
    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            return min(float(reset), _MAX_RETRY_AFTER)
        except ValueError:
            return default
    return default
