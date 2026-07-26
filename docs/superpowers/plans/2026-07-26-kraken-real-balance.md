# Vrai solde Kraken — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kraken_balance_usd` cesse d'être une fiction (`cash × 0,8`) et devient le solde réel du compte Kraken spot, ou `null` déclaré comme tel — jamais un nombre inventé.

**Architecture:** Le trading-engine, seul détenteur des secrets d'exchange, interroge Kraken en lecture seule dans une boucle périodique et publie un `AccountSnapshotEvent`. L'api-gateway le persiste comme n'importe quel autre événement et le plan de lecture le sert. Aucune clé ne descend dans le service exposé publiquement.

**Tech Stack:** Python 3.12, httpx, aiokafka, SQLAlchemy 2.0 async, Alembic, TimescaleDB, FastAPI, Next.js 14.

**Spec:** `docs/superpowers/specs/2026-07-25-command-center-pipeline-kraken-events-design.md` — section « Phase 2 »

---

## Pourquoi ce chantier

Question de l'opérateur, au début du chantier :

> *« Mon argent sur la crypto devrait faire référence à Kraken qui a mon portefeuille, pourquoi je vois null par rapport à ce que j'ai en live ? »*

Diagnostic : `services/api-gateway/app/read_api.py:594` renvoie
`"kraken_balance_usd": round(cash * 0.8, 2)`. Ce n'est pas un solde, c'est une
constante multipliée par un capital de référence lui aussi imaginaire
(`CMI_BASE_CAPITAL_USD`, 100 000 $ par défaut). Rien dans le système n'a jamais
appelé Kraken pour lire un solde.

Deuxième diagnostic, plus grave : le seul client Kraken du dépôt
(`services/trading-engine/app/kraken.py`) parle à **futures.kraken.com**, avec le
schéma de signature Futures. Le compte de l'opérateur est un compte **spot**
(déterminé sans ambiguïté par le format des clés transmises : clé publique de 56
caractères, secret base64 de 88 caractères terminé par `==`). Ce client n'aurait
donc jamais pu afficher ce solde, quelle que soit la configuration.

---

## Contexte indispensable

### État vérifié (2026-07-26)

- `.env` de production : **aucune variable `KRAKEN_*`**. Le chemin nominal
  aujourd'hui est donc le chemin dégradé — il doit être correct, pas seulement
  toléré.
- `alembic_version` sera à **0011** après la phase 3. Ce plan pose **0012**.
- `EventType` (`libs/cmi_common/cmi_common/events/base.py:26`) n'a pas d'entrée
  pour un snapshot de compte ; `Topic` non plus.

### ⚠️ Clés compromises

La paire transmise dans la conversation est passée **en clair dans un transcript**
et doit être considérée comme compromise. Elle n'a pas été déployée. Avant toute
mise en service : en régénérer une neuve côté Kraken avec la **seule** permission
« Query Funds », et la poser en `KRAKEN_READ_API_KEY` / `KRAKEN_READ_API_SECRET`.
Ce plan s'implémente et se teste **sans** clé ; seule la recette finale en a besoin.

### Conventions du projet — les pièges déjà payés

- **Un événement absent de `AnyEvent` se publie très bien et casse à la
  consommation.** `parse_event` lève une `ValidationError` sur le discriminant.
  C'est exactement ce qui est arrivé à `JournalEntryEvent`. Tout nouvel événement
  entre dans l'union **et** obtient un test d'aller-retour.
- **Invariant à trois tables** : `Topic` + `TOPIC_EVENT` + `TOPIC_PARTITIONS`,
  gardé par `tests/test_journal_topic.py::test_every_topic_appears_in_both_tables`.
- `BaseEvent` : `extra="forbid"`, `frozen=True`, `use_enum_values=True`. Ce
  dernier ne s'applique **pas** aux valeurs par défaut non validées — `event_type`
  reste un membre d'enum en mémoire. Utiliser `archiver.event_type_of` ou
  `.value` avant tout formatage (`str()` rendrait `"EventType.X"`).
- Tests à plat dans `tests/`. **Jamais** de module de service importé sous un nom
  commençant par `app.` — `tests/conftest.py` fait échouer la collecte. Utiliser
  `from service_modules import load_service_module` (deux arguments).
- Tout ajout au plan de lecture entre dans `read_contract.py` **et** reçoit une
  assertion dans `tests/test_read_contract.py`.
- `asyncio_mode = "auto"`. Ruff 0.15 `target-version = "py312"` ; `black` n'est
  pas installé sur cette machine — formater à la main sur 88 colonnes. Préférer
  `from datetime import UTC` à `timezone.utc` (`UP017`).

### Échecs pré-existants — ne pas corriger, ne pas signaler comme nouveaux

`tests/test_bluesky_provider.py` (×2), `tests/test_raw_content_model.py` (×1).
Référence de départ : **3 failed, 386 passed, 2 skipped**.

---

## Structure des fichiers

**Créés**

| Fichier | Responsabilité |
|---|---|
| `libs/cmi_common/cmi_common/events/account.py` | `AccountSnapshotEvent` |
| `services/trading-engine/app/kraken_spot.py` | client spot lecture seule — signature + parsing |
| `services/trading-engine/app/account.py` | boucle de sondage → Kafka + Redis |
| `migrations/alembic/versions/0012_account_snapshots.py` | table `account_snapshots` |
| `tests/test_kraken_spot.py`, `tests/test_account_poller.py`, `tests/test_account_snapshot_topic.py` | |

**Modifiés**

| Fichier | Changement |
|---|---|
| `libs/cmi_common/cmi_common/events/base.py` | `EventType.ACCOUNT_SNAPSHOT` |
| `libs/cmi_common/cmi_common/events/__init__.py` | union `AnyEvent` + exports |
| `libs/cmi_common/cmi_common/kafka/topics.py` | les **trois** tables |
| `libs/cmi_common/cmi_common/db/models.py` | `AccountSnapshot` |
| `scripts/create-topics.sh` | nouveau topic |
| `services/websocket-gateway/app/consumer.py` | diffusion du snapshot |
| `services/trading-engine/app/config.py` | clés lecture seule + intervalle |
| `services/trading-engine/app/main.py` | démarrage de la boucle |
| `services/api-gateway/app/persister.py` | branche snapshot |
| `services/api-gateway/app/read_api.py` | fin de la fiction + capital de référence |
| `services/api-gateway/app/read_contract.py` | trois nouvelles clés |
| `frontend/src/lib/types/domain.ts` | `Portfolio` |
| `frontend/src/components/**` | affichage « — · non connecté » / périmé grisé |

---

## Task 1 : l'événement et son topic

**Files:**
- Create: `libs/cmi_common/cmi_common/events/account.py`
- Modify: `libs/cmi_common/cmi_common/events/base.py`, `.../events/__init__.py`,
  `.../kafka/topics.py`, `scripts/create-topics.sh`,
  `services/websocket-gateway/app/consumer.py`
- Test: `tests/test_account_snapshot_topic.py`

- [ ] **Step 1: Write the failing test**

```python
"""Le snapshot de compte est un citoyen de première classe du bus.

Un événement absent de l'union `AnyEvent` se publie parfaitement et échoue à la
*consommation* — c'est exactement ce qui est arrivé à JournalEntryEvent. Le
round-trip est donc le test qui compte, pas la simple construction.
"""

from __future__ import annotations

from cmi_common.events import AccountSnapshotEvent, parse_event
from cmi_common.events.base import EventType
from cmi_common.kafka import TOPIC_EVENT, TOPIC_PARTITIONS, Topic


def _snapshot() -> AccountSnapshotEvent:
    return AccountSnapshotEvent(
        venue="kraken_spot",
        equity_usd=1234.56,
        cash_usd=1000.0,
        balances={"ZUSD": 1000.0, "XXBT": 0.01},
    )


def test_round_trips_through_the_discriminated_union() -> None:
    decoded = parse_event(_snapshot().as_kafka_value())
    assert isinstance(decoded, AccountSnapshotEvent)
    assert decoded.venue == "kraken_spot"
    assert decoded.equity_usd == 1234.56


def test_event_type_is_the_plain_class_name() -> None:
    """`use_enum_values=True` ne s'applique pas aux valeurs par défaut, donc
    l'attribut reste un membre d'enum ; c'est sa *valeur* qui doit être le nom
    de classe, sans quoi tout formatage rendrait « EventType.ACCOUNT_SNAPSHOT »."""
    assert EventType.ACCOUNT_SNAPSHOT.value == "AccountSnapshotEvent"


def test_the_topic_appears_in_all_three_tables() -> None:
    assert Topic.ACCOUNT_SNAPSHOT in TOPIC_EVENT
    assert Topic.ACCOUNT_SNAPSHOT in TOPIC_PARTITIONS
    assert TOPIC_EVENT[Topic.ACCOUNT_SNAPSHOT] is AccountSnapshotEvent


def test_partition_key_is_the_venue() -> None:
    """Les snapshots d'un même venue doivent rester ordonnés entre eux : un
    snapshot périmé livré après un frais afficherait un solde qui recule."""
    assert _snapshot().partition_key() == "kraken_spot"
```

- [ ] **Step 2: Run it, confirm the failure**

`python -m pytest tests/test_account_snapshot_topic.py -v` → `ImportError` sur
`AccountSnapshotEvent`.

- [ ] **Step 3: Implement the event**

`libs/cmi_common/cmi_common/events/account.py` :

```python
"""Periodic read-only snapshot of an exchange account.

Published by trading-engine, which is the only service holding exchange
credentials. The api-gateway persists it and the read plane serves it, so the
publicly exposed service never needs a key of its own.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseEvent, EventType, Source


class AccountSnapshotEvent(BaseEvent):
    """One venue's balance at one instant.

    `equity_usd` is the whole account valued in USD; `cash_usd` is the quote
    currency alone. They differ as soon as anything is held in coin, and the
    portfolio page needs both.
    """

    event_type: Literal[EventType.ACCOUNT_SNAPSHOT] = EventType.ACCOUNT_SNAPSHOT
    source: Source = Source.TRADING_ENGINE

    venue: str
    equity_usd: float
    cash_usd: float
    balances: dict[str, float] = Field(default_factory=dict)

    def partition_key(self) -> str:
        # Keyed by venue so a venue's snapshots stay ordered against each other:
        # a stale snapshot delivered after a fresh one would show the balance
        # going backwards.
        return self.venue
```

`base.py`, dans `EventType`, après `CONTROL_COMMAND` :

```python
    ACCOUNT_SNAPSHOT = "AccountSnapshotEvent"
```

- [ ] **Step 4: Register it everywhere it must be registered**

`events/__init__.py` : importer `AccountSnapshotEvent`, l'ajouter à l'union
`AnyEvent` **et** à `__all__` (l'union est la partie qui casse en silence).

`kafka/topics.py` : les trois tables.

```python
    ACCOUNT_SNAPSHOT = "account.snapshot.events"
```
```python
    Topic.ACCOUNT_SNAPSHOT: AccountSnapshotEvent,
```
```python
    # One venue, one snapshot a minute: the lowest-volume topic on the bus.
    Topic.ACCOUNT_SNAPSHOT: 1,
```

`scripts/create-topics.sh` : ajouter le topic. **Lire le fichier d'abord** — il
manque déjà `execution.events` et `control.commands` ; les ajouter aussi, c'est
le même défaut et la même ligne à écrire.

`services/websocket-gateway/app/consumer.py` : ajouter `Topic.ACCOUNT_SNAPSHOT` à
la liste diffusée, pour que le flux temps réel gagne un événement « portefeuille »
qui, lui, existe vraiment.

- [ ] **Step 5: Verify**

`python -m pytest tests/test_account_snapshot_topic.py tests/test_journal_topic.py -v`
→ tout passe, y compris le garde de l'invariant à trois tables.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(events): AccountSnapshotEvent on its own topic

Registered in AnyEvent, not only in the topic tables: an event missing from the
discriminated union publishes fine and fails on consumption, which is how
JournalEntryEvent shipped broken.

Keyed by venue so a venue's snapshots stay ordered -- a stale snapshot delivered
after a fresh one would show the balance going backwards.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- libs/ scripts/create-topics.sh services/websocket-gateway/app/consumer.py tests/test_account_snapshot_topic.py
```

---

## Task 2 : client Kraken spot, lecture seule

**Files:**
- Create: `services/trading-engine/app/kraken_spot.py`
- Modify: `services/trading-engine/app/config.py`
- Test: `tests/test_kraken_spot.py`

Le schéma de signature spot **diffère** de celui des Futures déjà implémenté
(`kraken.py:53-59`) :

```
API-Sign = base64( HMAC-SHA512( base64decode(secret),
                                path.encode() + SHA256(nonce + postdata) ) )
```

Contre Futures : `base64(HMAC-SHA512(secret, SHA256(postdata + nonce + path)))`.
L'ordre de concaténation et la position du hash diffèrent ; recopier l'un pour
l'autre produit une signature invalide.

- [ ] **Step 1: Write the failing test**

```python
"""Client Kraken spot : signature, parsing, et le mode d'échec qui compte."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from service_modules import load_service_module

ks = load_service_module("trading-engine", "kraken_spot")

KEY = "k" * 56
SECRET = base64.b64encode(b"s" * 64).decode()


def test_signature_matches_the_spot_scheme_not_the_futures_one() -> None:
    """Recopier le schéma Futures produirait une signature refusée. On recalcule
    la référence à la main plutôt que de figer une constante opaque."""
    c = ks.KrakenSpotClient(KEY, SECRET)
    path, nonce, postdata = "/0/private/Balance", "1700000000000", "nonce=1700000000000"
    expected = base64.b64encode(
        hmac.new(
            base64.b64decode(SECRET),
            path.encode() + hashlib.sha256((nonce + postdata).encode()).digest(),
            hashlib.sha512,
        ).digest()
    ).decode()
    assert c.sign(path, nonce, postdata) == expected


def test_nonce_never_goes_backwards() -> None:
    """Kraken rejette définitivement un nonce inférieur au dernier vu. Deux
    appels dans la même milliseconde ne doivent pas produire le même."""
    c = ks.KrakenSpotClient(KEY, SECRET)
    nonces = [int(c._nonce()) for _ in range(50)]
    assert nonces == sorted(nonces)
    assert len(set(nonces)) == 50


def test_an_api_error_is_raised_even_though_kraken_answers_200() -> None:
    """Kraken renvoie HTTP 200 avec `{"error": ["EAPI:Invalid key"]}`. Sans ce
    contrôle, `result` serait vide et un solde de 0 s'afficherait comme un vrai
    solde — pire que pas de solde du tout."""
    with pytest.raises(ks.KrakenApiError, match="EAPI:Invalid key"):
        ks.unwrap({"error": ["EAPI:Invalid key"], "result": {}})


def test_unwrap_returns_the_result_when_there_is_no_error() -> None:
    assert ks.unwrap({"error": [], "result": {"ZUSD": "10.5"}}) == {"ZUSD": "10.5"}


def test_balances_are_parsed_from_strings_and_dust_is_dropped() -> None:
    """Kraken renvoie des montants en chaînes. Les comptes traînent des poussières
    à 1e-9 qui n'apportent rien et allongent le payload."""
    out = ks.parse_balances({"ZUSD": "1000.5000", "XXBT": "0.0100", "XETH": "0.00000000"})
    assert out == {"ZUSD": 1000.5, "XXBT": 0.01}


def test_equity_and_cash_come_from_the_two_endpoints() -> None:
    """`eb` de TradeBalance est le compte entier valorisé en USD ; le solde ZUSD
    de Balance est le cash seul. Confondre les deux afficherait la totalité du
    portefeuille comme si elle était disponible."""
    snap = ks.build_snapshot(
        trade_balance={"eb": "1234.5678", "tb": "1200.0"},
        balances={"ZUSD": "1000.0", "XXBT": "0.01"},
    )
    assert snap["equity_usd"] == 1234.5678
    assert snap["cash_usd"] == 1000.0
    assert snap["balances"] == {"ZUSD": 1000.0, "XXBT": 0.01}


def test_a_missing_quote_balance_means_zero_cash_not_a_crash() -> None:
    """Un compte entièrement investi n'a pas de ligne ZUSD."""
    snap = ks.build_snapshot(trade_balance={"eb": "500.0"}, balances={"XXBT": "0.01"})
    assert snap["cash_usd"] == 0.0
    assert snap["equity_usd"] == 500.0
```

- [ ] **Step 2: Run it, confirm the failure**

- [ ] **Step 3: Implement**

`services/trading-engine/app/kraken_spot.py` :

```python
"""Read-only Kraken *spot* client (api.kraken.com).

Separate from KrakenFuturesClient on two counts, neither cosmetic:

* The signing schemes differ. Spot signs
  `HMAC-SHA512(b64decode(secret), path + SHA256(nonce + postdata))`; Futures
  signs `HMAC-SHA512(secret, SHA256(postdata + nonce + path))`. Copying one for
  the other yields a rejected signature.
* This client is **not governed by the trading mode**. Reading a balance is
  always a real call, so the true portfolio shows correctly in dry_run. Only
  order placement is simulated. Mixing the two would make the reconciler close
  simulated positions against real ones.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import time
from typing import Any
from urllib.parse import urlencode

import httpx

BASE_URL = "https://api.kraken.com"
BALANCE_PATH = "/0/private/Balance"
TRADE_BALANCE_PATH = "/0/private/TradeBalance"
QUOTE_ASSET = "ZUSD"
# Kraken keeps sub-satoshi dust on old accounts; it adds nothing to a balance
# display and only lengthens the payload.
DUST = 1e-8


class KrakenApiError(RuntimeError):
    """Kraken answered 200 with a non-empty `error` array."""


def unwrap(payload: dict[str, Any]) -> Any:
    """Kraken signals failure *inside* a 200 response. Without this check an
    invalid key yields an empty `result`, which would render as a balance of 0 --
    worse than no balance, because it looks like an answer."""
    errors = payload.get("error") or []
    if errors:
        raise KrakenApiError("; ".join(errors))
    return payload.get("result", {})


def parse_balances(raw: dict[str, str]) -> dict[str, float]:
    out = {}
    for asset, amount in raw.items():
        value = float(amount)
        if abs(value) > DUST:
            out[asset] = value
    return out


def build_snapshot(
    *, trade_balance: dict[str, str], balances: dict[str, str]
) -> dict[str, Any]:
    """`eb` is the whole account valued in the quote currency; the ZUSD line of
    Balance is the cash alone. Reporting the first as the second would present
    the entire portfolio as spendable."""
    parsed = parse_balances(balances)
    return {
        "equity_usd": float(trade_balance.get("eb", 0.0)),
        "cash_usd": parsed.get(QUOTE_ASSET, 0.0),
        "balances": parsed,
    }


class KrakenSpotClient:
    def __init__(self, key: str, secret: str) -> None:
        self._key = key
        self._secret = secret
        self._http: httpx.AsyncClient | None = None
        self._tiebreak = itertools.count()

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0, base_url=BASE_URL)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def sign(self, path: str, nonce: str, postdata: str) -> str:
        message = path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
        mac = hmac.new(base64.b64decode(self._secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _nonce(self) -> str:
        """Strictly increasing. Kraken permanently rejects a nonce below the last
        one it saw, and two calls inside the same millisecond are routine, so the
        clock alone is not enough."""
        return str(int(time.time() * 1000) * 1000 + next(self._tiebreak) % 1000)

    async def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert self._http is not None, "client not started"
        body = {"nonce": self._nonce(), **(params or {})}
        postdata = urlencode(body)
        resp = await self._http.post(
            path,
            content=postdata,
            headers={
                "API-Key": self._key,
                "API-Sign": self.sign(path, body["nonce"], postdata),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
        return unwrap(resp.json())

    async def snapshot(self) -> dict[str, Any]:
        return build_snapshot(
            trade_balance=await self._post(TRADE_BALANCE_PATH, {"asset": QUOTE_ASSET}),
            balances=await self._post(BALANCE_PATH),
        )
```

`config.py` : ajouter au dataclass et à `from_env` —

```python
    read_api_key: str = ""
    read_api_secret: str = ""
    account_poll_s: int = 60
```
```python
            read_api_key=os.getenv("KRAKEN_READ_API_KEY", ""),
            read_api_secret=os.getenv("KRAKEN_READ_API_SECRET", ""),
            account_poll_s=int(os.getenv("CMI_ACCOUNT_POLL_S", "60")),
```

- [ ] **Step 4: Verify and commit**

`python -m pytest tests/test_kraken_spot.py -v` → 7 passed.

```bash
git commit -m "feat(trading-engine): read-only Kraken spot client

The spot signing scheme is not the Futures one -- different concatenation order,
different hash position -- so the existing client could never have read this
account, whatever the configuration.

Kraken reports failure inside a 200 response, so `error` is checked explicitly:
an invalid key otherwise yields an empty result that renders as a balance of 0,
which is worse than no balance because it looks like an answer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- services/trading-engine/app/kraken_spot.py services/trading-engine/app/config.py tests/test_kraken_spot.py
```

---

## Task 3 : la boucle de sondage

**Files:**
- Create: `services/trading-engine/app/account.py`
- Modify: `services/trading-engine/app/main.py`
- Test: `tests/test_account_poller.py`

- [ ] **Step 1: Write the failing test**

```python
"""La boucle de sondage du compte : ce qu'elle publie, et ce qu'elle fait quand
l'exchange ne répond pas."""

from __future__ import annotations

import pytest
from service_modules import load_service_module

acc = load_service_module("trading-engine", "account")


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list = []

    async def publish(self, topic, event) -> None:
        self.sent.append((topic, event))


class FakeCache:
    def __init__(self) -> None:
        self.written: dict = {}

    async def set_json(self, key, value, ttl_seconds: int = 60) -> None:
        self.written[key] = value


class FakeClient:
    def __init__(self, snap=None, error=None) -> None:
        self._snap = snap or {"equity_usd": 10.0, "cash_usd": 4.0,
                              "balances": {"ZUSD": 4.0}}
        self._error = error
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._snap


async def test_one_poll_publishes_and_caches() -> None:
    p, c = FakeProducer(), FakeCache()
    poller = acc.AccountPoller(FakeClient(), p, c, venue="kraken_spot")
    await poller.poll_once()
    topic, event = p.sent[0]
    assert event.venue == "kraken_spot"
    assert event.equity_usd == 10.0
    assert c.written["trading:account:kraken_spot"]["equity_usd"] == 10.0


async def test_an_exchange_failure_publishes_nothing() -> None:
    """Mieux vaut pas de snapshot qu'un snapshot faux : l'absence se traduit par
    « non connecté » côté lecture, un zéro se traduirait par « vous n'avez rien »."""
    p, c = FakeProducer(), FakeCache()
    poller = acc.AccountPoller(FakeClient(error=RuntimeError("boom")), p, c,
                               venue="kraken_spot")
    await poller.poll_once()
    assert p.sent == []
    assert c.written == {}


async def test_a_failure_does_not_stop_the_loop() -> None:
    """Une clé temporairement rejetée ou un timeout ne doit pas tuer la boucle
    pour le reste de la vie du processus."""
    p, c = FakeProducer(), FakeCache()
    poller = acc.AccountPoller(FakeClient(error=RuntimeError("boom")), p, c,
                               venue="kraken_spot")
    await poller.poll_once()
    await poller.poll_once()  # ne doit pas lever


def test_no_key_means_no_provider_at_all() -> None:
    """« Aucune clé configurée → le venue est absent, pas en erreur. » Construire
    un client sans clé produirait une boucle qui échoue toutes les 60 s et remplit
    les logs pour rien."""
    assert acc.build_poller(_cfg(key="", secret=""), None, None) is None
    assert acc.build_poller(_cfg(key="k", secret=""), None, None) is None


def _cfg(*, key: str, secret: str):
    from types import SimpleNamespace
    return SimpleNamespace(read_api_key=key, read_api_secret=secret,
                           account_poll_s=60)
```

- [ ] **Step 2: Run it, confirm the failure**

- [ ] **Step 3: Implement**

`services/trading-engine/app/account.py` :

```python
"""Periodic account-balance poll.

Deliberately independent of the trading mode: reading a balance is always a real
call, so the operator sees their true portfolio while the bot is still in
dry_run. Only order placement is simulated.
"""

from __future__ import annotations

import asyncio
import logging

from cmi_common.cache import Cache
from cmi_common.events.account import AccountSnapshotEvent
from cmi_common.kafka import EventProducer, Topic

from .kraken_spot import KrakenSpotClient

logger = logging.getLogger(__name__)

REDIS_KEY = "trading:account:{venue}"
# Twice the default poll interval: a key that outlives two missed polls would
# let control-api serve a snapshot the read plane has already called stale.
CACHE_TTL_MULTIPLE = 2


class AccountPoller:
    def __init__(self, client, producer: EventProducer, cache: Cache, *,
                 venue: str, interval_s: int = 60) -> None:
        self._client = client
        self._producer = producer
        self._cache = cache
        self._venue = venue
        self._interval = interval_s
        self._stop = asyncio.Event()

    async def poll_once(self) -> None:
        try:
            snap = await self._client.snapshot()
        except Exception:
            # No snapshot beats a wrong one: absence reads as "not connected"
            # downstream, whereas a zero would read as "you own nothing".
            logger.warning("account snapshot failed for %s", self._venue,
                           exc_info=True)
            return
        event = AccountSnapshotEvent(venue=self._venue, **snap)
        await self._producer.publish(Topic.ACCOUNT_SNAPSHOT, event)
        await self._cache.set_json(
            REDIS_KEY.format(venue=self._venue),
            {"venue": self._venue, "fetched_at": event.occurred_at.isoformat(),
             **snap},
            ttl_seconds=self._interval * CACHE_TTL_MULTIPLE,
        )

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()


def build_poller(config, producer, cache) -> AccountPoller | None:
    """None when no read-only key is configured.

    The spec's rule: no key means the venue is *absent*, not failing. Building a
    client anyway would produce a loop that errors every 60 seconds and fills the
    logs to say nothing.
    """
    if not (config.read_api_key and config.read_api_secret):
        return None
    client = KrakenSpotClient(config.read_api_key, config.read_api_secret)
    return AccountPoller(client, producer, cache, venue="kraken_spot",
                         interval_s=config.account_poll_s)
```

**Signatures vérifiées contre le dépôt** (2026-07-26) : `EventProducer.publish(topic, event)`
— *pas* `send` — et `Cache.set_json(key, value, ttl_seconds=60)` — *pas* `ttl`.
Le code et les fakes ci-dessus sont déjà corrigés en conséquence.

- [ ] **Step 4: Wire it into `main.py`**

Après le démarrage du producer, sur le modèle du reconciler existant :

```python
    poller = build_poller(config, producer, cache)
    if poller is not None:
        await poller._client.start()
        app.state.account_poller = poller
        app.state.account_task = asyncio.create_task(poller.run())
```

L'arrêter dans le shutdown existant (`stop()` puis fermer le client HTTP), et
n'ajouter sa tâche au `gather` que si elle existe. Lire `main.py` pour la forme
exacte du shutdown.

- [ ] **Step 5: Verify and commit**

```bash
git commit -m "feat(trading-engine): poll the real account balance on its own loop

Independent of the trading mode on purpose: reading a balance is always a real
call, so the operator sees their true portfolio while the bot is still in
dry_run. Order placement stays simulated -- coupling the two would make the
reconciler close simulated positions against real ones.

No key configured means no poller at all, per the spec: constructing a client
anyway would produce a loop that fails every 60 seconds to say nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" -- services/trading-engine/app/account.py services/trading-engine/app/main.py tests/test_account_poller.py
```

---

## Task 4 : persistance du snapshot

**Files:**
- Create: `migrations/alembic/versions/0012_account_snapshots.py`
- Modify: `libs/cmi_common/cmi_common/db/models.py`, `.../db/__init__.py`,
  `services/api-gateway/app/persister.py`, `services/api-gateway/app/main.py`
- Test: `tests/test_account_persistence.py`

- [ ] **Step 1: Write the migration**

Table **non** hypertable : un enregistrement par venue et par minute, soit 1 440
lignes par jour pour un venue. Le partitionnement temporel ne rapporterait rien
et compliquerait la requête « dernier état par venue ».

```python
"""account_snapshots

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("equity_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("cash_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("balances", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Serves the only query there is: the latest snapshot for a venue.
    op.create_index(
        "ix_account_snapshots_venue_time",
        "account_snapshots", ["venue", sa.text("fetched_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_account_snapshots_venue_time", table_name="account_snapshots")
    op.drop_table("account_snapshots")
```

- [ ] **Step 2: Model, persister branch, consumer topic**

Modèle `AccountSnapshot` dans `models.py` (colonne `fetched_at` en
`DateTime(timezone=True)` — c'est ce que la migration crée), exporté depuis
`cmi_common.db`.

Dans `persister.py`, une branche `AccountSnapshotEvent` sur le modèle des
existantes, en `on_conflict_do_nothing` sur `event_id` (Kafka est at-least-once),
avec son `EVENTS_CONSUMED.labels(...)`. **Utiliser la `.value` du type
d'événement**, pas le membre d'enum, comme partout ailleurs.

Ajouter `Topic.ACCOUNT_SNAPSHOT` à la liste du consumer **persister** dans
`main.py`. L'archiveur le récupère déjà : son routage par défaut envoie tout type
inconnu vers `events_signal`, ce que la spec demande explicitement.

- [ ] **Step 3: Test**

```python
"""Le snapshot atterrit en base une fois et une seule."""
```

Écrire, sur le modèle exact de `tests/test_archiver_writes.py` (fake session,
fake db), deux tests : le snapshot produit un `INSERT` dans `account_snapshots`,
et une redélivrance ne produit pas de doublon (`ON CONFLICT` présent dans le
statement).

- [ ] **Step 4: Verify**

`cd migrations && python -m alembic upgrade head --sql` puis
`python -m alembic downgrade head:0011 --sql` — la forme `head:0011` est
obligatoire, un downgrade hors-ligne refuse une cible nue.

- [ ] **Step 5: Commit**

---

## Task 5 : fin de la fiction dans le plan de lecture

**Files:**
- Modify: `services/api-gateway/app/read_api.py`, `.../read_contract.py`
- Test: `tests/test_portfolio_balance.py`, `tests/test_read_contract.py`

- [ ] **Step 1: Write the failing test**

```python
"""`kraken_balance_usd` : un vrai solde, ou rien — jamais un nombre inventé."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from service_modules import load_service_module

read_api = load_service_module("api-gateway", "read_api")

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def test_without_a_snapshot_the_balance_is_null_and_says_so() -> None:
    """Un nombre inventé est pire qu'une absence : l'opérateur ne peut pas
    distinguer « je n'ai rien » de « je ne suis pas connecté »."""
    p = read_api.compute_portfolio([], 0.0, snapshot=None, now=NOW)
    assert p["kraken_balance_usd"] is None
    assert p["balance_source"] == "unavailable"
    assert p["balance_stale"] is False
    assert p["balance_fetched_at"] is None


def test_a_fresh_snapshot_is_served_as_is() -> None:
    snap = {"venue": "kraken_spot", "equity_usd": 1234.56, "cash_usd": 1000.0,
            "fetched_at": NOW - timedelta(seconds=30)}
    p = read_api.compute_portfolio([], 0.0, snapshot=snap, now=NOW)
    assert p["kraken_balance_usd"] == 1234.56
    assert p["balance_source"] == "kraken_spot"
    assert p["balance_stale"] is False


def test_a_snapshot_older_than_five_minutes_is_flagged_stale_not_dropped() -> None:
    """Une valeur périmée reste plus informative qu'un vide — à condition d'être
    annoncée comme périmée, sinon elle passe pour fraîche."""
    snap = {"venue": "kraken_spot", "equity_usd": 1234.56, "cash_usd": 1000.0,
            "fetched_at": NOW - timedelta(minutes=6)}
    p = read_api.compute_portfolio([], 0.0, snapshot=snap, now=NOW)
    assert p["kraken_balance_usd"] == 1234.56
    assert p["balance_stale"] is True


def test_a_real_snapshot_becomes_the_reference_capital() -> None:
    """Sans cela, un vrai solde Kraken s'afficherait à côté de positions
    dimensionnées sur un capital imaginaire de 100 000 $."""
    snap = {"venue": "kraken_spot", "equity_usd": 5000.0, "cash_usd": 5000.0,
            "fetched_at": NOW}
    assert read_api.reference_capital(snap) == 5000.0


def test_without_a_snapshot_the_configured_capital_is_the_fallback() -> None:
    assert read_api.reference_capital(None) == read_api.BASE_CAPITAL


def test_a_stale_snapshot_still_governs_the_capital() -> None:
    """Un solde d'il y a dix minutes reste une bien meilleure approximation du
    capital réel que la constante de configuration."""
    snap = {"venue": "kraken_spot", "equity_usd": 5000.0, "cash_usd": 5000.0,
            "fetched_at": NOW - timedelta(minutes=10)}
    assert read_api.reference_capital(snap) == 5000.0
```

- [ ] **Step 2: Implement**

Dans `read_api.py` :

```python
# A snapshot older than this is shown greyed rather than as a current balance.
STALE_AFTER_S = 300


def reference_capital(snapshot: dict | None) -> float:
    """The real equity when we have one, the configured constant otherwise.

    Without this, a real Kraken balance would be displayed next to positions
    sized against an imaginary 100k. A stale snapshot still governs: a balance
    from ten minutes ago approximates the real capital far better than a
    configuration constant does.
    """
    if snapshot and snapshot.get("equity_usd") is not None:
        return float(snapshot["equity_usd"])
    return BASE_CAPITAL
```

`compute_portfolio` gagne un paramètre `snapshot: dict | None = None`, cesse de
calculer `cash * 0.8`, et renvoie en plus `balance_source`,
`balance_fetched_at`, `balance_stale`. Le `base_capital` de la signature devient
`reference_capital(snapshot)` **au point d'appel de l'endpoint**, et ce même
capital doit être passé à `map_position` et `map_portfolio_trade` — sinon les
positions restent dimensionnées sur la constante et le total ne correspond plus
au solde affiché.

L'endpoint charge le dernier snapshot par venue (`ORDER BY fetched_at DESC LIMIT 1`).

`read_contract.py` : ajouter `balance_source`, `balance_fetched_at`,
`balance_stale` à l'entrée `portfolio`.

- [ ] **Step 3: Verify** — `tests/test_read_contract.py` doit continuer à passer ;
  ajuster ses fakes si nécessaire.

- [ ] **Step 4: Commit**

---

## Task 6 : frontend

**Files:**
- Modify: `frontend/src/lib/types/domain.ts`, le composant du bandeau capital
  (le localiser avec `grep -rn "kraken_balance_usd" frontend/src`), et le mock
  correspondant.

- [ ] **Step 1:** `Portfolio.kraken_balance_usd` devient `number | null` ; ajouter
  `balance_source: 'kraken_spot' | 'kraken_futures' | 'unavailable'`,
  `balance_fetched_at: string | null`, `balance_stale: boolean`.

- [ ] **Step 2:** Affichage — `null` rend **« — · non connecté »** et non « 0 $ » ;
  un snapshot périmé rend la valeur grisée avec l'âge (« il y a 8 min »). Le mock
  doit servir un snapshot frais pour que le chemin nominal reste démontrable, et
  la route mock doit pouvoir rendre le cas `unavailable` (c'est l'état réel de la
  production tant qu'aucune clé n'est posée).

- [ ] **Step 3:** `npx tsc --noEmit && npm run build`, puis charger réellement la
  page Capital en mode mock et décrire ce qui s'affiche.

- [ ] **Step 4: Commit**

---

## Task 7 : vérification

- [ ] **Step 1** — `python -m pytest tests/ --tb=no` → 3 échecs connus seulement.
- [ ] **Step 2** — delta de lint contre `master` : `python -m ruff check libs services
  --statistics` doit rendre le même total qu'en tête de `master`.
- [ ] **Step 3** — `cd migrations && python -m alembic upgrade head --sql` puis
  `python -m alembic downgrade head:0011 --sql`.
- [ ] **Step 4** — `cd frontend && npx tsc --noEmit && npm run build`.
- [ ] **Step 5 — recette sans clé (le chemin réel aujourd'hui)** : démarrer la
  stack, vérifier que trading-engine **ne** loggue **aucune** erreur de compte,
  que `/portfolio` renvoie `kraken_balance_usd: null` et
  `balance_source: "unavailable"`, et que la page Capital affiche « non connecté ».
- [ ] **Step 6 — recette avec clé (bloquée sur l'opérateur)** : après régénération
  d'une paire « Query Funds », poser `KRAKEN_READ_API_KEY` /
  `KRAKEN_READ_API_SECRET` sur le VPS, redémarrer trading-engine, puis :

```bash
ssh <VPS_USER>@<VPS_HOST> "docker exec bottrading-postgres-1 psql -U cmi -d cmi -c \
  'SELECT venue, equity_usd, cash_usd, fetched_at FROM account_snapshots \
   ORDER BY fetched_at DESC LIMIT 3;'"
```

**Le test d'acceptation est la comparaison avec l'écran Kraken.** Si `equity_usd`
ne correspond pas au solde affiché par Kraken, le parsing est faux — et un solde
faux est pire que pas de solde.

---

## Self-review

**Couverture de la spec**

| Exigence | Tâche |
|---|---|
| Clés confinées au trading-engine | 2, 3 |
| Paire lecture seule distincte | 2 |
| Consultation indépendante du mode | 3 |
| `AccountSnapshotProvider` multi-venue | 2 (spot ; Futures ultérieur) |
| Topic + événement | 1 |
| Persistance + Redis | 3, 4 |
| Fin de `cash × 0.8` | 5 |
| `balance_source` / `_fetched_at` / `_stale` | 5 |
| Capital de référence réel | 5 |
| Affichage « non connecté » / périmé | 6 |
| Diffusion WebSocket du snapshot | 1 |

**Écarts assumés**

- `KrakenFuturesProvider` n'est pas écrit : le compte est spot, et une abstraction
  à un seul implémenteur se conçoit mieux quand le second arrive vraiment. Le
  protocole se réduit à `async def snapshot() -> dict`, que `KrakenSpotClient`
  satisfait déjà.
- `account_snapshots` n'est pas une hypertable — 1 440 lignes/jour/venue ne
  justifient pas le partitionnement, et « dernier état par venue » est plus
  simple sans.
