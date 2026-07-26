# Journal contrefactuel — design

**Date:** 2026-07-26
**Statut:** design, en attente de validation opérateur
**Dépend de:** [[2026-07-26-state-change-dedup-design]] (§12 mode ombre)
**Contexte:** [[2026-07-26-ai-cost-quality-strategy]] §4

---

## 1. La question à laquelle il doit répondre

> **Les appels IA supplémentaires créent-ils réellement de la valeur ?**

Tout le reste en découle. Trois sous-questions opérationnelles :

| # | Question | Population comparée |
|---|---|---|
| Q1 | Une décision rejetée par le risk-engine aurait-elle été profitable ? | approuvées vs rejetées, à score comparable |
| Q2 | Un signal non escaladé aurait-il mérité une analyse ? | escaladés vs non escaladés |
| Q3 | Les validations Sonnet apportent-elles de la valeur ? | validés vs rejetés **parmi les escaladés** |

**Q3 impose sa méthode.** Sonnet ne voit que des signaux escaladés : comparer
« escaladés » à « non escaladés » mesurerait la qualité du *gate*, pas celle de
Sonnet — les deux populations diffèrent déjà avant qu'il n'intervienne. La seule
comparaison propre est **validés contre rejetés à l'intérieur de la population
escaladée** : même gate, même seuil, seul le verdict de l'analyste diffère.

Q2 souffre du biais symétrique et doit être lue avec prudence : elle mesure le
gate, pas Sonnet, et les deux populations ne sont pas appariées.

---

## 2. Principe : stocker les discriminants, pas les verdicts

L'exigence de rejouer plusieurs stratégies de seuil (p95, p99, p99,5, appris)
détermine la conception.

**Un journal qui stocke « la politique p99 a dit oui » ne permet de rejouer que
p99.** Il faudrait décider à l'avance de la liste des politiques, ré-instrumenter
à chaque nouvelle idée, et on ne pourrait jamais tester une politique apprise.

**Le journal stocke donc les quantités brutes dont tout verdict se déduit** — les
dérives par facteur, les changements de signe, l'âge de l'ancre, les scores. Une
politique devient alors une fonction pure appliquée *a posteriori* aux lignes
enregistrées. Nombre de politiques : illimité. Ré-instrumentation : nulle.

### Corollaire : le rejeu utilise la fonction de production

Le module `dedup.py` (spécifié dans le design de déduplication) est **sans I/O** :
il prend un état courant, une ancre et un jeu de seuils, et renvoie
`(déclencher, motif)`. Le rejeu appelle **exactement cette fonction**, alimentée
par les lignes du journal au lieu de Redis.

C'est non négociable : un rejeu qui réimplémenterait la logique testerait la
réimplémentation, pas le système. Toute divergence entre les deux passerait
inaperçue et invaliderait silencieusement l'analyse.

---

## 3. Ce qu'on stocke, ce qu'on recalcule

| Donnée | Stockée ? | Pourquoi |
|---|---|---|
| Dérives par facteur, signes, âge d'ancre | **Oui** | dépendent de l'ancre Redis, éphémère et irrécupérable |
| Verdict Sonnet, score, confiance | **Oui** | événement ponctuel, non reproductible |
| Seuils en vigueur + version de calibration | **Oui** | sans eux, un verdict passé est inexplicable |
| Verdict risk-engine et son motif | **Oui** | idem |
| Features et facteurs | **Oui** | rend le journal autonome si `signals` est purgé |
| **Prix à +1 h / +4 h / +24 h** | **Non** | déductibles de `prices` par jointure |
| **P&L simulé** | **Non** | fonction des prix et des niveaux, recalculable |

### Pourquoi ne pas matérialiser les prix futurs

Trois raisons, dans l'ordre d'importance :

1. **Pas d'état partiel.** Une colonne `price_24h` reste nulle pendant 24 h après
   chaque ligne. Toute requête doit alors distinguer « pas encore mûr » de
   « donnée manquante », et chaque analyse doit gérer ce tri. Calculé à la volée,
   le cas se réduit à « pas de prix disponible à cet instant », qui est traité
   uniformément.
2. **Pas de tâche de remplissage.** Matérialiser exigerait un job différé,
   donc un ordonnanceur, donc un mode de panne silencieux — un job arrêté produit
   un journal qui a l'air complet mais ne l'est pas.
3. **Horizons libres.** À la volée, ajouter un horizon à +12 h ou +72 h est une
   requête. Matérialisé, c'est une migration plus un rattrapage.

`prices` est une hypertable indexée sur `(symbol, time)`. Le coût d'une jointure
latérale sur ~700 k lignes de journal reste acceptable ; si la mesure prouve le
contraire, une vue continue Timescale se rajoute sans changer le schéma.

---

## 4. Schéma

Une table, dénormalisée à dessein — un journal se lit analytiquement, pas en
jointures.

```sql
CREATE TABLE decision_journal (
    time                   TIMESTAMPTZ NOT NULL,
    symbol                 TEXT        NOT NULL,
    signal_event_id        TEXT        NOT NULL,

    -- état décisionnel au moment T
    factors                JSONB       NOT NULL,   -- facteurs normalisés courants
    anchor_factors         JSONB,                  -- NULL si T1
    features               JSONB       NOT NULL,   -- brutes, pour l'analyse post-hoc
    score                  INTEGER     NOT NULL,
    confidence             REAL        NOT NULL,
    factors_present        SMALLINT    NOT NULL,

    -- discriminants bruts : tout verdict de politique s'en déduit
    drift_momentum         REAL,
    drift_volume           REAL,
    drift_sentiment        REAL,
    drift_liquidity        REAL,
    sign_flip_chg          BOOLEAN     NOT NULL DEFAULT FALSE,
    sign_flip_sentiment    BOOLEAN     NOT NULL DEFAULT FALSE,
    score_anchor           INTEGER,
    factors_present_anchor SMALLINT,
    seconds_since_anchor   INTEGER,
    regime                 TEXT,
    regime_anchor          TEXT,
    market_cap_rank        INTEGER,

    -- ce qui s'est réellement passé
    cooldown_verdict       BOOLEAN     NOT NULL,   -- autorité pendant le mode ombre
    dedup_verdict          BOOLEAN     NOT NULL,   -- ombre
    dedup_trigger          TEXT,                   -- T1..T7, NULL si pas de déclenchement
    sonnet_called          BOOLEAN     NOT NULL,
    sonnet_validated       BOOLEAN,
    sonnet_score           INTEGER,
    sonnet_confidence      REAL,
    sonnet_direction       TEXT,

    -- aval
    decision_event_id      TEXT,
    risk_verdict           TEXT,                   -- approved | rejected | not_reached
    risk_reason            TEXT,
    entry_price            NUMERIC(38,12),
    stop_loss              NUMERIC(38,12),
    take_profit            NUMERIC(38,12),
    position_size_pct      REAL,

    -- provenance de calibration
    dedup_version          TEXT        NOT NULL,
    dedup_quantile         REAL,
    dedup_deltas           JSONB       NOT NULL,
    thresholds_in_force    JSONB       NOT NULL,   -- escalate/decision/risk du moment

    PRIMARY KEY (time, signal_event_id)
);
SELECT create_hypertable('decision_journal', 'time');
```

Rétention **180 jours** — le double de la table `events_signal`, parce qu'un
horizon de 24 h plus une fenêtre statistique de plusieurs semaines rend ce journal
plus lent à mûrir que tout le reste.

### Population journalisée

**Toutes les analyses**, pas seulement les escaladées.

Q2 (« un signal non escaladé aurait-il mérité une analyse ? ») exige la population
non escaladée : sans elle, il n'y a pas de groupe témoin et le gate devient
invérifiable.

Volume : ~7 735 lignes/jour aujourd'hui, ~1,4 M sur 180 jours. Confortable pour
une hypertable. Les colonnes propres à l'aval restent nulles pour les non
escaladées — c'est leur signification, pas un défaut de remplissage.

---

## 5. Résultats marché et P&L simulé

### 5.1 Prix aux horizons

```sql
CREATE FUNCTION price_at(p_symbol TEXT, p_time TIMESTAMPTZ)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT price_usd FROM prices
    WHERE symbol = p_symbol AND time >= p_time AND time < p_time + interval '10 minutes'
    ORDER BY time LIMIT 1
$$;
```

Fenêtre de tolérance de 10 minutes : au-delà, le prix n'est pas « celui de cet
instant » et la fonction renvoie `NULL` plutôt qu'une valeur trompeuse. **Un trou
de collecte doit produire une absence, pas une approximation** — sans quoi les
pannes de collecteur se transformeraient en faux résultats.

### 5.2 P&L simulé — le chemin, pas le point

Un rendement à +24 h ne suffit pas : une position avec stop-loss peut être sortie
à −5 % puis voir le prix remonter. Comparer au prix final surestimerait
systématiquement la performance.

La simulation parcourt donc **le chemin de prix** et applique la première borne
touchée :

```
simulate(symbol, t0, entry, direction, sl, tp, horizon) :
    parcourir prices(symbol) sur [t0, t0+horizon] par ordre chronologique
    si direction = long  : sortie au premier px ≤ sl (perte) ou px ≥ tp (gain)
    si direction = short : sortie au premier px ≥ sl (perte) ou px ≤ tp (gain)
    aucune borne atteinte → valorisation au dernier prix de la fenêtre
    retourner (issue, prix_sortie, secondes_écoulées, pnl_brut, pnl_net)
```

`issue` ∈ `stop_loss` | `take_profit` | `horizon` | `no_data`.

**Frais inclus** : 0,16 % par côté, cohérent avec `read_api.map_portfolio_trade`.
`pnl_net = pnl_brut − frais_entrée − frais_sortie`. Les ignorer rendrait
profitable une stratégie à faible espérance et fausserait toute calibration de
seuil.

### 5.3 Limites explicites

La simulation **ignore** :

- le **slippage** — l'entrée et la sortie sont supposées au prix affiché ;
- la **profondeur de carnet** — une taille de position n'est jamais irréalisable ;
- la **granularité** — les prix sont échantillonnés à ~60 s ; une mèche
  intra-minute touchant le stop est invisible, ce qui **surestime** la performance
  des positions proches de leur stop ;
- la **latence d'exécution** entre décision et ordre.

Ces biais vont tous dans le même sens : **la simulation est optimiste.** Un
résultat marginalement profitable en simulation doit être traité comme non
profitable. C'est une limite du dispositif, pas un réglage à corriger — la
mesurer exigerait des données de carnet qu'on ne collecte pas.

---

## 6. Rejeu de politiques

### 6.1 Politiques de déduplication

```python
def replay(rows, policy) -> ReplayResult:
    """`policy` : jeu de DELTA + MAX_AGE + déclencheurs actifs.
    Appelle dedup.should_call — la fonction de production — sur chaque ligne."""
```

Sortie par politique :

| Métrique | Calcul |
|---|---|
| Appels déclenchés | lignes où la politique dit oui |
| Appels évités vs cooldown | `cooldown_verdict AND NOT policy` |
| Appels supplémentaires vs cooldown | `policy AND NOT cooldown_verdict` |
| **Signaux manqués** | `cooldown_verdict AND NOT policy AND sonnet_validated` |
| Gain de latence | distribution de `seconds_since_anchor` sur les appels supplémentaires |
| Couverture | symboles distincts touchés |

Politiques comparées d'emblée : p95, p99, p99,5, cooldown 900 s (référence), et
tout jeu appris ultérieurement. **Aucune n'est appliquée en production par ce
mécanisme** — le rejeu produit un tableau comparatif, la décision reste humaine.

### 6.2 La limite du rejeu, à énoncer

Un appel Sonnet supplémentaire qu'une politique aurait déclenché **n'a pas de
verdict** : Sonnet n'a jamais été interrogé sur cet état. Le rejeu ne peut donc
pas dire « cette politique aurait produit N validations de plus ».

Deux approches, et une seule est honnête à court terme :

1. **Borne par le résultat marché** — pour un appel supplémentaire, on ignore ce
   qu'aurait dit Sonnet mais on connaît le mouvement de prix qui a suivi. On peut
   donc mesurer : *les états que cette politique aurait analysés en plus étaient-ils
   suivis de mouvements exploitables ?* C'est une borne supérieure de la valeur
   ajoutée — elle suppose que Sonnet aurait validé les bons.
2. **Rejeu réel par lot** — soumettre a posteriori les états manquants à Sonnet
   via la Batch API (−50 %, latence sans importance en rétrospectif). Coûteux en
   quota, mais donne le verdict réel.

**Recommandation : (1) d'abord.** Gratuite, immédiate, et suffisante pour éliminer
les politiques manifestement mauvaises. (2) ne se justifie que pour départager
deux politiques finalistes.

---

## 7. Les trois questions, opérationnalisées

### Q1 — Une décision rejetée aurait-elle été profitable ?

```
population : risk_verdict = 'rejected'
mesure     : simulate(...) avec les niveaux qu'on aurait appliqués
comparaison: espérance nette des rejetées vs des approuvées
```

Les niveaux SL/TP sont déterministes (5 % / 10 % dans `rules._compute_levels`),
donc calculables même pour une décision refusée. Le refus est stratifié par motif
(`risk_reason`) : un rejet pour confiance insuffisante ne se lit pas comme un
rejet pour exposition maximale.

**Lecture attendue :** si les rejetées ont une espérance nette positive
significativement supérieure aux approuvées, les planchers sont trop stricts. Si
elles sont autour de zéro ou négatives, ils font leur travail.

### Q2 — Un signal non escaladé aurait-il mérité une analyse ?

```
population : escalated = false
mesure     : rendement à +4 h / +24 h
comparaison: vs escaladés, apparié par tranche de score et factors_present
```

**L'appariement par `factors_present` est indispensable.** Sans lui, on
comparerait des scores bâtis sur 2 facteurs à des scores bâtis sur 4 — les deux
n'ont pas le même sens, comme la phase 1 l'a établi.

**Lecture attendue :** si des non escaladés à score comparable produisent des
mouvements équivalents, le gate laisse passer de la valeur et `escalate_score`
mérite d'être abaissé.

### Q3 — Les validations Sonnet créent-elles de la valeur ?

```
population : sonnet_called = true
comparaison: sonnet_validated = true  vs  sonnet_validated = false
mesure     : rendement dirigé selon sonnet_direction, à +1 h / +4 h / +24 h
```

**C'est la question centrale, et la comparaison est propre** : les deux groupes
ont franchi le même gate et été soumis au même analyste. Seul son verdict diffère.

**Lecture attendue :** si les validés ne surperforment pas les rejetés, Sonnet
n'apporte pas de discrimination et aucun réglage de budget ou de seuil ne le
corrigera — c'est le prompt ou le modèle qu'il faudrait revoir.

### La contrainte d'échantillon, dite franchement

Au rythme actuel — 11 appels et ~1 validation par jour — **Q3 demande 30 à
50 jours** pour disposer de quelques dizaines d'observations par groupe. Q1 est
pire : zéro trade approuvé à ce jour.

Ce n'est pas un défaut du journal, c'est l'état du pipeline. Et cela **renforce
l'ordre de travail choisi** : élargir le vivier (levier S3) n'améliore pas
seulement la couverture, c'est ce qui rend l'analyse statistique atteignable dans
un délai utile. Un journal alimenté par 2 symboles mettra des mois à conclure.

---

## 8. Restitution

`GET /systems/journal/summary?window=30d` :

```json
{
  "window": "30d",
  "sample": {"analyses": 0, "escalated": 0, "sonnet_called": 0,
             "validated": 0, "approved": 0, "matured_24h": 0},
  "q1_rejected_vs_approved": {"n_rejected": 0, "n_approved": 0,
                              "expectancy_rejected": null, "expectancy_approved": null,
                              "by_reason": []},
  "q2_gate_discrimination":  {"by_score_band": []},
  "q3_sonnet_value":         {"n_validated": 0, "n_rejected": 0,
                              "return_validated": null, "return_rejected": null,
                              "confidence_interval": null},
  "policy_replay": [{"policy": "p99", "calls": 0, "avoided": 0,
                     "additional": 0, "missed_signals": 0, "median_latency_gain_s": null}]
}
```

**`sample` est en tête à dessein.** Chaque réponse statistique est accompagnée de
son effectif, et une comparaison sous 30 observations par groupe renvoie `null`
plutôt qu'un chiffre. Un intervalle de confiance calculé sur n=3 est plus
dangereux qu'une absence de réponse : il invite à agir.

`matured_24h` compte les lignes dont l'horizon de 24 h est écoulé — c'est le seul
effectif exploitable pour les mesures à cet horizon, et il est distinct du nombre
de lignes journalisées.

---

## 9. Écriture

Le journal est écrit par **`ai-worker-sonnet`**, seul point du système qui
connaît simultanément l'état, l'ancre, les deux verdicts et l'issue Sonnet.

Une ligne est émise à chaque analyse reçue, y compris non escaladée. L'aval
(`risk_verdict`, niveaux, `decision_event_id`) est complété par une mise à jour
depuis l'api-gateway à réception du `RiskApprovedEvent` ou du `RiskRejectedEvent`,
appariée sur `decision_event_id`.

**L'écriture ne bloque jamais la décision.** En cas d'échec, on journalise et on
continue : perdre une ligne d'analyse est acceptable, retarder un signal de
trading ne l'est pas. Même principe qu'à §7 du design de déduplication —
l'incertitude se résout toujours en faveur du chemin de production.

---

## 10. Périmètre

**Dans le périmètre**
- Migration : hypertable `decision_journal` + rétention 180 j
- `price_at()` et la fonction de simulation de chemin
- Écriture depuis `ai-worker-sonnet`, complément depuis `api-gateway`
- `scripts/replay_policies.py` — rejeu appelant `dedup.should_call`
- `GET /systems/journal/summary` + entrée au contrat de lecture
- Panneau de restitution sur le Command Center

**Hors périmètre**
- Toute modification de seuil — le journal mesure, il ne décide pas
- La calibration automatique (priorité 4)
- Le rejeu Sonnet par lot (§6.2 option 2) — à décider une fois deux politiques
  finalistes identifiées

---

## 11. Questions ouvertes pour l'opérateur

1. **Journaliser toutes les analyses** (~7 735/jour) plutôt que les seules
   escaladées (~400/jour). C'est ce qui rend Q2 vérifiable ; sans groupe témoin,
   le gate d'opportunité reste invérifiable pour toujours. Coût : ~1,4 M lignes
   sur 180 jours.
2. **Effectif minimum de 30 par groupe** avant toute réponse chiffrée. Volontairement
   conservateur — la conséquence est qu'il ne faut pas espérer de conclusion sur
   Q3 avant plusieurs semaines, et aucune sur Q1 tant qu'aucun trade n'est passé.
3. **Horizons +1 h / +4 h / +24 h.** Fixés par jugement, pas par mesure. Si
   l'horizon de détention visé diffère, il faut le dire maintenant : c'est le seul
   paramètre de ce design qui dépend de ta stratégie de trading et non des
   données.
