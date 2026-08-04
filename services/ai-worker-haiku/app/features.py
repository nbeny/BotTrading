"""Per-symbol feature store backed by Redis.

The Haiku worker correlates events arriving on different topics for the same
symbol. Rather than hold state in memory (which breaks with replicas), the
latest features per symbol are kept in a short-TTL Redis hash.
"""

from __future__ import annotations

from typing import Any

from cmi_common.cache import Cache

FEATURE_TTL = 900  # 15 min sliding correlation window
KEY = "features:{symbol}"


class FeatureStore:
    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    async def update(self, symbol: str, fields: dict[str, Any]) -> dict[str, Any]:
        current = await self._cache.get_json(KEY.format(symbol=symbol)) or {}
        current.update({k: v for k, v in fields.items() if v is not None})
        await self._cache.set_json(
            KEY.format(symbol=symbol), current, ttl_seconds=FEATURE_TTL
        )
        return current

    async def get(self, symbol: str) -> dict[str, Any]:
        return await self._cache.get_json(KEY.format(symbol=symbol)) or {}


#: La lecture de regime a un TTL propre, plus long que celui des features par
#: symbole. Il reprend le `market_ttl_seconds=3600` que le decision-engine
#: applique a l'etat qu'il tient en memoire.
#:
#: 3600 s ne « couvre » pas la cadence d'alimentation: mesure sur 14 jours de
#: production, 399 mises a jour, l'ecart median vaut 1120 s mais le p95 vaut
#: 4241 s et 38 ecarts sur 399 depassent une heure -- le record etant 40,2 h.
#: Le critere n'est pas la couverture, c'est la fidelite: le moteur est deja
#: aveugle pendant ces trous, et le rejeu hors-ligne doit les reproduire.
#:
#: Deux erreurs symetriques. Heriter du FEATURE_TTL de 900 s raccourcirait la
#: fenetre d'un facteur quatre et ferait disparaitre l'axe news_score pour des
#: lignes qui le gardent aujourd'hui. Allonger le TTL pour combler les trous
#: modifierait le scoring et fausserait la distribution qu'on calibre.
REGIME_TTL = 3600
REGIME_KEY = "market:regime"


class MarketRegimeStore:
    """Lecture de sentiment a l'echelle du marche, hors de tout symbole.

    Le contenu crypto qui ne nomme aucune piece -- regulation, macro,
    incidents d'exchange -- porte le symbole MARKET. Il informe le score de
    tous les symboles sans sentiment propre, mais jamais leur confiance: il
    est identique pour tout le livre.
    """

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    async def set(self, sentiment_score: float) -> None:
        await self._cache.set_json(
            REGIME_KEY, {"sentiment_score": sentiment_score}, ttl_seconds=REGIME_TTL
        )

    async def get(self) -> float | None:
        stored = await self._cache.get_json(REGIME_KEY)
        if not stored:
            return None
        value = stored.get("sentiment_score")
        return None if value is None else float(value)
