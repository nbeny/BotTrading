# Positionnement Kraken Futures — collecte et scoring

**Date :** 2026-08-06
**Statut :** validé, prêt à planifier
**Services touchés :** `collector-kraken-futures` (nouveau), `decision-engine`,
`api-gateway`, `frontend`, `libs/cmi_common`
**Relation aux specs antérieures :** étend l'axe `positioning` introduit par
`2026-07-31-derivatives-fundamentals-scoring-v2-design.md`, sans en modifier le poids.

## Périmètre

Cette spec couvre **l'étage lent uniquement** : les métriques dont l'horizon utile se
compte en heures, ingérées comme signaux de scoring.

Un **étage rapide** — carnet d'ordres, profondeur, CVD intra-journalier, slippage
attendu — a été identifié dans la même session de cadrage et fera l'objet d'une spec
séparée. Il vit dans `trading-engine`, juste avant `send_order`, et non dans le pipeline
de scoring. La raison est une incompatibilité de fréquence : le `FeatureStore` expire à
900 s, alors qu'un carnet d'ordres se périme en secondes. Les deux étages ne peuvent pas
partager un chemin de données.

L'étage rapide dépend en outre d'une connexion Kraken Futures opérationnelle, qui reste
bloquée par trois défauts documentés séparément (clés absentes du rendu `.env`, pas de
taille de contrat global, portefeuille spot affiché pour un moteur qui trade en Futures).
**L'étage lent ne dépend d'aucun d'eux** : il n'utilise que des endpoints publics et ne
requiert ni compte Futures ni clé API. C'est ce qui justifie de le livrer en premier.

## Problème

L'axe `positioning` (poids 0.1380) lit le funding, l'open interest et le ratio long/short
depuis **Binance**. Or les ordres partent chez **Kraken**. Le funding qui entre dans le
score n'est pas celui qui sera effectivement payé, et l'encombrement mesuré est celui d'un
carnet où la plateforme ne prendra jamais position.

Deux classes de signal restent par ailleurs absentes de l'axe alors qu'elles décrivent
exactement le phénomène qu'il prétend mesurer :

1. **Les liquidations.** L'encombrement mesuré par le funding décrit une tension ; les
   liquidations décrivent sa résolution. Un marché à funding positif élevé et un marché
   qui vient de purger ses longs se ressemblent sur le funding seul, et sont deux
   propositions opposées.
2. **Le basis.** La prime que paie le côté encombré, lisible directement comme écart entre
   prix marqué et index.

## Objectifs

- Ingérer les métriques de positionnement Kraken Futures comme événements typés sur le bus,
  depuis des endpoints publics, sans compte ni clé.
- Conserver Binance comme source de repli, et rendre les deux lectures visibles côté
  dossier — un écart de funding entre venues est lui-même une information.
- Étendre `_norm_positioning` avec le déséquilibre de liquidations, **sans créer de
  neuvième axe** et sans modifier aucun poids.

## Non-objectifs

- Aucune modification de `WEIGHTS`, ni renormalisation des huit poids existants.
- Aucun nouvel axe, donc aucune modification des trois copies indépendantes de la liste
  d'axes (`scoring.py::WEIGHTS`, `dossier.py::AXIS_KEYS`, `dossier.ts::SCORE_AXES`).
- Aucun changement de comportement pour les symboles que Kraken ne cote pas.

## Architecture

### Nouveau service `collector-kraken-futures`

Même forme clean-architecture que `collector-binance-futures`, dont il reprend
délibérément les patrons plutôt que d'en inventer :

```
domain/         mapper.py (payload Kraken → DerivativesEvent), symbols.py (PF_XBTUSD → BTC)
application/    collector.py (boucle de cycle, compteur d'indisponibilité)
infrastructure/ kraken_client.py (HTTP public, aucune signature)
main.py         DI + boucle
```

Il publie sur le topic **existant** `market.derivatives.events` avec l'événement
**existant** `DerivativesEvent`. Aucun nouveau topic.

### Le champ `venue` est obligatoire

`DerivativesEvent.partition_key()` retourne aujourd'hui `symbol` seul
(`libs/cmi_common/cmi_common/events/market.py:101`). Deux producteurs publiant pour `BTC`
produiraient un dernier-écrit-gagne : la source retenue serait décidée par la latence
réseau, au cycle près, sans que rien ne le signale.

`DerivativesEvent` reçoit donc un champ `venue: str`. Le `FeatureStore` stocke par
`(symbol, venue)` et la lecture applique une préférence explicite : **Kraken s'il est
présent, Binance sinon**. Le patron existe déjà dans le dépôt — `AccountSnapshotEvent`
porte le même champ pour la même raison.

### Champs nouveaux sur `DerivativesEvent`

Tous nullables, comme les champs existants ; un événement partiel est le cas normal.

| Champ | Type | Note |
|---|---|---|
| `venue` | `str` | `"binance"` \| `"kraken"`. Requis, pas de défaut : un producteur qui l'oublie doit échouer à la construction. |
| `liquidations_long_usd_24h` | `float \| None` | |
| `liquidations_short_usd_24h` | `float \| None` | |
| `basis_pct` | `float \| None` | Dérivé de `markPrice`/`indexPrice`. Marqué dérivé dans le docstring, au même titre que `funding_annualized_pct`. |

**Les deux côtés de liquidation sont stockés séparément et ne sont jamais agrégés en
amont.** Un flush de longs et un squeeze de shorts sont des lectures opposées ; les sommer
les annule. L'agrégation, si elle a lieu, appartient au scorer, qui sait quel signe il
cherche.

### Le piège d'unité sur le funding

Le champ existant s'appelle `funding_rate_8h` parce que Binance publie une période de 8 h.
**La période de funding de Kraken n'est pas nécessairement la même.** Y verser le taux
Kraken brut produirait un nombre parfaitement plausible et faux, qui traverserait la
validation Pydantic, le `FeatureStore` et le scorer sans qu'aucun test n'échoue —
exactement la classe de défaut que `CLAUDE.md` documente comme ayant coûté quatorze
occurrences.

**Exigence :** la période de funding réelle de Kraken est vérifiée contre la documentation
Kraken au moment de l'implémentation, écrite comme constante nommée dans
`domain/mapper.py` avec la source en commentaire, et la conversion vers l'équivalent 8 h
est explicite. Elle n'est ni déduite, ni supposée égale à celle de Binance.

Même exigence de vérification pour les chemins d'endpoints : les noms cités en cadrage
(`/api/v3/tickers`, `/api/v3/orderbook`, famille `analytics`) sont donnés de mémoire et
doivent être confirmés contre la documentation live avant implémentation.

### Couverture

Kraken liste nettement moins de perpétuels que Binance. Pour la majorité des symboles
scorés, la lecture Kraken sera absente et Binance prendra le relais. **C'est le cas
nominal, pas un mode dégradé** — l'absence est déjà exclue du dénominateur, jamais
pénalisée.

## Scoring

### Aucun neuvième axe

Les nouvelles métriques deviennent des sous-signaux de `positioning`.
`_norm_positioning` (`decision-engine/app/scoring.py:220`) est déjà un `_mean_present`
sur trois sous-signaux ; en ajouter un quatrième ne touche ni `WEIGHTS`, ni la
renormalisation, ni les trois copies de la liste d'axes. Un symbole sans données Kraken
garde le comportement d'aujourd'hui, puisque `_mean_present` exclut les sous-signaux
absents.

Le rattachement est sémantiquement juste : l'axe est défini comme « contrarien sur
l'encombrement, confirmatoire sur l'engagement ». Les liquidations sont cet encombrement
purgé de force.

### Liquidations : le déséquilibre, contrarien

Le sous-signal est le déséquilibre normalisé, dans [−1, 1] :

```
imbalance = (long_liq - short_liq) / (long_liq + short_liq)
```

Positif quand ce sont les longs qui sautent. Passé en sigmoïde, **il fait monter le
score** : le côté encombré vient d'être vidé.

Le dénominateur nul (aucune liquidation sur la fenêtre) rend le sous-signal **absent**,
pas neutre. Zéro liquidation est une mesure, mais elle ne dit rien sur le *côté* — et
`0.5` serait une lecture confiante là où il n'y en a pas.

**Cette direction encode une thèse, et c'est un choix assumé.** « Les longs ont été
liquidés donc l'entrée est meilleure » est du contrarien qui achète les cascades. Le
risque assumé est d'attraper un couteau qui tombe quand la cascade n'est que le début du
mouvement. La direction est cohérente avec le reste de l'axe (un funding positif fait
baisser le score) et c'est cette cohérence qui la justifie, pas une neutralité qu'elle
n'a pas.

### Basis : collecté, non scoré

`basis_pct` entre dans l'événement, est stocké, et s'affiche dans le dossier `/market`.
**Il n'entre pas dans `_norm_positioning`.**

La raison est structurelle : sur un perpétuel, le funding *est* le mécanisme qui colle le
prix marqué à l'index. Le basis d'un perp est donc petit, bruité, et porte quasiment la
même information que le funding — métrique riche sur un contrat à échéance fixe, presque
vide sur un perp. Le scorer, qui fait une moyenne, donnerait à deux sous-signaux corrélés
un poids mécaniquement double sur le même phénomène.

Le collecter coûte zéro (il vient du même appel que le funding) et accumule de la donnée
observable. La décision de le scorer, si elle vient, se prendra sur des faits.

**Note pour toute évolution ultérieure :** si `basis_pct` est un jour ajouté à
`_norm_positioning`, il ne doit pas être traité comme un signal indépendant du funding.

## Défaillance

- Tout fetch échoué produit `None`, **jamais `0`**, et incrémente le compteur `UNMEASURED`
  de `cmi_common.observability`.
- Un compteur de cycles indisponibles consécutifs sur le modèle de
  `UNAVAILABLE_CYCLES_BEFORE_WARNING` du collector Binance. Il compte **l'indisponibilité**
  (HTTP 4xx/5xx, timeout, rate limit), pas les réponses vides : le docstring du collector
  Binance documente cette distinction comme ayant été ratée une première fois, le garde-fou
  écrit contre la disparition silencieuse d'un axe étant précisément incapable de la voir.
- Republication à chaque cycle. Le `FeatureStore` expire à 900 s alors que le funding évolue
  bien plus lentement ; ne republier qu'au changement laisserait l'axe disparaître entre
  deux mouvements.

## Tests

**Mapper.** Payload Kraken réel en fixture → `DerivativesEvent`. Couvre en particulier la
conversion de période de funding : c'est le point où une erreur d'unité produit un nombre
plausible.

**Préférence de venue.** Kraken présent → Kraken l'emporte. Kraken absent → Binance.
Les deux sens.

**Déséquilibre de liquidations.** Signe correct dans les deux directions ; dénominateur nul
→ sous-signal absent, et non `0.5`.

**Test de non-régression, le plus important.** Un symbole avec des données Binance
uniquement doit produire un `opportunity_score` **identique** à celui d'aujourd'hui. C'est
lui qui prouve qu'aucune repondération silencieuse de l'univers existant n'a eu lieu.

**Parité de contrat.** Le dossier `/market` expose `basis_pct` et les deux lectures de
venue : le test de parité existant (`tests/test_read_contract.py`, adossé au manifeste
`api-gateway/app/read_contract.py`) doit être étendu, sinon le champ n'apparaîtra jamais
côté frontend sans qu'aucun test n'échoue.

## Suites

- **Étage rapide** (spec séparée) : carnet, profondeur, CVD, slippage attendu, en filtre
  d'exécution dans `trading-engine`. Mode fantôme d'abord — calculer le verdict, le logger,
  ne pas agir — pour le valider sur de vrais signaux sans engager d'argent.
- Décider, sur données observées, si `basis_pct` mérite d'entrer dans le scoring.
