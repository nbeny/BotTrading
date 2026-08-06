"""Pure assembly for GET /decisions/{event_id}/explain.

Reuses dossier.build_score / build_pipeline so the inspector can never drift
from the /market drawer. The Haiku triage factors are a DISJOINT namespace
from the eight scoring axes (see CLAUDE.md) — they are surfaced under
`triage`, never merged into `score`.
"""

from __future__ import annotations

from typing import Any

from .dossier import build_pipeline, build_score


def build_explain(
    event_id: str,
    *,
    decision: Any | None,
    journal: Any | None,
    rejection: Any | None,
    trace: dict[str, Any] | None,
    counterfactual: dict[str, Any] | None,
) -> dict[str, Any]:
    symbol = getattr(decision, "symbol", None) or getattr(journal, "symbol", None)
    triage = None
    risk = None
    if journal is not None:
        triage = {
            "score": journal.score,
            "confidence": journal.confidence,
            "factors": journal.factors or {},
            "dominant_factor": journal.dominant_factor,
            "escalated": bool(journal.escalated),
            "sonnet_called": bool(journal.sonnet_called),
            "sonnet_validated": journal.sonnet_validated,
            "sonnet_score": journal.sonnet_score,
            "sonnet_direction": journal.sonnet_direction,
            "skip_reason": journal.skip_reason,
        }
        risk = {"verdict": journal.risk_verdict, "reason": journal.risk_reason}
    return {
        "id": event_id,
        "symbol": symbol,
        "direction": getattr(decision, "direction", None),
        "score": build_score(decision),
        "triage": triage,
        "risk": risk,
        "pipeline": build_pipeline(journal, rejection),
        "counterfactual": counterfactual,
        "trace": trace,
        "correlation_id": (
            getattr(decision, "correlation_id", None)
            or getattr(journal, "correlation_id", None)
        ),
    }
