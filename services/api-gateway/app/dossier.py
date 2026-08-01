"""Assemblage du dossier d'un token — fonctions pures.

Séparé de ``read_api`` pour deux raisons : ce module ne touche ni la base ni
FastAPI et se teste donc sans fixture, et ``read_api`` dépasse déjà 1500 lignes.
Il ne doit jamais importer ``read_api`` en retour (cycle d'import) — d'où le
``_iso`` local plutôt qu'un import du helper homonyme.

La règle qui gouverne tout ce fichier : une valeur non mesurée est ``None`` ou
une clé absente, jamais un ``0``. Le scoring v2 renormalise sur le poids des
axes *présents*, donc un axe absent est exclu du calcul ; le rapporter à 0.0 le
transformerait en mesure au pire, ce qui déplace le score vers le bas sans que
rien ne le signale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: Les sept axes de decision-engine/app/scoring.py::WEIGHTS, dans l'ordre
#: d'affichage. Dupliqués ici et non importés : api-gateway ne dépend pas du
#: decision-engine, et cette liste ne bouge que lors d'un changement de modèle
#: de scoring, qui touchera de toute façon les deux fichiers.
AXIS_KEYS: tuple[str, ...] = (
    "volume_growth",
    "social_score",
    "news_score",
    "market_trend",
    "liquidity_score",
    "positioning",
    "fundamentals",
)


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def build_score(decision: Any | None) -> dict[str, Any]:
    """Décomposition par axe du dernier score connu pour un symbole.

    La source est ``Decision.payload["meta"]["breakdown"]`` : ``engine.py`` y
    publie le ``breakdown`` du scoring v2, et le persister sérialise
    l'événement entier dans la colonne ``payload``.

    **Pas** ``DecisionJournal.factors`` : celui-là porte le triage Haiku à
    quatre facteurs (``momentum``/``volume``/``sentiment``/``liquidity``), un
    espace de noms disjoint. L'y lire renverrait ``{}`` en permanence, soit
    sept tirets à l'écran indiscernables d'un vrai « rien mesuré ».

    ``axes`` ne contient que les axes **mesurés**. L'absence d'une clé est
    l'information : elle dit « non mesuré », pas « nul ».
    """
    if decision is None:
        return {
            "value": None,
            "confidence": None,
            "axes": {},
            "axes_total": len(AXIS_KEYS),
            "insufficient_evidence": False,
            "computed_at": None,
        }

    breakdown = ((decision.payload or {}).get("meta") or {}).get("breakdown") or {}
    # `is not None` et non un test de vérité : un axe mesuré à 0.0 est une
    # mesure et doit être conservé.
    axes = {k: float(breakdown[k]) for k in AXIS_KEYS if breakdown.get(k) is not None}

    # Un breakdown vide sur une décision existante veut dire que le poids
    # présent était sous `_MIN_PRESENT_WEIGHT` : scoring.py renvoie alors
    # `ScoreResult(0, 0.0, {})`. Ce 0 n'est pas une mesure, et le publier comme
    # `value` en ferait une — la faute exacte que ce module existe pour éviter.
    insufficient = not axes
    return {
        "value": None if insufficient else decision.opportunity_score,
        "confidence": None if insufficient else decision.confidence,
        "axes": axes,
        "axes_total": len(AXIS_KEYS),
        "insufficient_evidence": insufficient,
        "computed_at": _iso(decision.created_at),
    }


#: `PipelineRejection.stage` porte la *source* de l'événement
#: (`persister.py::_STAGE_BY_SOURCE`), pas l'id d'étage. Le reste de la
#: plateforme — dont le graphe du Command Center — parle le vocabulaire de
#: `systems_pipeline.py::STAGE_SPECS`, et c'est celui que le frontend sait
#: libeller. Sans cette table, un rejet du decision-engine s'afficherait
#: « decision_engine » en brut dans le drawer.
_STAGE_BY_REJECTION_SOURCE = {
    "decision_engine": "decision",
    "risk_engine": "risk",
}


def _normalise_stage(stage: str) -> str:
    """Vocabulaire de rejet -> id d'étage.

    Une source non mappée passe telle quelle, pour la raison exacte que
    `stage_for` invoque déjà : un rejeteur inattendu doit rester visible plutôt
    que d'être silencieusement renommé ou masqué.
    """
    return _STAGE_BY_REJECTION_SOURCE.get(stage, stage)


def _verdict(j: Any) -> tuple[str, str | None, str | None]:
    """``(reached_stage, blocked_at, block_reason)`` pour une ligne de journal.

    Le persister complète la ligne de journal en aval (``risk_verdict``,
    ``execution_event_id``), donc un seul enregistrement porte tout le parcours.

    On ne déclare un blocage que sur preuve positive. « Sonnet appelé, pas de
    décision » peut être un vol en cours autant qu'un abandon : afficher
    « bloqué » y serait une mesure inventée, exactement la faute que ce projet
    cherche à ne plus commettre.
    """
    if j.execution_event_id:
        return "execute", None, None
    if j.risk_verdict == "rejected":
        return "risk", "risk", j.risk_reason
    if j.risk_verdict == "approved":
        return "risk", None, None
    if j.decision_event_id:
        return "decision", None, None
    if j.sonnet_called:
        return "senior", None, None
    if not j.escalated:
        return "triage", "triage", j.skip_reason or "not_escalated"
    return "triage", None, None


def build_pipeline(journal: Any | None, rejection: Any | None) -> dict[str, Any]:
    """Parcours du dernier signal connu pour un symbole.

    ``journal`` fait autorité quand il existe. ``rejection`` n'est qu'un repli,
    pour les refus qui n'ont jamais eu de ligne de journal.
    """
    if journal is None:
        if rejection is None:
            return {
                "reached_stage": None,
                "blocked_at": None,
                "block_reason": None,
                "escalated": None,
                "sonnet_called": None,
                "sonnet_validated": None,
                "last_event_at": None,
            }
        stage = _normalise_stage(rejection.stage)
        return {
            "reached_stage": stage,
            "blocked_at": stage,
            "block_reason": rejection.reason,
            # `None`, pas `False` : sans ligne de journal on ignore si Haiku
            # avait escaladé. Le decision-engine consomme les analyses en
            # parallèle de Sonnet, donc un rejet déterministe ne dit rien du
            # chemin d'escalade. Répondre `False` serait une supposition
            # déguisée en mesure.
            "escalated": None,
            "sonnet_called": None,
            "sonnet_validated": None,
            "last_event_at": _iso(rejection.time),
        }

    reached, blocked, reason = _verdict(journal)
    return {
        "reached_stage": reached,
        "blocked_at": blocked,
        "block_reason": reason,
        "escalated": bool(journal.escalated),
        "sonnet_called": bool(journal.sonnet_called),
        "sonnet_validated": journal.sonnet_validated,
        "last_event_at": _iso(journal.time),
    }
