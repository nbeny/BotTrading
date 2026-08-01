"""Funnel aggregation: pure shaping of already-queried counts.

The funnel answers "why do I get no decisions?". Production measured 7735
analyses, 1 decision and 0 trades in 24h, and the answer was only reachable by
querying the database by hand. These cases pin the shaping so the endpoint can
say it instead.
"""

from __future__ import annotations

from service_modules import load_service_module

funnel = load_service_module("api-gateway", "funnel")


def test_stages_are_ordered_and_complete() -> None:
    out = funnel.build_funnel(
        analyses=1000,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={0: 400, 10: 500, 20: 100},
        block_reasons=[("haiku", "score_below_threshold", 980)],
        factors_presence={2: 900, 3: 100},
    )
    assert [s["stage"] for s in out["stages"]] == [
        "analyses",
        "escalated",
        "decisions",
        "approved",
        "executed",
    ]
    assert out["stages"][0]["count"] == 1000


def test_conversion_pct_is_relative_to_the_previous_stage() -> None:
    out = funnel.build_funnel(
        analyses=1000,
        escalated=100,
        decisions=50,
        approved=25,
        executed=5,
        score_buckets={},
        block_reasons=[],
        factors_presence={},
    )
    by = {s["stage"]: s for s in out["stages"]}
    assert by["escalated"]["conversion_pct"] == 10.0
    assert by["decisions"]["conversion_pct"] == 50.0
    assert by["executed"]["conversion_pct"] == 20.0


def test_zero_upstream_does_not_divide_by_zero() -> None:
    """A fully stalled pipeline is the state this endpoint exists to report, so
    it must be an ordinary result rather than an exception."""
    out = funnel.build_funnel(
        analyses=0,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        block_reasons=[],
        factors_presence={},
    )
    assert all(s["conversion_pct"] == 0.0 for s in out["stages"])


def test_real_production_shape_is_reported_faithfully() -> None:
    """The measured 24h state: analyses flow, escalation works, and almost
    everything dies between escalation and decision."""
    out = funnel.build_funnel(
        analyses=7735,
        escalated=329,
        decisions=1,
        approved=0,
        executed=0,
        score_buckets={10: 2368, 20: 3528, 30: 1210, 70: 218},
        block_reasons=[("risk_engine", "confidence 0.45 below floor", 1)],
        factors_presence={2: 7000, 4: 735},
    )
    by = {s["stage"]: s for s in out["stages"]}
    assert by["escalated"]["conversion_pct"] == 4.25
    assert by["decisions"]["conversion_pct"] == 0.3
    assert by["approved"]["count"] == 0


def test_score_histogram_fills_missing_buckets() -> None:
    """A sparse GROUP BY must still render a full 0-90 histogram; a missing
    bucket is a count of zero, not an absent bar."""
    out = funnel.build_funnel(
        analyses=10,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={20: 7, 50: 3},
        block_reasons=[],
        factors_presence={},
    )
    assert len(out["score_histogram"]) == 10
    assert out["score_histogram"][2] == {"bucket": 20, "count": 7}
    assert out["score_histogram"][0] == {"bucket": 0, "count": 0}


def test_a_score_of_100_lands_in_the_top_bucket() -> None:
    """The scorer is capped at 100; a naive range would drop it entirely."""
    out = funnel.build_funnel(
        analyses=1,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={100: 1},
        block_reasons=[],
        factors_presence={},
    )
    top = out["score_histogram"][-1]
    assert top["bucket"] == 90
    assert top["count"] == 1


def test_factors_presence_covers_zero_to_four() -> None:
    out = funnel.build_funnel(
        analyses=10,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        block_reasons=[],
        factors_presence={2: 10},
    )
    assert out["factors_presence"] == {"0": 0, "1": 0, "2": 10, "3": 0, "4": 0}


def test_block_reasons_are_sorted_by_count_desc() -> None:
    out = funnel.build_funnel(
        analyses=10,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        block_reasons=[
            ("haiku", "gate_not_met", 20),
            ("haiku", "score_below_threshold", 980),
        ],
        factors_presence={},
    )
    assert out["top_block_reasons"][0]["count"] == 980


# ── regroupement des raisons de blocage ──────────────────────────────────────
def test_reasons_differing_only_by_an_embedded_number_are_one_row() -> None:
    """Mesuré en production : les douze premières lignes du panneau étaient
    douze variantes de la même phrase, séparées par le score encastré dedans
    (« score 13 below decision threshold 70 »). Une raison réellement
    différente était donc invisible, poussée sous ~70 doublons."""
    out = funnel.build_funnel(
        window="24h",
        analyses=1,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        factors_presence={},
        block_reasons=[
            ("decision_engine", "score 13 below decision threshold 70", 47654),
            ("decision_engine", "score 14 below decision threshold 70", 23287),
            ("decision_engine", "score 20 below decision threshold 70", 16372),
            ("risk_engine", "confidence 0.42 below floor 0.55", 12),
        ],
    )
    rows = out["top_block_reasons"]
    assert len(rows) == 2
    assert rows[0]["count"] == 47654 + 23287 + 16372
    assert rows[0]["reason"] == "score N below decision threshold N"
    # La raison rare reste visible, ce qui est tout l'intérêt.
    assert rows[1]["reason"] == "confidence N below floor N"


def test_the_same_wording_from_two_stages_stays_separate() -> None:
    """Le stage fait partie de l'identité : fusionner un refus du moteur de
    décision avec un refus du moteur de risque effacerait où meurt le signal."""
    out = funnel.build_funnel(
        window="24h",
        analyses=1,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        factors_presence={},
        block_reasons=[
            ("haiku", "score N too low", 5),
            ("risk_engine", "score N too low", 3),
        ],
    )
    assert len(out["top_block_reasons"]) == 2


def test_a_reason_with_no_number_is_untouched() -> None:
    out = funnel.build_funnel(
        window="24h",
        analyses=1,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        factors_presence={},
        block_reasons=[("haiku", "gate_not_met", 9)],
    )
    assert out["top_block_reasons"][0]["reason"] == "gate_not_met"


def test_decimals_collapse_to_a_single_placeholder() -> None:
    """`0.42` ne doit pas devenir `N.N` : deux nombres là où l'opérateur en lit
    un seul rendrait la phrase illisible."""
    out = funnel.build_funnel(
        window="24h",
        analyses=1,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        factors_presence={},
        block_reasons=[("risk_engine", "confidence 0.42 below floor 0.55", 1)],
    )
    assert out["top_block_reasons"][0]["reason"] == "confidence N below floor N"


def test_the_list_is_capped_and_says_so() -> None:
    """« top » doit vouloir dire quelque chose. Tronquer en silence ferait lire
    une liste partielle comme exhaustive."""
    many = [("haiku", f"raison {chr(97 + i)}", 100 - i) for i in range(15)]
    out = funnel.build_funnel(
        window="24h",
        analyses=1,
        escalated=0,
        decisions=0,
        approved=0,
        executed=0,
        score_buckets={},
        factors_presence={},
        block_reasons=many,
    )
    assert len(out["top_block_reasons"]) == funnel.MAX_BLOCK_REASONS
    assert out["block_reasons_truncated"] is True


def test_collapse_reason_is_the_shared_number_normaliser() -> None:
    """The stage drawer groups rejection reasons the same way the funnel does;
    two different collapsers would make the two panels disagree on the same rows."""
    assert funnel.collapse_reason("score 13 below threshold 70") == (
        "score N below threshold N"
    )
