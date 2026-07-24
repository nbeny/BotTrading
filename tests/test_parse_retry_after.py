"""parse_retry_after: derive resume-after seconds from rate-limit headers."""

from __future__ import annotations

import httpx

from cmi_common.sources import parse_retry_after


def _resp(headers: dict[str, str]) -> httpx.Response:
    return httpx.Response(429, headers=headers)


def test_retry_after_seconds() -> None:
    assert parse_retry_after(_resp({"Retry-After": "42"}), default=99) == 42.0


def test_x_ratelimit_reset_delta_seconds() -> None:
    # A small value is treated as a delta in seconds, not an epoch.
    assert parse_retry_after(_resp({"x-ratelimit-reset": "30"}), default=99) == 30.0


def test_no_headers_uses_default() -> None:
    assert parse_retry_after(_resp({}), default=77) == 77.0


def test_retry_after_takes_priority_over_reset() -> None:
    r = _resp({"Retry-After": "10", "x-ratelimit-reset": "999"})
    assert parse_retry_after(r, default=99) == 10.0
