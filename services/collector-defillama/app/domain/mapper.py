"""Fold DefiLlama's per-deployment protocol/fee rows into one FundamentalsEvent
per tracked token, and resolve the protocol *family* a row belongs to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cmi_common.events import FundamentalsEvent
from cmi_common.events.base import Source

from .unlocks import Unlock

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Bucket:
    """Per-token accumulator.

    Presence is tracked per *field*, not per row or per bucket. A single
    bucket-level flag cannot express "we saw this protocol, but DefiLlama
    published no 7d change for it", or "we saw a fee row, but its 24h total
    was null" — and on a momentum axis a fabricated 0.0 is not a neutral
    default, it is the assertion that nothing moved.
    """

    tvl: Decimal = Decimal(0)
    saw_tvl: bool = False
    #: TVL-weighted, but weighted only over the rows that reported a change —
    #: a deployment that stayed silent must not dilute one that reported.
    weighted_change: float = 0.0
    change_weight: float = 0.0
    saw_change: bool = False
    fees_24h: Decimal = Decimal(0)
    saw_fees_24h: bool = False
    #: The 7d-over-7d ratio needs both legs from the *same* row: a deployment
    #: that reports total7d but not total14dto7d (or vice versa) contributes
    #: to neither sum, because mixing one deployment's numerator with
    #: another's denominator can flip the sign of the result, not just its
    #: size.
    fees_7d: Decimal = Decimal(0)
    fees_prev_7d: Decimal = Decimal(0)
    saw_fees_ratio: bool = False

    def tvl_usd(self) -> Decimal | None:
        return self.tvl if self.saw_tvl else None

    def tvl_change_pct(self) -> float | None:
        # change_weight can be zero even with saw_change, when every reporting
        # deployment had zero TVL: the weights sum to nothing and the mean is
        # undefined, so it is dropped rather than reported against a zero
        # denominator.
        if not self.saw_change or self.change_weight <= 0:
            return None
        return round(self.weighted_change / self.change_weight, 4)

    def fees_24h_usd(self) -> Decimal | None:
        return self.fees_24h if self.saw_fees_24h else None

    def fees_change_pct(self) -> float | None:
        if not self.saw_fees_ratio:
            return None
        if self.fees_prev_7d <= 0:
            # A zero baseline makes the ratio genuinely undefined -- unlike
            # the value fields above, this None is a real "cannot be computed".
            return None
        delta = self.fees_7d - self.fees_prev_7d
        return round(100.0 * float(delta) / float(self.fees_prev_7d), 4)


def emission_key(row: dict[str, Any]) -> str | None:
    """The slug identifying the protocol *family* a ``/protocols`` row belongs to.

    A family groups a token's deployments: ``aave-v2``, ``aave-v3`` and their
    siblings all carry ``parentProtocol: "parent#aave"`` and belong to the
    ``aave`` family. This key serves two joins, not one:

    - DefiLlama's emissions list is keyed by family. It contains ``aave``;
      ``/protocols`` only ever contains ``aave-v2``, ``aave-v3`` and friends.
      Matching the emissions list against ``slug`` alone covers 220 of the 359
      scheduled protocols and misses Aave entirely — and it misses them
      silently, since an unmatched protocol simply reports no schedule. Going
      through the parent covers 335.
    - A token's ``gecko_id`` sits on exactly one row of its family, and not
      necessarily the largest one: on the live API, ``aave-v2`` carries
      ``gecko_id: "aave"`` at $109M while ``aave-v3``, at $13.7B, carries
      ``None``. Rolling a token's rows up by family — rather than by
      ``gecko_id`` alone — is what makes the other five sixths of Aave's TVL
      reachable at all; see ``to_fundamentals_events``.

    This is parent-*then*-slug, not parent-*or*-slug: a row with a parent never
    falls back to its own slug. Measured against the live API, exactly one
    scheduled protocol (``minebean``, parent ``nu11dotfun``) is listed under its
    own slug while its parent isn't — trying both would cover 336 rather than
    335. Not worth the branch, but worth naming, since a silent miss is exactly
    the failure this function exists to avoid.
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

    ``known`` maps CoinGecko id -> symbol. A protocol family without a
    ``gecko_id`` on any of its rows, or whose id we do not track, is dropped
    rather than matched on its ticker: ticker collisions are real and
    silently wrong.

    A token's ``gecko_id`` is carried by exactly one row of its family, and
    not necessarily the largest — measured live, no two ``/protocols`` rows
    ever share a ``gecko_id``, so joining row-by-row ships only whichever
    single deployment happens to be tagged. Aave alone is seven deployment
    rows; only ``aave-v2`` ($109M) carries the id while ``aave-v3`` ($13.7B)
    does not, so a row-by-row join would ship 0.76% of Aave's TVL as if it
    were the whole token. Rolling up by family (``emission_key``) first is
    what makes multi-row aggregation see the deployments at all; without it
    the largest deployment is the one that goes missing, not the smallest.

    ``fees`` is keyed by protocol **slug**, because the fees payload carries no
    ``gecko_id`` at all — verified against the live API, 0 of 2,514 rows have
    one. Keying it by coin id would match nothing and quietly report every
    protocol as fee-less.

    ``unlocks`` maps CoinGecko id -> the pending unlock, or None when the
    schedule is known and empty. A key that is simply absent means DefiLlama
    publishes no schedule for that token, which is a different statement.
    """
    # A family's gecko_id can sit on any one of its rows -- setdefault takes
    # the first one seen, which is fine, since live data never puts two
    # different ids on one family.
    family_gecko: dict[str, str] = {}
    for row in protocols:
        key = emission_key(row)
        gecko = row.get("gecko_id")
        if key and gecko:
            family_gecko.setdefault(key, gecko)

    aggregated: dict[str, _Bucket] = {}
    for row in protocols:
        key = emission_key(row)
        coin_id = row.get("gecko_id") or (family_gecko.get(key) if key else None)
        if not coin_id or coin_id not in known:
            continue
        bucket = aggregated.setdefault(coin_id, _Bucket())

        raw_tvl = row.get("tvl")
        if raw_tvl is not None and float(raw_tvl) < 0:
            # A negative TVL is nonsense data, not a measurement worth
            # summing -- and letting it through would make one bad row sum
            # to a negative total, which fails FundamentalsEvent's tvl_usd
            # >= 0 constraint and aborts the whole emit loop, silently
            # dropping every other token in the cycle.
            logger.warning(
                "negative tvl %s for %s; skipping row", raw_tvl, row.get("slug")
            )
            raw_tvl = None

        if raw_tvl is not None:
            bucket.saw_tvl = True
            # Converted per row and summed in Decimal: summing float and
            # converting once at the end would carry each float's binary
            # rounding error into the total.
            bucket.tvl += Decimal(str(raw_tvl))
            # TVL-weighted: a $3M deployment flat and a $1M deployment up 8% is
            # a 2% move for the token, not the 4% a plain mean would report.
            change = row.get("change_7d")
            if change is not None:
                bucket.saw_change = True
                bucket.change_weight += float(raw_tvl)
                bucket.weighted_change += float(raw_tvl) * float(change)

        # Fees aggregate the same way TVL does: by family, not by the single
        # row that happens to carry the id.
        slug = row.get("slug")
        fee_row = fees.get(slug) if slug else None
        if fee_row is not None:
            raw_24h = fee_row.get("total24h")
            if raw_24h is not None:
                bucket.saw_fees_24h = True
                bucket.fees_24h += Decimal(str(raw_24h))
            raw_7d = fee_row.get("total7d")
            raw_prev_7d = fee_row.get("total14dto7d")
            if raw_7d is not None and raw_prev_7d is not None:
                bucket.saw_fees_ratio = True
                bucket.fees_7d += Decimal(str(raw_7d))
                bucket.fees_prev_7d += Decimal(str(raw_prev_7d))

    events: list[FundamentalsEvent] = []
    for coin_id, bucket in aggregated.items():
        unlock = unlocks.get(coin_id)
        events.append(
            FundamentalsEvent(
                source=Source.DEFILLAMA,
                symbol=known[coin_id],
                coin_id=coin_id,
                tvl_usd=bucket.tvl_usd(),
                tvl_change_pct_7d=bucket.tvl_change_pct(),
                fees_24h_usd=bucket.fees_24h_usd(),
                # Derived: the payload has change_30dover30d but no 7d-over-7d.
                fees_change_pct_7d=bucket.fees_change_pct(),
                next_unlock_at=unlock.at if unlock else None,
                next_unlock_pct_supply=unlock.pct_supply if unlock else None,
                has_unlock_schedule=coin_id in unlocks,
            )
        )
    return events
