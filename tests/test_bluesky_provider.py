"""BlueskyProvider: searchPosts -> one RawItem per crypto post.

These tests referenced `SEARCH_URL`, a constant that stopped existing when
authenticated Bluesky landed and split it into `PUBLIC_SEARCH_URL` /
`AUTH_SEARCH_URL`. They had been failing on an AttributeError ever since, which
meant the authenticated path — the reason for the split — shipped with no
coverage at all. It has some now.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from service_modules import load_service_module

from cmi_common.sources import RateLimitedError

bsky = load_service_module("collector-social", "providers.bluesky")


def _post(text: str, did: str, likes: int = 0) -> dict:
    return {
        "uri": f"at://{did}/{likes}",
        "record": {"text": text},
        "author": {"did": did},
        "likeCount": likes,
        "repostCount": 0,
        "replyCount": 0,
    }


@respx.mock
async def test_aggregates_cashtags() -> None:
    respx.get(bsky.PUBLIC_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "posts": [
                    _post("$BTC breakout bullish", "did:a", likes=10),
                    _post("still holding $BTC", "did:b", likes=5),
                    _post("$ETH strong", "did:c", likes=2),
                ]
            },
        )
    )
    provider = bsky.BlueskyProvider()

    items = await provider.fetch()
    await provider.close()

    assert {i.source for i in items} == {"bluesky"}
    assert all(i.kind == "social" for i in items)
    btc = [i for i in items if "BTC" in i.symbols]
    assert len(btc) == 2  # two posts mention $BTC
    assert btc[0].external_id  # a stable post id
    assert "$BTC" in btc[0].text


@respx.mock
async def test_a_post_with_no_cashtag_is_dropped() -> None:
    """The query is a plain keyword search, so most results mention no symbol.
    Keeping them would flood raw_content with rows sentiment cannot attribute."""
    respx.get(bsky.PUBLIC_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"posts": [_post("crypto is interesting today", "did:a")]}
        )
    )
    provider = bsky.BlueskyProvider()
    assert await provider.fetch() == []
    await provider.close()


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get(bsky.PUBLIC_SEARCH_URL).mock(return_value=httpx.Response(429))
    provider = bsky.BlueskyProvider()

    with pytest.raises(RateLimitedError):
        await provider.fetch()
    await provider.close()


# ── authenticated path ────────────────────────────────────────────────────────
@respx.mock
async def test_credentials_switch_the_host_and_bearer_the_request() -> None:
    """With an app password the provider must hit bsky.social, not the public
    mirror: the public endpoint ignores the token and silently returns the
    unauthenticated result set, so a wrong host looks like it works."""
    respx.post(bsky.SESSION_URL).mock(
        return_value=httpx.Response(200, json={"accessJwt": "jwt-1"})
    )
    route = respx.get(bsky.AUTH_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"posts": [_post("$BTC up", "did:a")]})
    )
    provider = bsky.BlueskyProvider(identifier="me.bsky.social", app_password="pw")

    items = await provider.fetch()
    await provider.close()

    assert len(items) == 1
    assert route.calls[0].request.headers["Authorization"] == "Bearer jwt-1"


@respx.mock
async def test_the_session_is_created_once_and_reused() -> None:
    """A createSession per fetch would burn that endpoint's own rate limit for
    nothing, and the poll loop runs continuously."""
    session = respx.post(bsky.SESSION_URL).mock(
        return_value=httpx.Response(200, json={"accessJwt": "jwt-1"})
    )
    respx.get(bsky.AUTH_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"posts": []})
    )
    provider = bsky.BlueskyProvider(identifier="me.bsky.social", app_password="pw")

    await provider.fetch()
    await provider.fetch()
    await provider.close()

    assert session.call_count == 1


@respx.mock
async def test_an_expired_token_is_refreshed_once_then_gives_up() -> None:
    """A JWT outlives neither the process nor the day. Retrying forever on a 401
    would spin against a permanently bad credential; not retrying at all would
    kill the source the first time a token expired."""
    session = respx.post(bsky.SESSION_URL).mock(
        return_value=httpx.Response(200, json={"accessJwt": "jwt-1"})
    )
    search = respx.get(bsky.AUTH_SEARCH_URL).mock(return_value=httpx.Response(401))
    provider = bsky.BlueskyProvider(identifier="me.bsky.social", app_password="pw")

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch()
    await provider.close()

    assert session.call_count == 2  # initial + one refresh
    assert search.call_count == 2  # the retry is attempted exactly once
