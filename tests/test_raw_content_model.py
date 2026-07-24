"""RawContent / ContentSentimentAgg ORM shape + hypertable registration."""

from __future__ import annotations

from cmi_common.db.models import HYPERTABLES, ContentSentimentAgg, RawContent


def test_raw_content_columns() -> None:
    cols = set(RawContent.__table__.columns.keys())
    assert {"source", "kind", "external_id", "text", "symbols", "scored_at"} <= cols
    uq = {tuple(sorted(c.columns.keys())) for c in RawContent.__table__.constraints
          if c.__class__.__name__ == "UniqueConstraint"}
    assert ("external_id", "source") in uq


def test_agg_primary_key() -> None:
    pk = {c.name for c in ContentSentimentAgg.__table__.primary_key.columns}
    assert pk == {"symbol", "kind", "window_start", "window_size"}


def test_raw_content_is_hypertable() -> None:
    assert HYPERTABLES.get("raw_content") == "fetched_at"
