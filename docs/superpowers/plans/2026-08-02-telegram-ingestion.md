# Telegram Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter Telegram comme douzième source de contenu, de la lecture des canaux MTProto jusqu'au score de sentiment consommé par `decision-engine`.

**Architecture:** Un `TelegramProvider` de plus dans le service `collector-social` existant, piloté par l'`AdaptivePollLoop` déjà en place (toggles opérateur, budget Redis, backoff, normalisation, déduplication). Il écrit dans Postgres `raw_content` — pas de topic Kafka, pas de service neuf, pas de détecteur de symboles spécifique. La liste de canaux vit dans la clé Redis `collectors:runtime` et devient éditable depuis le terminal.

**Tech Stack:** Python 3.12, Telethon 1.44 (MTProto), Pydantic v2, Redis, PostgreSQL, FastAPI, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Next.js + MUI + vitest côté terminal.

**Spec:** `docs/superpowers/specs/2026-08-02-telegram-ingestion-design.md`

---

## ⚠️ Ce plan a été dépassé en cours d'exécution — lire ceci d'abord

Après la tâche 1, on a découvert une implémentation Telegram déjà écrite et jamais mergée,
sur la branche `worktree-telegram-collector` (commit `33562e9`, 656 insertions), cachée dans
un worktree verrouillé sous `.claude/worktrees/`. Elle a été mergée dans
`feat/telegram-ingestion`, et la suite du travail est devenue un **delta** par-dessus, pas
l'exécution des tâches 2 à 7 ci-dessous.

Ce qui a réellement été fait, et où lire la vérité :

| Tâche du plan | Devenu |
|---|---|
| **1** — canaux dans `collectors:runtime` | fait tel quel (`8b9854f`) |
| **2** — mapper pur `telegram_map.py` | **abandonné.** Le provider existant teste déjà la règle absent/zéro (`test_absent_views_stay_none_rather_than_zero`) ; extraire un mapper aurait été du refactoring sans gain. |
| **3** — `TelegramProvider` | existait déjà, et en mieux : cache d'entités, mise à l'écart des canaux irrésolubles, import Telethon paresseux. Remplacé par **D1** (liste relue à chaque cycle) et **D2** (clé de santé). |
| **4** — câblage | existait déjà. `TELEGRAM_POLL_INTERVAL` abandonné (non demandé, non utilisé). |
| **5** — control-api | fait en **D3**, avec deux écarts assumés : plafond à **50** et non 25 (la graine compte 24 canaux, 25 ne laissait de place que pour un ajout), et `normalize_channel` vit dans `cmi_common.sources.runtime`, pas dans `collectors.py` — control-api n'a pas le droit d'importer un collecteur, et deux normalisations divergentes laisseraient l'opérateur saisir un handle que le provider n'interroge jamais. |
| **6** — terminal | inchangé, reste à faire |
| **7** — déploiement | largement apporté par le merge. `deploy.yml` n'est **pas** modifié pour les secrets (il n'en transporte aucun) mais il lance une **liste explicite de fichiers de test** en CI, ce que ce plan avait manqué. |

S'y ajoute **D2b**, absente du plan d'origine : une revue de qualité a trouvé que la clé de
santé annonçait `ok: true` indéfiniment dès qu'un cycle échouait après connexion — soit
précisément la panne qu'elle devait rendre visible — et que `set_runtime` détruisait le
signal « jamais configuré » en matérialisant la graine.

Les blocs de code ci-dessous restent utiles comme trace du raisonnement, mais **ne
correspondent plus au code en place**. Le code fait foi.

---

## Structure de fichiers

**Créés :**

| Fichier | Responsabilité |
|---|---|
| `services/collector-social/app/providers/telegram_map.py` | Mapping pur message → `RawItem`. Aucun import Telethon, aucune I/O. |
| `services/collector-social/app/providers/telegram.py` | Le provider : client Telethon, canaux, curseurs, erreurs, santé. |
| `tests/test_telegram_mapper.py` | Les règles de mapping, dont `engagement is None`. |
| `tests/test_telegram_provider.py` | L'orchestration de `fetch()`, avec un faux client. |

Le découpage mapper / client suit le précédent de `collector-binance-futures`
(`test_binance_futures_mapper.py` et `test_binance_futures_client.py` sont deux fichiers
distincts) : les règles de mapping sont la partie qui mérite d'être testée exhaustivement,
et elles n'ont besoin ni de session ni de réseau.

**Modifiés :**

| Fichier | Changement |
|---|---|
| `libs/cmi_common/cmi_common/sources/runtime.py` | `telegram_channels` + `"telegram"` dans `KNOWN_PLATFORMS`. |
| `services/collector-social/app/main.py` | Instanciation key-gated + `TELEGRAM_POLL_INTERVAL`. |
| `services/collector-social/pyproject.toml` | Dépendance `telethon`. |
| `services/control-api/app/routers/collectors.py` | `telegram_channels` dans `RuntimePatch`, normalisation, `source_status`. |
| `tests/control_api_helpers.py` | `collectors` dans `_ROUTERS`. |
| `tests/test_collector_runtime.py` | Tests de la liste de canaux. |
| `frontend/src/lib/api/endpoints.ts` | Types `telegram_channels` + `source_status`. |
| `frontend/src/components/settings/SourcesPanel.tsx` | Éditeur de canaux + pastille de santé. |
| `Makefile` | `telethon` dans la cible `install`. |
| `pyproject.toml` (racine) | `telethon.*` dans les overrides mypy (pas de stubs). |
| `docker-compose.vps.yml` | Les trois secrets Telegram. |

---

## Task 1: Liste de canaux dans le runtime partagé

**Files:**
- Modify: `libs/cmi_common/cmi_common/sources/runtime.py`
- Test: `tests/test_collector_runtime.py`

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_collector_runtime.py` :

```python
async def test_telegram_is_a_known_social_platform() -> None:
    """SourcesPanel itère sur known_platforms; sans cette entrée l'interrupteur
    n'apparaît jamais dans le terminal."""
    assert "telegram" in runtime.KNOWN_PLATFORMS["social"]


async def test_telegram_channels_default_to_the_seed() -> None:
    cache = _FakeCache()
    rt = await runtime.get_runtime(cache)
    assert rt["telegram_channels"] == list(runtime.TELEGRAM_SEED_CHANNELS)


async def test_an_explicitly_empty_channel_list_is_not_refilled_by_the_seed(
    monkeypatch,
) -> None:
    """La graine livrée est vide, donc tester contre elle ne prouverait rien :
    `[] or SEED` et le test `is None` se comportent identiquement quand SEED est
    vide. On pose une graine non vide pour que la distinction soit falsifiable."""
    monkeypatch.setattr(runtime, "TELEGRAM_SEED_CHANNELS", ["seeded"])
    cache = _FakeCache({"telegram_channels": []})
    rt = await runtime.get_runtime(cache)
    assert rt["telegram_channels"] == []


async def test_set_runtime_replaces_the_channel_list_wholesale() -> None:
    """Remplacement, pas merge : sans ça un canal supprimé depuis l'UI
    ressusciterait au patch suivant."""
    cache = _FakeCache({"telegram_channels": ["alpha", "beta"]})
    out = await runtime.set_runtime(cache, {"telegram_channels": ["gamma"]})
    assert out["telegram_channels"] == ["gamma"]


async def test_set_runtime_leaves_channels_alone_when_not_patched() -> None:
    cache = _FakeCache({"telegram_channels": ["alpha"]})
    out = await runtime.set_runtime(cache, {"platforms": {"reddit": False}})
    assert out["telegram_channels"] == ["alpha"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_collector_runtime.py -v`
Expected: FAIL — `AttributeError: module 'cmi_common.sources.runtime' has no attribute 'TELEGRAM_SEED_CHANNELS'`, et `assert 'telegram' in [...]` en échec.

- [ ] **Step 3: Implement in `runtime.py`**

Dans `libs/cmi_common/cmi_common/sources/runtime.py`, ajouter `"telegram"` à la liste `social` de `KNOWN_PLATFORMS` :

```python
KNOWN_PLATFORMS: dict[str, list[str]] = {
    "social": [
        "bluesky",
        "reddit",
        "mastodon",
        "fourchan",
        "neynar",
        "youtube",
        "lens",
        "telegram",
    ],
    "news": ["cryptocompare", "gdelt", "newsdata", "rss"],
}

#: Canaux Telegram livrés par défaut. Vide à dessein : aucune liste vérifiée n'a
#: été fournie, et des usernames inventés produiraient des canaux introuvables
#: signalés en erreur à chaque cycle. L'opérateur la peuple depuis le terminal.
TELEGRAM_SEED_CHANNELS: list[str] = []
```

Ajouter la clé au défaut :

```python
def default_runtime() -> dict[str, Any]:
    return {
        "social_enabled": True,
        "news_enabled": True,
        "platforms": {p: True for ps in KNOWN_PLATFORMS.values() for p in ps},
        "telegram_channels": list(TELEGRAM_SEED_CHANNELS),
    }
```

Dans `get_runtime`, juste avant le `return merged` :

```python
    channels = cfg.get("telegram_channels")
    # `[]` est un "aucun canal" délibéré, pas un "non renseigné". Un `or` ferait
    # revivre la graine que l'opérateur vient de vider.
    if channels is not None:
        merged["telegram_channels"] = [str(c) for c in channels]
    return merged
```

Dans `set_runtime`, juste avant le `await cache.set_json(...)` :

```python
    # Remplacement intégral, contrairement aux `platforms` qui se mergent.
    if patch.get("telegram_channels") is not None:
        cur["telegram_channels"] = [str(c) for c in patch["telegram_channels"]]
```

Exporter la constante depuis `libs/cmi_common/cmi_common/sources/__init__.py` : ajouter
`TELEGRAM_SEED_CHANNELS` à l'import depuis `.runtime` et à `__all__` (la liste `__all__`
est triée : le placer entre `"SymbolLexicon"` et `"aggregate_buckets"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_collector_runtime.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add libs/cmi_common/cmi_common/sources/runtime.py libs/cmi_common/cmi_common/sources/__init__.py tests/test_collector_runtime.py
git commit -m "feat(sources): liste de canaux Telegram dans collectors:runtime"
```

---

## Task 2: Mapping pur message → RawItem

**Files:**
- Create: `services/collector-social/app/providers/telegram_map.py`
- Test: `tests/test_telegram_mapper.py`

- [ ] **Step 1: Write the failing tests**

Créer `tests/test_telegram_mapper.py` :

```python
"""Règles de mapping Telegram -> RawItem. Pures : ni session, ni réseau."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from service_modules import load_service_module

tmap = load_service_module("collector-social", "providers.telegram_map")


def _msg(**kw) -> SimpleNamespace:
    base = dict(
        id=7,
        message="$BTC listing soon",
        date=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        views=None,
        forwards=None,
        reactions=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _reactions(*counts: int) -> SimpleNamespace:
    return SimpleNamespace(results=[SimpleNamespace(count=c) for c in counts])


def test_engagement_is_none_when_telegram_reports_nothing() -> None:
    """LE test central. Un 0.0 confiant atterrirait dans
    content_sentiment_agg.engagement_sum et tirerait le signal social vers le bas
    comme s'il avait été mesuré. Les groupes non-broadcast n'ont aucun compteur."""
    item = tmap.to_raw_item(_msg(), channel_id=1234, username="cryptonews")
    assert item.engagement is None


def test_engagement_sums_views_forwards_and_reactions() -> None:
    item = tmap.to_raw_item(
        _msg(views=100, forwards=10, reactions=_reactions(3, 4)),
        channel_id=1234,
        username="cryptonews",
    )
    assert item.engagement == 117.0


def test_a_single_available_counter_is_enough() -> None:
    item = tmap.to_raw_item(_msg(views=42), channel_id=1234, username="cryptonews")
    assert item.engagement == 42.0


def test_a_present_but_empty_reaction_block_is_a_measured_zero() -> None:
    """Bloc absent = non mesuré (None). Bloc présent et vide = mesuré à zéro."""
    item = tmap.to_raw_item(
        _msg(reactions=_reactions()), channel_id=1234, username="cryptonews"
    )
    assert item.engagement == 0.0


def test_external_id_distinguishes_the_same_message_id_in_two_channels() -> None:
    a = tmap.to_raw_item(_msg(id=7), channel_id=1, username="alpha")
    b = tmap.to_raw_item(_msg(id=7), channel_id=2, username="beta")
    assert a.external_id != b.external_id


def test_external_id_uses_the_numeric_id_not_the_username() -> None:
    """Un canal renommé ré-entrerait sinon tout son historique en nouvelles lignes."""
    before = tmap.to_raw_item(_msg(), channel_id=1234, username="oldname")
    after = tmap.to_raw_item(_msg(), channel_id=1234, username="newname")
    assert before.external_id == after.external_id


def test_url_and_author_fall_back_when_the_channel_has_no_public_username() -> None:
    item = tmap.to_raw_item(_msg(), channel_id=1234, username=None)
    assert item.url is None
    assert item.author == "1234"


def test_url_points_at_the_message_when_the_channel_is_public() -> None:
    item = tmap.to_raw_item(_msg(id=7), channel_id=1234, username="cryptonews")
    assert item.url == "https://t.me/cryptonews/7"


def test_symbols_are_left_to_the_normalizer() -> None:
    """Un second résolveur divergerait de ContentNormalizer."""
    item = tmap.to_raw_item(_msg(), channel_id=1, username="alpha")
    assert item.symbols == []


def test_an_empty_message_still_produces_an_item() -> None:
    """Le rejet appartient à ContentNormalizer (empty_text), à un seul endroit."""
    item = tmap.to_raw_item(_msg(message=""), channel_id=1, username="alpha")
    assert item.text == ""
    assert item.source == "telegram"
    assert item.kind == "social"


def test_lang_is_none_because_mtproto_does_not_provide_it() -> None:
    item = tmap.to_raw_item(_msg(), channel_id=1, username="alpha")
    assert item.lang is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_mapper.py -v`
Expected: FAIL à la collecte — `FileNotFoundError` sur `providers/telegram_map.py`.

- [ ] **Step 3: Write the implementation**

Créer `services/collector-social/app/providers/telegram_map.py` :

```python
"""Mapping pur d'un message Telegram vers ``RawItem``. Ni Telethon, ni I/O.

Séparé de ``telegram.py`` pour la même raison que ``collector-binance-futures``
sépare son mapper de son client : les règles de mapping sont la partie qui mérite
d'être testée exhaustivement, et elles n'ont besoin ni de session ni de réseau.
"""

from __future__ import annotations

from typing import Any

from cmi_common.sources import RawItem


def to_raw_item(message: Any, *, channel_id: int, username: str | None) -> RawItem:
    """Un message de canal -> une ligne ``raw_content``.

    ``message`` est typé ``Any`` à dessein : la fonction ne lit que des attributs
    (``id``, ``message``, ``date``, ``views``, ``forwards``, ``reactions``), ce qui
    la garde testable sans instancier quoi que ce soit de Telethon.
    """
    return RawItem(
        source="telegram",
        kind="social",
        # L'identifiant numérique du canal, pas son username : un canal renommé
        # ré-entrerait sinon tout son historique en nouvelles lignes, la
        # contrainte UNIQUE(source, external_id) ne voyant que du neuf.
        external_id=f"{channel_id}:{message.id}",
        text=message.message or "",
        # L'unité pertinente est le canal, pas l'auteur du post : un canal
        # broadcast n'expose pas d'auteur par message.
        author=username or str(channel_id),
        url=f"https://t.me/{username}/{message.id}" if username else None,
        published_at=message.date,
        engagement=engagement(message),
        # symbols laissé vide : LexiconNormalizer est le résolveur unique.
    )


def engagement(message: Any) -> float | None:
    """Vues + transferts + réactions, ou ``None`` si Telegram n'en rapporte aucun.

    Jamais 0.0 pour une donnée absente. Les groupes non-broadcast n'ont aucun
    compteur de vues ; un zéro confiant atterrirait dans
    ``content_sentiment_agg.engagement_sum`` et tirerait le signal social vers le
    bas comme s'il avait été mesuré.
    """
    parts: list[float] = []
    for attr in ("views", "forwards"):
        value = getattr(message, attr, None)
        if value is not None:
            parts.append(float(value))
    reactions = reaction_count(message)
    if reactions is not None:
        parts.append(float(reactions))
    return sum(parts) if parts else None


def reaction_count(message: Any) -> int | None:
    """Total des réactions, ou ``None`` si le message ne porte aucun bloc.

    Un bloc présent avec une liste vide est un zéro *mesuré* et reste 0 — c'est
    la même distinction absent/vide que partout ailleurs dans ce pipeline.
    """
    reactions = getattr(message, "reactions", None)
    if reactions is None:
        return None
    results = getattr(reactions, "results", None)
    if results is None:
        return None
    return sum(int(getattr(r, "count", 0) or 0) for r in results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_mapper.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add services/collector-social/app/providers/telegram_map.py tests/test_telegram_mapper.py
git commit -m "feat(collector-social): mapping pur message Telegram -> RawItem"
```

---

## Task 3: TelegramProvider — canaux, curseurs, erreurs, santé

**Files:**
- Create: `services/collector-social/app/providers/telegram.py`
- Test: `tests/test_telegram_provider.py`

- [ ] **Step 1: Write the failing tests**

Créer `tests/test_telegram_provider.py` :

```python
"""TelegramProvider: orchestration de fetch(), avec un faux client MTProto.

Aucune session réelle n'est ouverte : le client est injecté via `client_factory`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from service_modules import load_service_module
from telethon.errors import AuthKeyUnregisteredError, ChannelPrivateError, FloodWaitError

from cmi_common.sources import RateLimitedError

tg = load_service_module("collector-social", "providers.telegram")


class _FakeCache:
    def __init__(self, channels: list[str] | None = None) -> None:
        self._store: dict = {}
        if channels is not None:
            self._store["collectors:runtime"] = {"telegram_channels": channels}

    async def get_json(self, key):
        return self._store.get(key)

    async def set_json(self, key, value, ttl_seconds=60):
        self._store[key] = value


def _msg(message_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        message="$BTC listing soon",
        date=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        views=None,
        forwards=None,
        reactions=None,
    )


class _FakeClient:
    """Rejoue des messages par canal; `errors` fait échouer un canal nommé."""

    def __init__(self, messages: dict[str, list], errors: dict | None = None) -> None:
        self._messages = messages
        self._errors = errors or {}
        self.calls: list[tuple[str, int]] = []
        self.disconnected = False

    async def get_entity(self, name):
        if name in self._errors:
            raise self._errors[name]
        return SimpleNamespace(id=abs(hash(name)) % 10_000, username=name)

    async def get_messages(self, entity, limit=None, min_id=0):
        self.calls.append((entity.username, min_id))
        return [m for m in self._messages.get(entity.username, []) if m.id > min_id]

    async def disconnect(self):
        self.disconnected = True


def _provider(cache, client) -> object:
    return tg.TelegramProvider(
        api_id=1,
        api_hash="hash",
        session="session-string",
        cache=cache,
        client_factory=lambda: client,
    )


async def test_no_configured_channel_yields_nothing() -> None:
    """La graine livrée est vide : le provider doit tourner à vide sans erreur."""
    cache = _FakeCache(channels=[])
    provider = _provider(cache, _FakeClient({}))
    assert await provider.fetch() == []


async def test_messages_become_raw_items() -> None:
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient({"cryptonews": [_msg(1), _msg(2)]})
    provider = _provider(cache, client)

    items = await provider.fetch()

    assert len(items) == 2
    assert {i.source for i in items} == {"telegram"}
    assert all(i.kind == "social" for i in items)


async def test_flood_wait_becomes_rate_limited_carrying_its_own_delay() -> None:
    """AdaptivePollLoop met la source en pause exactement ce temps-là."""
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient(
        {}, errors={"cryptonews": FloodWaitError(request=None, capture=42)}
    )
    provider = _provider(cache, client)

    with pytest.raises(RateLimitedError) as exc:
        await provider.fetch()
    assert exc.value.retry_after == 42.0


async def test_an_unreadable_channel_does_not_cancel_the_others() -> None:
    """Un @nom mal saisi ne doit pas blanchir les vingt-quatre autres canaux."""
    cache = _FakeCache(channels=["broken", "cryptonews"])
    client = _FakeClient(
        {"cryptonews": [_msg(1)]},
        errors={"broken": ChannelPrivateError(request=None)},
    )
    provider = _provider(cache, client)

    items = await provider.fetch()

    assert len(items) == 1
    status = await cache.get_json(tg.STATUS_KEY)
    assert status["ok"] is True
    assert "broken" in status["channels"]


async def test_the_cursor_advances_and_is_reused_next_cycle() -> None:
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient({"cryptonews": [_msg(1), _msg(5), _msg(3)]})
    provider = _provider(cache, client)

    await provider.fetch()
    assert await cache.get_json(tg.CURSOR_KEY.format(channel="cryptonews")) == 5

    await provider.fetch()
    assert client.calls == [("cryptonews", 0), ("cryptonews", 5)]


async def test_an_empty_cycle_leaves_the_cursor_where_it_was() -> None:
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient({"cryptonews": []})
    provider = _provider(cache, client)

    await provider.fetch()

    assert await cache.get_json(tg.CURSOR_KEY.format(channel="cryptonews")) is None


async def test_a_revoked_session_is_recorded_in_the_health_key() -> None:
    """Sans ça, AdaptivePollLoop avalerait l'erreur et rejouerait un warning
    toutes les deux minutes pendant des semaines sans que rien ne le dise."""
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient(
        {}, errors={"cryptonews": AuthKeyUnregisteredError(request=None)}
    )
    provider = _provider(cache, client)

    with pytest.raises(AuthKeyUnregisteredError):
        await provider.fetch()

    status = await cache.get_json(tg.STATUS_KEY)
    assert status["ok"] is False
    assert "AuthKeyUnregisteredError" in status["reason"]


async def test_a_clean_cycle_marks_the_source_healthy() -> None:
    cache = _FakeCache(channels=["cryptonews"])
    provider = _provider(cache, _FakeClient({"cryptonews": [_msg(1)]}))

    await provider.fetch()

    status = await cache.get_json(tg.STATUS_KEY)
    assert status == {"ok": True, "reason": None, "channels": {}}


async def test_the_client_is_built_once_and_reused() -> None:
    """Rouvrir une session MTProto à chaque cycle est un login complet."""
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient({"cryptonews": []})
    built = []

    def factory():
        built.append(1)
        return client

    provider = tg.TelegramProvider(
        api_id=1,
        api_hash="hash",
        session="session-string",
        cache=cache,
        client_factory=factory,
    )
    await provider.fetch()
    await provider.fetch()

    assert len(built) == 1


async def test_close_disconnects_the_client() -> None:
    cache = _FakeCache(channels=["cryptonews"])
    client = _FakeClient({"cryptonews": []})
    provider = _provider(cache, client)

    await provider.fetch()
    await provider.close()

    assert client.disconnected is True


async def test_close_without_a_client_is_a_no_op() -> None:
    """close() est appelé au shutdown même si aucun fetch n'a eu lieu."""
    provider = _provider(_FakeCache(channels=[]), _FakeClient({}))
    await provider.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_provider.py -v`
Expected: FAIL à la collecte — `FileNotFoundError` sur `providers/telegram.py`.

- [ ] **Step 3: Write the implementation**

Créer `services/collector-social/app/providers/telegram.py` :

```python
"""TelegramProvider — lit N canaux MTProto et rend des ``RawItem``.

Un provider ordinaire au sens de ``cmi_common.sources.Provider`` : c'est
``AdaptivePollLoop`` qui le pilote, persiste sa sortie, honore les toggles
opérateur et applique le backoff. Il n'écrit rien lui-même dans la base.

Le client Telethon est construit paresseusement au premier ``fetch()``, sur le
modèle de ``BlueskyProvider._ensure_session()`` : rouvrir une session MTProto à
chaque cycle est un login complet, pas une requête.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UnauthorizedError
from telethon.sessions import StringSession

from cmi_common.cache import Cache
from cmi_common.sources import RateLimitedError, RawItem, get_runtime

from .telegram_map import to_raw_item

logger = logging.getLogger(__name__)

#: Santé de la source, lue par control-api et affichée dans le terminal.
STATUS_KEY = "collectors:status:telegram"
#: Dernier message vu par canal, pour ne pas relire ce qui est déjà en base.
CURSOR_KEY = "telegram:cursor:{channel}"


class TelegramProvider:
    name = "telegram"
    kind = "social"
    # Délibérément non contraignant : AdaptivePollLoop consomme un jeton par
    # *cycle*, pas par appel API, donc ce budget ne peut pas borner le nombre
    # réel d'appels (fonction du nombre de canaux). Le vrai limiteur est
    # FloodWaitError, et le vrai plafond est la limite de 25 canaux.
    rate_limit = (1000, 300)

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session: str,
        cache: Cache,
        limit: int = 50,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session = session
        self._cache = cache
        self._limit = limit
        self._client_factory = client_factory or self._default_factory
        self._client: Any | None = None

    def _default_factory(self) -> Any:
        return TelegramClient(
            StringSession(self._session), self._api_id, self._api_hash
        )

    async def _ensure_client(self) -> Any:
        if self._client is None:
            client = self._client_factory()
            connect = getattr(client, "connect", None)
            if connect is not None:
                await connect()
            self._client = client
        return self._client

    async def fetch(self) -> list[RawItem]:
        channels = await self._channels()
        if not channels:
            logger.info("telegram: aucun canal configuré; cycle vide")
            return []
        client = await self._ensure_client()
        items: list[RawItem] = []
        failures: dict[str, str] = {}
        for channel in channels:
            try:
                items.extend(await self._fetch_channel(client, channel))
            except FloodWaitError as exc:
                # Le loop met la source en pause exactement ce temps-là.
                raise RateLimitedError(float(exc.seconds)) from exc
            except UnauthorizedError as exc:
                # Panne permanente (session révoquée, compte désactivé) : le loop
                # la rejouerait toutes les 120s en silence. On la rend visible.
                reason = f"session invalide: {type(exc).__name__}"
                await self._write_status(ok=False, reason=reason, channels=failures)
                raise
            except Exception as exc:
                # Un @nom mal saisi ne doit pas blanchir les autres canaux.
                failures[channel] = type(exc).__name__
                logger.warning(
                    "telegram: canal %s illisible (%s)", channel, type(exc).__name__
                )
        await self._write_status(ok=True, reason=None, channels=failures)
        return items

    async def _fetch_channel(self, client: Any, channel: str) -> list[RawItem]:
        entity = await client.get_entity(channel)
        channel_id = int(entity.id)
        username = getattr(entity, "username", None)
        cursor = await self._cursor(channel)
        messages = await client.get_messages(entity, limit=self._limit, min_id=cursor)
        if messages:
            await self._cache.set_json(
                CURSOR_KEY.format(channel=channel),
                max(int(m.id) for m in messages),
                ttl_seconds=0,  # durable, comme collectors:runtime
            )
        return [
            to_raw_item(m, channel_id=channel_id, username=username) for m in messages
        ]

    async def _channels(self) -> list[str]:
        runtime = await get_runtime(self._cache)
        return list(runtime.get("telegram_channels") or [])

    async def _cursor(self, channel: str) -> int:
        # 0 = pas de borne basse. Perdre Redis ne coûte qu'un refetch borné par
        # `limit`; le vrai filet anti-doublon est UNIQUE(source, external_id).
        value = await self._cache.get_json(CURSOR_KEY.format(channel=channel))
        return int(value) if value is not None else 0

    async def _write_status(
        self, *, ok: bool, reason: str | None, channels: dict[str, str]
    ) -> None:
        await self._cache.set_json(
            STATUS_KEY,
            {"ok": ok, "reason": reason, "channels": channels},
            ttl_seconds=0,
        )

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.disconnect()
        self._client = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_provider.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Run the whole suite to check nothing regressed**

Run: `pytest -q`
Expected: PASS, aucun échec neuf.

- [ ] **Step 6: Commit**

```bash
git add services/collector-social/app/providers/telegram.py tests/test_telegram_provider.py
git commit -m "feat(collector-social): TelegramProvider avec curseurs, FloodWait et cle de sante"
```

---

## Task 4: Câblage dans collector-social

**Files:**
- Modify: `services/collector-social/app/main.py:30-63`
- Modify: `services/collector-social/pyproject.toml`
- Modify: `Makefile` (cible `install`)
- Modify: `pyproject.toml` (racine, overrides mypy)

- [ ] **Step 1: Add the dependency**

Dans `services/collector-social/pyproject.toml`, remplacer la ligne `dependencies` :

```toml
dependencies = [
    "cmi-common",
    "httpx>=0.27",
    "sqlalchemy>=2.0",
    "asyncpg>=0.29",
    "telethon>=1.36",
]
```

Dans `Makefile`, cible `install`, ajouter `telethon` à la ligne d'outillage :

```makefile
	pip install ruff black mypy pytest pytest-asyncio pytest-cov pre-commit telethon
```

Dans le `pyproject.toml` racine, ajouter `telethon.*` aux modules sans stubs :

```toml
[[tool.mypy.overrides]]
module = ["aiokafka.*", "transformers.*", "anthropic.*", "telethon.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Wire the provider into `main.py`**

Dans `services/collector-social/app/main.py`, ajouter l'import après celui de `RedditProvider` :

```python
from .providers.telegram import TelegramProvider
```

Ajouter la constante après `SUBREDDITS` :

```python
# Telegram a sa propre cadence : AdaptivePollLoop prend déjà `poll_interval` par
# instance, alors que ce service passait un unique intervalle à ses huit boucles.
TELEGRAM_POLL_INTERVAL = float(
    os.getenv("TELEGRAM_POLL_INTERVAL", str(POLL_INTERVAL))
)
```

`_build_providers` a besoin du cache pour construire le provider Telegram. Changer sa
signature et son appel :

```python
def _build_providers(cache: Cache) -> list[Provider]:
```

et, avant le `return providers` :

```python
    # Key-gated comme neynar/youtube : les trois secrets sont requis ensemble.
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session = os.getenv("TELEGRAM_SESSION")
    if api_id and api_hash and session:
        providers.append(
            TelegramProvider(
                api_id=int(api_id),
                api_hash=api_hash,
                session=session,
                cache=cache,
            )
        )
```

Dans `_startup`, remplacer `providers = _build_providers()` par
`providers = _build_providers(cache)`, et donner à chaque boucle son intervalle :

```python
    loops = [
        # _RepoFactory implements the only method the loop uses (insert_items).
        AdaptivePollLoop(
            p,
            repo,  # type: ignore[arg-type]
            cache,
            poll_interval=(
                TELEGRAM_POLL_INTERVAL if p.name == "telegram" else POLL_INTERVAL
            ),
            service="collector-social",
            normalizer=normalizer,
        )
        for p in providers
    ]
```

- [ ] **Step 3: Verify the service module still imports**

Run: `python -c "import sys; sys.path.insert(0, 'tests'); from service_modules import load_service_module; m = load_service_module('collector-social', 'main'); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Verify the provider is gated off without secrets**

Run:
```bash
python - <<'PY'
import sys, os
sys.path.insert(0, 'tests')
# Les autres providers key-gated aussi, pour que la sortie attendue soit stable.
for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
          "NEYNAR_API_KEY", "YOUTUBE_API_KEY"):
    os.environ.pop(k, None)
from service_modules import load_service_module
main = load_service_module("collector-social", "main")
# cache n'est lu que sur le chemin Telegram, qui est désactivé ici.
names = [p.name for p in main._build_providers(cache=None)]
assert "telegram" not in names, names
print("gated off:", names)
PY
```
Expected: `gated off: ['bluesky', 'reddit', 'mastodon', 'fourchan', 'lens']`

- [ ] **Step 5: Run lint and the suite**

Run: `ruff check libs services && pytest -q`
Expected: aucune erreur ruff, suite verte.

- [ ] **Step 6: Commit**

```bash
git add services/collector-social/app/main.py services/collector-social/pyproject.toml Makefile pyproject.toml
git commit -m "feat(collector-social): cable TelegramProvider, key-gated, cadence dediee"
```

---

## Task 5: control-api — édition et validation de la liste de canaux

**Files:**
- Modify: `services/control-api/app/routers/collectors.py:37-58`
- Modify: `tests/control_api_helpers.py:13`
- Test: `tests/test_control_api_collectors.py` (créer)

- [ ] **Step 1: Register the router in the test helper**

Dans `tests/control_api_helpers.py`, ligne 13, ajouter `"collectors"` :

```python
_ROUTERS = ["auth", "settings", "positions", "opportunities", "orders", "collectors"]
```

- [ ] **Step 2: Write the failing tests**

Créer `tests/test_control_api_collectors.py` :

```python
"""Normalisation et validation de la liste de canaux Telegram."""

from __future__ import annotations

import pytest
from tests.control_api_helpers import load_module


def _mod():
    return load_module("routers.collectors")


def test_a_bare_name_passes_through() -> None:
    assert _mod().normalize_channel("cryptonews") == "cryptonews"


def test_the_at_prefix_is_stripped() -> None:
    assert _mod().normalize_channel("@cryptonews") == "cryptonews"


def test_both_link_forms_reduce_to_the_username() -> None:
    assert _mod().normalize_channel("t.me/cryptonews") == "cryptonews"
    assert _mod().normalize_channel("https://t.me/cryptonews") == "cryptonews"


def test_a_trailing_slash_is_tolerated() -> None:
    assert _mod().normalize_channel("https://t.me/cryptonews/") == "cryptonews"


def test_invite_links_are_rejected() -> None:
    """Ils supposent un flux d'adhésion que ce provider ne fait pas; acceptés,
    ils produiraient un canal illisible signalé à chaque cycle."""
    for bad in ("t.me/+AbCdEf", "https://t.me/joinchat/AbCdEf", "@+AbCdEf"):
        with pytest.raises(ValueError):
            _mod().normalize_channel(bad)


def test_an_empty_entry_is_rejected() -> None:
    with pytest.raises(ValueError):
        _mod().normalize_channel("   ")


def test_the_patch_model_normalizes_every_entry() -> None:
    patch = _mod().RuntimePatch(
        telegram_channels=["@alpha", "https://t.me/beta", "gamma"]
    )
    assert patch.telegram_channels == ["alpha", "beta", "gamma"]


def test_the_patch_model_caps_the_list() -> None:
    """Le nombre d'appels API par cycle est fonction du nombre de canaux."""
    with pytest.raises(ValueError):
        _mod().RuntimePatch(telegram_channels=[f"c{i}" for i in range(26)])


def test_an_explicitly_empty_list_is_accepted() -> None:
    """Vider la liste depuis le terminal doit être possible."""
    assert _mod().RuntimePatch(telegram_channels=[]).telegram_channels == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_control_api_collectors.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'normalize_channel'`.

- [ ] **Step 4: Implement in `collectors.py`**

Dans `services/control-api/app/routers/collectors.py`, ajouter après la ligne
`from ..auth_dep import require_principal` :

```python
from pydantic import field_validator
```

(fusionner avec l'import `pydantic` existant : `from pydantic import BaseModel, field_validator`)

Ajouter après la constante `_PENDING_WHERE` :

```python
#: Le nombre d'appels MTProto par cycle est fonction du nombre de canaux.
MAX_TELEGRAM_CHANNELS = 25
_INVITE_MARKERS = ("+", "joinchat")


def normalize_channel(raw: str) -> str:
    """`@nom`, `t.me/nom`, `https://t.me/nom` -> `nom`.

    Les liens d'invitation sont rejetés : ils supposent un flux d'adhésion que le
    provider ne fait pas. Acceptés, ils produiraient un canal illisible signalé
    en erreur à chaque cycle plutôt qu'une erreur au moment de la saisie.
    """
    value = raw.strip()
    for prefix in ("https://", "http://"):
        value = value.removeprefix(prefix)
    value = value.removeprefix("t.me/").removeprefix("@").strip("/")
    if not value:
        raise ValueError("nom de canal vide")
    if any(marker in value for marker in _INVITE_MARKERS):
        raise ValueError(f"lien d'invitation non supporté: {raw}")
    return value
```

Remplacer `RuntimePatch` :

```python
class RuntimePatch(BaseModel):
    social_enabled: bool | None = None
    news_enabled: bool | None = None
    platforms: dict[str, bool] | None = None
    telegram_channels: list[str] | None = None

    @field_validator("telegram_channels")
    @classmethod
    def _normalize(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > MAX_TELEGRAM_CHANNELS:
            raise ValueError(f"au plus {MAX_TELEGRAM_CHANNELS} canaux")
        return [normalize_channel(v) for v in value]
```

Ajouter la lecture de la santé et l'exposer dans les deux routes :

```python
async def _source_status(cache) -> dict:
    """Santé par plateforme, écrite par les providers eux-mêmes.

    Dictionnaire indexé par nom de plateforme pour que d'autres providers
    puissent s'y ajouter sans changer le contrat.
    """
    telegram = await cache.get_json("collectors:status:telegram")
    return {"telegram": telegram} if telegram else {}


@router.get("/collectors/runtime")
async def get_collectors_runtime(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    cache = _cache(request)
    rt = await get_runtime(cache)
    return {
        **rt,
        "known_platforms": KNOWN_PLATFORMS,
        "source_status": await _source_status(cache),
    }


@router.post("/collectors/runtime")
async def set_collectors_runtime(
    body: RuntimePatch,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    cache = _cache(request)
    rt = await set_runtime(cache, body.model_dump(exclude_none=True))
    return {
        **rt,
        "known_platforms": KNOWN_PLATFORMS,
        "source_status": await _source_status(cache),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_control_api_collectors.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Run the suite**

Run: `pytest -q`
Expected: suite verte.

- [ ] **Step 7: Commit**

```bash
git add services/control-api/app/routers/collectors.py tests/control_api_helpers.py tests/test_control_api_collectors.py
git commit -m "feat(control-api): edition validee de la liste de canaux Telegram + source_status"
```

---

## Task 6: Terminal — éditeur de canaux et pastille de santé

**Files:**
- Modify: `frontend/src/lib/api/endpoints.ts:139-163`
- Modify: `frontend/src/components/settings/SourcesPanel.tsx`
- Test: `frontend/src/components/settings/__tests__/SourcesPanel.test.tsx` (créer)

- [ ] **Step 1: Extend the types**

Dans `frontend/src/lib/api/endpoints.ts`, remplacer `CollectorRuntime` :

```ts
export interface SourceStatus {
  ok: boolean;
  reason: string | null;
  channels: Record<string, string>;
}

export interface CollectorRuntime {
  social_enabled: boolean;
  news_enabled: boolean;
  platforms: Record<string, boolean>;
  known_platforms: { social: string[]; news: string[] };
  telegram_channels: string[];
  source_status: Record<string, SourceStatus>;
}
```

et la signature de `setRuntime` :

```ts
  setRuntime: (patch: {
    social_enabled?: boolean;
    news_enabled?: boolean;
    platforms?: Record<string, boolean>;
    telegram_channels?: string[];
  }) => control.post<CollectorRuntime>('/collectors/runtime', patch).then((r) => r.data),
```

- [ ] **Step 2: Write the failing tests**

Créer `frontend/src/components/settings/__tests__/SourcesPanel.test.tsx` :

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SourcesPanel } from '../SourcesPanel';
import type { CollectorRuntime } from '@/lib/api/endpoints';

const runtime = vi.fn();
const setRuntime = vi.fn();

vi.mock('@/lib/api/endpoints', () => ({
  collectorsApi: {
    runtime: () => runtime(),
    setRuntime: (patch: unknown) => setRuntime(patch),
    aiQuota: () => Promise.resolve({ paused: false, resume_at: null, workers: [] }),
  },
}));

const base: CollectorRuntime = {
  social_enabled: true,
  news_enabled: true,
  platforms: { telegram: true, bluesky: true },
  known_platforms: { social: ['bluesky', 'telegram'], news: [] },
  telegram_channels: ['cryptonews'],
  source_status: { telegram: { ok: true, reason: null, channels: {} } },
};

beforeEach(() => {
  runtime.mockReset().mockResolvedValue(base);
  setRuntime.mockReset().mockResolvedValue(base);
});

describe('SourcesPanel', () => {
  it('affiche les canaux Telegram configurés', async () => {
    render(<SourcesPanel />);
    expect(await screen.findByText('cryptonews')).toBeInTheDocument();
  });

  it('envoie la liste complète en ajoutant un canal', async () => {
    render(<SourcesPanel />);
    const input = await screen.findByLabelText('Ajouter un canal');
    await userEvent.type(input, 'alphagroup{enter}');
    await waitFor(() =>
      expect(setRuntime).toHaveBeenCalledWith({
        telegram_channels: ['cryptonews', 'alphagroup'],
      }),
    );
  });

  it('envoie une liste vide en supprimant le dernier canal', async () => {
    render(<SourcesPanel />);
    await userEvent.click(await screen.findByLabelText('Retirer cryptonews'));
    await waitFor(() =>
      expect(setRuntime).toHaveBeenCalledWith({ telegram_channels: [] }),
    );
  });

  it('signale une session Telegram invalide', async () => {
    runtime.mockResolvedValue({
      ...base,
      source_status: {
        telegram: { ok: false, reason: 'session invalide: AuthKeyUnregisteredError', channels: {} },
      },
    });
    render(<SourcesPanel />);
    expect(await screen.findByText(/session invalide/)).toBeInTheDocument();
  });

  it('ne signale rien quand la source est saine', async () => {
    render(<SourcesPanel />);
    await screen.findByText('cryptonews');
    expect(screen.queryByText(/session invalide/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/settings/__tests__/SourcesPanel.test.tsx`
Expected: FAIL — `Unable to find a label with the text of: Ajouter un canal`.

- [ ] **Step 4: Implement the editor**

Dans `frontend/src/components/settings/SourcesPanel.tsx` :

Ajouter `telegram: 'Telegram'` à `LABELS` (après `lens`), et ajouter aux imports MUI
`Button` et `TextField`, plus l'import de type `SourceStatus` :

```tsx
import { collectorsApi, type AiQuotaStatus, type CollectorRuntime, type SourceStatus } from '@/lib/api/endpoints';
```

Ajouter ce composant avant `SourcesPanel` :

```tsx
function TelegramChannels({
  channels,
  status,
  disabled,
  onChange,
}: {
  channels: string[];
  status: SourceStatus | undefined;
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState('');

  const add = () => {
    const value = draft.trim();
    if (!value) return;
    setDraft('');
    // La liste part entière : le backend la remplace, il ne la merge pas.
    if (!channels.includes(value)) onChange([...channels, value]);
  };

  return (
    <Box sx={{ pl: 4, pt: 1 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary">
          Canaux Telegram écoutés
        </Typography>
        {status && !status.ok && (
          <Chip size="small" color="error" label={status.reason ?? 'source en erreur'} />
        )}
      </Stack>
      <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} sx={{ mb: 1 }}>
        {channels.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            Aucun canal configuré — la collecte Telegram tourne à vide.
          </Typography>
        )}
        {channels.map((c) => (
          <Chip
            key={c}
            label={c}
            size="small"
            disabled={disabled}
            color={status?.channels?.[c] ? 'warning' : 'default'}
            onDelete={() => onChange(channels.filter((x) => x !== c))}
            deleteIcon={<span aria-label={`Retirer ${c}`}>✕</span>}
          />
        ))}
      </Stack>
      <Stack direction="row" spacing={1}>
        <TextField
          size="small"
          label="Ajouter un canal"
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button size="small" onClick={add} disabled={disabled || !draft.trim()}>
          Ajouter
        </Button>
      </Stack>
    </Box>
  );
}
```

Dans `SourcesPanel`, à l'intérieur de `category`, juste après la `<Stack>` des
interrupteurs de plateformes et avant la fermeture de `</Box>` :

```tsx
        {kind === 'social' && (rt.platforms.telegram ?? true) && enabled && (
          <TelegramChannels
            channels={rt.telegram_channels ?? []}
            status={rt.source_status?.telegram}
            disabled={busy}
            onChange={(next) => patch({ telegram_channels: next })}
          />
        )}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/settings/__tests__/SourcesPanel.test.tsx`
Expected: PASS, 5 tests.

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: aucune erreur.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api/endpoints.ts frontend/src/components/settings/SourcesPanel.tsx frontend/src/components/settings/__tests__/SourcesPanel.test.tsx
git commit -m "feat(frontend): editeur de canaux Telegram et pastille de sante des sources"
```

---

## Task 7: Déploiement

**Files:**
- Modify: `docker-compose.vps.yml` (service `collector-social`)
- Modify: `CLAUDE.md`
- Hors dépôt : `/opt/bottrading/.env` sur le VPS

- [ ] **Step 1: Pass the secrets to the container**

Dans `docker-compose.vps.yml`, dans le bloc `environment` du service `collector-social`,
ajouter (en suivant la syntaxe déjà utilisée par les autres clés du fichier, par exemple
`NEYNAR_API_KEY`) :

```yaml
      TELEGRAM_API_ID: ${TELEGRAM_API_ID:-}
      TELEGRAM_API_HASH: ${TELEGRAM_API_HASH:-}
      TELEGRAM_SESSION: ${TELEGRAM_SESSION:-}
      TELEGRAM_POLL_INTERVAL: ${TELEGRAM_POLL_INTERVAL:-}
```

- [ ] **Step 2: Add the secrets to the VPS env file**

`deploy.yml` n'écrit **aucun** secret applicatif : il fait un `rsync` de
`docker-compose.vps.yml` puis lance `scripts/deploy-vps.sh` par ssh. Les variables vivent
dans `/opt/bottrading/.env` sur le VPS, maintenu à la main — c'est pourquoi
`NEYNAR_API_KEY` n'apparaît nulle part dans le workflow. **Le workflow n'est donc pas
modifié.**

Sur le VPS, ajouter à `/opt/bottrading/.env` :

```
TELEGRAM_API_ID=<api_id>
TELEGRAM_API_HASH=<api_hash>
TELEGRAM_SESSION=<string_session>
```

`TELEGRAM_SESSION` est un identifiant de compte complet, équivalent à un mot de passe : il
ne doit jamais être committé ni journalisé. Ce fichier est hors dépôt, ce qui est exactement
ce qu'on veut ici.

- [ ] **Step 3: Document the source in CLAUDE.md**

Dans `CLAUDE.md`, section « Pipeline (data → decision) », modifier la phrase décrivant
`collector-social` pour inclure Telegram :

```
as two fan-out services — `collector-social` (Bluesky, Reddit, Mastodon, 4chan, Farcaster,
YouTube, Lens, Telegram) and `collector-news` (CryptoCompare, RSS, GDELT, NewsData) —
```

et remplacer la phrase « Telegram/StockTwits/Messari/CoinGecko-news deferred (paid or
session-based) » par :

```
Key-gated sources (Farcaster, YouTube, NewsData) activate when their env key is set;
Telegram activates when its three MTProto secrets are set together (`TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `TELEGRAM_SESSION` — a StringSession generated offline). Its channel
list lives in `collectors:runtime` and is editable from the terminal.
StockTwits/Messari/CoinGecko-news deferred (paid or session-based).
```

- [ ] **Step 4: Verify the compose file parses**

Run: `docker compose -f docker-compose.vps.yml config --quiet`
Expected: aucune sortie (le fichier est valide).

- [ ] **Step 5: Full check**

Run: `make lint && pytest -q && cd frontend && npm run typecheck && npx vitest run`
Expected: tout vert.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.vps.yml CLAUDE.md
git commit -m "chore(deploy): secrets MTProto Telegram + documentation de la source"
```

---

## Vérification live (hors CI)

Une fois les secrets posés et le service déployé, avec au moins un canal configuré depuis
le terminal :

```bash
docker compose -f docker-compose.vps.yml logs --tail=50 collector-social | grep telegram
```
Attendu : `telegram ingested N new items`.

```sql
SELECT source, count(*), count(*) FILTER (WHERE engagement IS NULL) AS sans_engagement
FROM raw_content WHERE source = 'telegram' GROUP BY source;
```
Attendu : des lignes présentes, et `sans_engagement` non nul si des canaux de type groupe
sont écoutés — c'est la preuve que l'absence de compteur n'a pas été convertie en 0.

```sql
SELECT symbols, count(*) FROM raw_content
WHERE source = 'telegram' GROUP BY symbols ORDER BY 2 DESC LIMIT 10;
```
Attendu : des symboles résolus par le normalizer commun, `["MARKET"]` inclus.

---

## Critères de succès

1. Des lignes `source = 'telegram'` apparaissent dans `raw_content`, avec des `symbols`
   résolus par `ContentNormalizer`.
2. `sentiment-service` les score et publie les `SentimentEvent` correspondants, sans
   qu'une seule ligne de ce service ait changé.
3. Couper Telegram depuis `SourcesPanel` arrête la collecte au cycle suivant ; éditer la
   liste de canaux prend effet au cycle suivant, sans redéploiement.
4. Une session invalide s'affiche dans le terminal et n'empêche aucun autre collecteur de
   tourner.
5. Un message de groupe sans compteur de vues produit `engagement IS NULL` en base,
   jamais `0.0`.
