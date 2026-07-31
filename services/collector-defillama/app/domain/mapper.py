"""Fold DefiLlama's per-deployment rows into one event per token."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cmi_common.events import FundamentalsEvent
from cmi_common.events.base import Source

from .unlocks import Unlock


@dataclass(slots=True)
class _Bucket:
    """Per-token accumulator.

    The two `saw_*` flags are the whole point: a bucket that starts at 0.0
    cannot otherwise tell "no deployment reported this" from "the deployments
    reported zero", and collapsing those is precisely the unknown-vs-measured
    confusion the platform already shipped once.
    """

    tvl: float = 0.0
    weighted_change: float = 0.0
    fees_24h: float = 0.0
    fees_7d: float = 0.0
    fees_prev_7d: float = 0.0
    saw_tvl: bool = False
    saw_fees: bool = False


def emission_key(row: dict[str, Any]) -> str | None:
    """The slug DefiLlama's emissions list uses for this protocol row.

    Emission slugs are *parent* slugs. The list contains ``aave``; ``/protocols``
    only ever contains ``aave-v2``, ``aave-v3`` and their siblings, each carrying
    ``parentProtocol: "parent#aave"``. Matching the emissions list against
    ``slug`` alone covers 220 of the 359 scheduled protocols and misses Aave
    entirely — and it misses them silently, since an unmatched protocol simply
    reports no schedule. Going through the parent covers 335.
    """
    parent = row.get("parentProtocol")
    if parent:
        return str(parent).split("#", 1)[-1] or None
    return row.get("slug") or None


def to_fundamentals_events(
    protocols: list[dict[str, Any]],
    *,
    fees: dict[str, dict[str, Any]],
    unlocks: dict[str, Unlock | None],
    known: dict[str, str],
) -> list[FundamentalsEvent]:
    """One event per tracked token.

    ``known`` maps CoinGecko id -> symbol. A protocol without a ``gecko_id``, or
    whose id we do not track, is dropped rather than matched on its ticker:
    ticker collisions are real and silently wrong.

    ``fees`` is keyed by protocol **slug**, because the fees payload carries no
    ``gecko_id`` at all — verified against the live API, 0 of 2,514 rows have
    one. Keying it by coin id would match nothing and quietly report every
    protocol as fee-less.

    ``unlocks`` maps CoinGecko id -> the pending unlock, or None when the
    schedule is known and empty. A key that is simply absent means DefiLlama
    publishes no schedule for that token, which is a different statement.
    """
    aggregated: dict[str, _Bucket] = {}
    for row in protocols:
        coin_id = row.get("gecko_id")
        if not coin_id or coin_id not in known:
            continue
        bucket = aggregated.setdefault(coin_id, _Bucket())

        raw_tvl = row.get("tvl")
        if raw_tvl is not None:
            bucket.saw_tvl = True
            bucket.tvl += float(raw_tvl)
            # TVL-weighted: a $3M deployment flat and a $1M deployment up 8% is
            # a 2% move for the token, not the 4% a plain mean would report.
            change = row.get("change_7d")
            if change is not None:
                bucket.weighted_change += float(raw_tvl) * float(change)

        # Fees aggregate the same way TVL does. Aave alone is seven deployment
        # rows sharing one gecko_id, so reading any single row would report a
        # fraction of the token's revenue as if it were all of it.
        fee_row = fees.get(row.get("slug") or "")
        if fee_row:
            bucket.saw_fees = True
            bucket.fees_24h += float(fee_row.get("total24h") or 0.0)
            bucket.fees_7d += float(fee_row.get("total7d") or 0.0)
            bucket.fees_prev_7d += float(fee_row.get("total14dto7d") or 0.0)

    events: list[FundamentalsEvent] = []
    for coin_id, bucket in aggregated.items():
        unlock = unlocks.get(coin_id)
        events.append(
            FundamentalsEvent(
                source=Source.DEFILLAMA,
                symbol=known[coin_id],
                coin_id=coin_id,
                tvl_usd=Decimal(str(bucket.tvl)) if bucket.saw_tvl else None,
                tvl_change_pct_7d=(
                    round(bucket.weighted_change / bucket.tvl, 4)
                    if bucket.tvl > 0
                    else None
                ),
                fees_24h_usd=(
                    Decimal(str(bucket.fees_24h)) if bucket.saw_fees else None
                ),
                # Derived: the payload has change_30dover30d but no 7d-over-7d.
                # A zero baseline yields None, not a division and not a 0.0 that
                # would read as "fees held flat".
                fees_change_pct_7d=(
                    round(
                        100.0
                        * (bucket.fees_7d - bucket.fees_prev_7d)
                        / bucket.fees_prev_7d,
                        4,
                    )
                    if bucket.fees_prev_7d > 0
                    else None
                ),
                next_unlock_at=unlock.at if unlock else None,
                next_unlock_pct_supply=unlock.pct_supply if unlock else None,
                has_unlock_schedule=coin_id in unlocks,
            )
        )
    return events
