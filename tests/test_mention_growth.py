"""Mention growth: the social_score axis finally has a producer.

`Features.social_growth` was read by the decision engine from the day it was
written and populated in 0 of 12,183 production signals -- nothing computed it.
The producer disappeared when social ingestion moved from Kafka events to
raw_content, and the consumer was never updated, leaving 20% of the model weight
permanently at zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cmi_common.sources import FakeContentRepository

# The metric measures the last COMPLETE hour, never the hour in progress:
# a partial bucket compared against complete ones read -1.0 for every symbol
# in production. NOW is 12:30, so the measured hour is 11:00.
NOW = datetime(2026, 7, 26, 12, 30, tzinfo=UTC)
LIVE = NOW.replace(minute=0, second=0, microsecond=0)  # 12:00, ignored
HOUR = LIVE - timedelta(hours=1)  # 11:00, measured


def _repo(buckets: dict[datetime, int], symbol: str = "BTC") -> FakeContentRepository:
    repo = FakeContentRepository()
    for bucket, mentions in buckets.items():
        repo.aggregates[(symbol, "social", bucket)] = {
            "mentions": mentions,
            "score_sum": 0.0,
            "confidence_sum": 0.0,
            "weighted_score_sum": 0.0,
            "engagement_sum": 0.0,
        }
    return repo


async def test_chatter_matching_the_usual_rate_reads_as_flat() -> None:
    repo = _repo({HOUR: 4, HOUR - timedelta(hours=1): 4, HOUR - timedelta(hours=2): 4})
    assert await repo.mention_growth(["BTC"], now=NOW) == {"BTC": 0.0}


async def test_a_doubling_reads_as_plus_one() -> None:
    repo = _repo({HOUR: 8, HOUR - timedelta(hours=1): 4, HOUR - timedelta(hours=2): 4})
    assert await repo.mention_growth(["BTC"], now=NOW) == {"BTC": 1.0}


async def test_a_halving_reads_as_minus_one_half() -> None:
    repo = _repo({HOUR: 2, HOUR - timedelta(hours=1): 4, HOUR - timedelta(hours=2): 4})
    assert await repo.mention_growth(["BTC"], now=NOW) == {"BTC": -0.5}


async def test_silence_this_hour_reads_as_minus_one() -> None:
    repo = _repo({HOUR - timedelta(hours=1): 4, HOUR - timedelta(hours=2): 4})
    assert await repo.mention_growth(["BTC"], now=NOW) == {"BTC": -1.0}


async def test_a_permanently_noisy_coin_does_not_read_as_permanently_surging() -> None:
    # The baseline is the symbol's own normal, so volume alone carries no
    # signal -- only the change does. A coin at a steady 500/hour is flat.
    loud = _repo({HOUR: 500, HOUR - timedelta(hours=1): 500})
    quiet = _repo({HOUR: 3, HOUR - timedelta(hours=1): 3})
    assert await loud.mention_growth(["BTC"], now=NOW) == await quiet.mention_growth(
        ["BTC"], now=NOW
    )


async def test_no_baseline_is_omitted_rather_than_reported_as_flat() -> None:
    # "We have never heard of this symbol" and "this symbol is at its usual
    # level" are different claims, and the scorer treats them differently: an
    # absent value leaves social_score out of the confidence, a 0.0 does not.
    assert await _repo({HOUR: 9}).mention_growth(["BTC"], now=NOW) == {}


async def test_an_unknown_symbol_is_simply_absent() -> None:
    repo = _repo({HOUR: 5, HOUR - timedelta(hours=1): 5})
    assert "DOGE" not in await repo.mention_growth(["BTC", "DOGE"], now=NOW)


async def test_history_older_than_the_baseline_window_is_ignored() -> None:
    # A burst two days ago must not depress today's reading forever.
    repo = _repo(
        {
            HOUR: 4,
            HOUR - timedelta(hours=1): 4,
            HOUR - timedelta(hours=48): 10_000,
        }
    )
    assert await repo.mention_growth(["BTC"], now=NOW) == {"BTC": 0.0}


async def test_an_empty_request_does_not_query() -> None:
    assert await _repo({HOUR: 1}).mention_growth([], now=NOW) == {}


async def test_the_hour_in_progress_is_ignored() -> None:
    # Measured against production at 21:37: the live bucket held 3 mentions
    # where the previous complete hour held 73, so comparing the partial hour
    # against complete ones reported -1.0 for every symbol on the board. The
    # partial bucket must not count, however busy or empty it looks.
    repo = _repo(
        {
            LIVE: 999,  # hour in progress
            HOUR: 4,  # last complete hour -- this is what is measured
            HOUR - timedelta(hours=1): 4,
        }
    )
    assert await repo.mention_growth(["BTC"], now=NOW) == {"BTC": 0.0}
