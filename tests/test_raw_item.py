"""RawItem: normalized item every provider returns."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cmi_common.sources import RawItem


def test_minimal_social_item() -> None:
    item = RawItem(
        source="bluesky", kind="social", external_id="at://1", text="$BTC up"
    )
    assert item.symbols == []
    assert item.title is None
    assert item.engagement is None


def test_full_news_item() -> None:
    item = RawItem(
        source="rss",
        kind="news",
        external_id="guid-1",
        title="BTC rallies",
        text="body",
        url="https://x/a",
        symbols=["BTC"],
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert item.kind == "news"
    assert item.symbols == ["BTC"]


def test_kind_must_be_social_or_news() -> None:
    with pytest.raises(ValidationError):
        RawItem(source="x", kind="video", external_id="1")


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        RawItem(source="x", kind="news", external_id="1", bogus=1)
