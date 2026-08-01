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


def build_score(journal: Any | None) -> dict:
    """Décomposition par axe du dernier score connu pour un symbole.

    ``axes`` ne contient que les axes **mesurés**. L'absence d'une clé est
    l'information : elle dit « non mesuré », pas « nul ».
    """
    if journal is None:
        return {
            "value": None,
            "confidence": None,
            "axes": {},
            "axes_total": len(AXIS_KEYS),
            "dominant_factor": None,
            "dominant_factor_share": None,
            "computed_at": None,
        }

    factors = journal.factors or {}
    return {
        "value": journal.score,
        "confidence": journal.confidence,
        # `is not None` et non un test de vérité : un axe mesuré à 0.0 est une
        # mesure et doit être conservé.
        "axes": {k: float(factors[k]) for k in AXIS_KEYS if factors.get(k) is not None},
        "axes_total": len(AXIS_KEYS),
        "dominant_factor": journal.dominant_factor,
        "dominant_factor_share": journal.dominant_factor_share,
        "computed_at": _iso(journal.time),
    }
