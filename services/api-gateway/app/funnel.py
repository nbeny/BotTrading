"""Pipeline funnel: how many signals survive each stage, and why the rest don't.

Answers the operational question "why do I get no decisions?". Before this
existed the answer was only reachable by querying the database by hand — a 24h
production sample showed 7735 analyses, 329 escalations, 1 decision and 0 trades,
with the collapse happening between escalation and decision. Nothing in the
product surfaced that.

The shaping below is pure so it can be unit-tested without a database; the
queries live in ``read_api.systems_funnel``.
"""

from __future__ import annotations

from datetime import datetime, timezone

STAGE_ORDER = ["analyses", "escalated", "decisions", "approved", "executed"]
SCORE_BUCKET_WIDTH = 10
N_SCORE_BUCKETS = 100 // SCORE_BUCKET_WIDTH
N_FACTORS = 4


def _pct(numerator: int, denominator: int) -> float:
    """Conversion relative to the previous stage.

    A zero upstream reports 0.0 rather than raising: a stalled pipeline is the
    state this endpoint exists to describe, not an error condition.
    """
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _histogram(score_buckets: dict[int, int]) -> list[dict[str, int]]:
    """Dense 0-90 histogram from a sparse GROUP BY.

    A missing bucket is a count of zero, not an absent bar — a gap-toothed chart
    reads as "no data here" when it means "no signals scored here".

    The scorer is capped at 100 inclusive, so integer division puts a perfect
    score in a bucket of its own. It is folded into the top bucket instead;
    otherwise the one signal that scored full marks would vanish from the chart.
    """
    counts = [0] * N_SCORE_BUCKETS
    for raw_bucket, count in score_buckets.items():
        index = min(raw_bucket // SCORE_BUCKET_WIDTH, N_SCORE_BUCKETS - 1)
        if index >= 0:
            counts[index] += count
    return [
        {"bucket": i * SCORE_BUCKET_WIDTH, "count": c} for i, c in enumerate(counts)
    ]


def build_funnel(
    *,
    analyses: int,
    escalated: int,
    decisions: int,
    approved: int,
    executed: int,
    score_buckets: dict[int, int],
    block_reasons: list[tuple[str, str, int]],
    factors_presence: dict[int, int],
    window: str = "24h",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(tz=timezone.utc)
    counts = [analyses, escalated, decisions, approved, executed]
    stages = [
        {
            "stage": name,
            "count": count,
            # The first stage has no upstream, so it converts from itself: 100%
            # when non-empty, 0% when nothing is flowing at all.
            "conversion_pct": _pct(count, counts[i - 1] if i else count),
        }
        # strict: STAGE_ORDER and counts must stay in lockstep — a silently
        # truncated zip would drop a stage from the funnel without any error.
        for i, (name, count) in enumerate(zip(STAGE_ORDER, counts, strict=True))
    ]
    return {
        "window": window,
        "stages": stages,
        "score_histogram": _histogram(score_buckets),
        # Always 0..N_FACTORS: a score built on 2 of 4 factors is not comparable
        # to one built on 4 of 4, and the operator cannot tune a threshold
        # without knowing which of the two they are looking at.
        "factors_presence": {
            str(k): factors_presence.get(k, 0) for k in range(N_FACTORS + 1)
        },
        "top_block_reasons": [
            {"stage": stage, "reason": reason, "count": count}
            for stage, reason, count in sorted(
                block_reasons, key=lambda r: r[2], reverse=True
            )
        ],
        "updated_at": now.isoformat(),
    }
