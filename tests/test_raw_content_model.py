"""RawContent / ContentSentimentAgg ORM shape + hypertable registration."""

from __future__ import annotations

from cmi_common.db.models import (
    HYPERTABLES,
    ContentSentimentAgg,
    ContentSentimentAggDaily,
    RawContent,
)


def test_raw_content_columns() -> None:
    cols = set(RawContent.__table__.columns.keys())
    assert {"source", "kind", "external_id", "text", "symbols", "scored_at"} <= cols
    uq = {tuple(sorted(c.columns.keys())) for c in RawContent.__table__.constraints
          if c.__class__.__name__ == "UniqueConstraint"}
    assert ("external_id", "source") in uq


def test_agg_primary_key() -> None:
    """The key is (symbol, kind, bucket_start) at a single hourly resolution.

    It used to carry a `window_size` alongside `window_start`, storing the same
    mentions under several window lengths. The rework made every stored quantity
    additive instead, so any trailing window is a sum over the covering hourly
    buckets — this assertion is what stops a second resolution creeping back in
    and double-counting one mention across overlapping rows.
    """
    pk = {c.name for c in ContentSentimentAgg.__table__.primary_key.columns}
    assert pk == {"symbol", "kind", "bucket_start"}


def test_the_daily_rollup_keeps_the_hourly_shape() -> None:
    """Long-window reads union the two tables, so a column present in one and
    not the other would silently drop from any window long enough to cross the
    compaction boundary."""
    hourly = set(ContentSentimentAgg.__table__.columns.keys())
    daily = set(ContentSentimentAggDaily.__table__.columns.keys())
    assert hourly == daily


def test_raw_content_is_not_hypertable() -> None:
    # Not a hypertable: dedup needs UNIQUE(source, external_id), which Timescale
    # forbids on a hypertable (partition column must be in every unique index).
    assert "raw_content" not in HYPERTABLES
