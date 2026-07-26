"""LexiconNormalizer: async adapter over the pure normalizer, plus drop metrics."""

from __future__ import annotations

from cmi_common.observability import CONTENT_DROPPED
from cmi_common.sources import LexiconLoader, LexiconNormalizer, RawItem


class FakeCache:
    async def get_json(self, key: str) -> object:
        return [{"ticker": "BTC", "name": "Bitcoin"}]


def _item(title: str, external_id: str = "1") -> RawItem:
    return RawItem(source="stub", kind="news", external_id=external_id, title=title)


async def test_returns_only_kept_items_with_resolved_symbols() -> None:
    norm = LexiconNormalizer(LexiconLoader(FakeCache()), service="collector-news")
    out = await norm.normalize(
        [_item("Bitcoin rallies", "1"), _item("Local football final", "2")]
    )
    assert [i.external_id for i in out] == ["1"]
    assert out[0].symbols == ["BTC"]


async def test_increments_the_drop_counter_with_source_and_reason() -> None:
    norm = LexiconNormalizer(LexiconLoader(FakeCache()), service="collector-news")
    before = CONTENT_DROPPED.labels(
        "collector-news", "stub", "not_relevant"
    )._value.get()
    await norm.normalize([_item("Local football final", "2")])
    after = CONTENT_DROPPED.labels(
        "collector-news", "stub", "not_relevant"
    )._value.get()
    assert after == before + 1
