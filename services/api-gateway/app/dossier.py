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


def build_score(decision: Any | None) -> dict:
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
