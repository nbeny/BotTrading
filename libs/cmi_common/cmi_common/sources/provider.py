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

    async def fetch(self) -> list[RawItem]:
        ...

    async def close(self) -> None:
        ...


def parse_retry_after(response: httpx.Response, *, default: float) -> float:
    """Seconds to wait before retrying, learned from the API's own headers.

    Priority: ``Retry-After`` (delta-seconds) → ``X-RateLimit-Reset``
    (delta-seconds; large values interpreted as an epoch are still bounded by
    the caller) → ``default``. HTTP-date ``Retry-After`` is not emitted by the
    crypto APIs we use, so only the numeric form is handled; anything
    unparseable falls back to ``default``.
    """
    ra = response.headers.get("retry-after")
    if ra is not None:
        try:
            return float(ra)
        except ValueError:
            return default
    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            return float(reset)
        except ValueError:
            return default
    return default
