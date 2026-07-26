"""Golden cases captured from production on 2026-07-26.

These are the rows that motivated the whole normalization layer. If one of them
regresses, the pipeline is mis-attributing sentiment again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmi_common.sources import ContentNormalizer, RawItem, SymbolLexicon
from cmi_common.sources.vocab import SEED_COINS

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "production_rows.json").read_text(
        encoding="utf-8"
    )
)

# The live top-N universe holds these too. They are the tickers NewsData
# hallucinated onto these very articles, so the fixtures only prove anything if
# the lexicon actually knows them -- otherwise they would be rejected for being
# out of universe rather than for being uncorroborated.
_EXTRA = [
    {"ticker": "JST", "name": "JUST"},
    {"ticker": "KEEP", "name": "Keep Network"},
    {"ticker": "ONE", "name": "Harmony"},
    {"ticker": "BAND", "name": "Band Protocol"},
]
LEXICON = SymbolLexicon.from_coins(
    [{"ticker": t, "name": n} for t, n in SEED_COINS] + _EXTRA
)


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_production_row(case: dict) -> None:
    item = RawItem(
        source=case["source"],
        kind=case["kind"],
        external_id=case["case"],
        title=case["title"],
        text=case["text"],
        symbols=case["provider_symbols"],
    )
    result = ContentNormalizer(LEXICON).apply([item])
    if case["expect_kept"]:
        assert result.kept, f"{case['case']}: expected kept, was dropped"
        assert result.kept[0].symbols == case["expect_symbols"]
    else:
        assert not result.kept, f"{case['case']}: expected dropped, was kept"
