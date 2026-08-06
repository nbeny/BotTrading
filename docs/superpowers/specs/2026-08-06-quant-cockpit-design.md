# Quant Cockpit — vision et vague 1 (bandeau régime, inspecteur de décision, journal)

**Date :** 2026-08-06
**Statut :** validé, prêt à planifier
**Services touchés (vague 1) :** `api-gateway`, `frontend`
**Relation aux specs antérieures :** s'appuie sur le patron de drawer URL-adressable de
`2026-08-01-market-page-drawer-design.md`, étend le journal de
`2026-07-26-counterfactual-journal-design.md`, complète la calibration de
`2026-08-04-decision-valve-calibration-design.md`, et respecte le manifeste de contrat de
`2026-07-25-read-plane-contract-parity-design.md`. L'« étage rapide » (carnet d'ordres,
microstructure) reste celui identifié par `2026-08-06-kraken-futures-positioning-design.md`
et n'est pas couvert ici.

## Périmètre et décisions de cadrage

Le point de départ est un prompt produit décrivant le cockpit d'un fonds quant idéalisé en
douze zones (« NASA Mission Control + Bloomberg Terminal »). Une partie de ces zones n'a
aucun backend dans CMI. Cinq décisions de cadrage, prises en session :

1. **Mix vision + évolution.** La spec documente la vision complète (partie I) mais
   l'exécution ne construit que ce que le backend peut nourrir (partie II). Pas de refonte
   du shell, pas de nouvelle page « cockpit » qui dupliquerait `/command`.
2. **Opérateur unique.** Les « modes par rôle » (Trader / Risk / Researcher / Executive) du
   prompt ne sont pas construits : un seul opérateur réel. Les rôles sont des zones du même
   terminal. Les panneaux restent modulaires pour qu'un passage aux presets par rôle soit un
   chantier de composition, pas de réécriture.
3. **Vague 1 = explicabilité, régime, post-trade.** Choisies parce qu'elles répondent au
   problème opérationnel du moment : bot en `dry_run`, seuil de risque encore à 101, goulot
   mesuré au budget Sonnet. Le travail quotidien est de calibrer la vanne et de comprendre
   pourquoi les décisions passent ou non.
4. **Backend léger.** Uniquement des endpoints de lecture dans api-gateway qui agrègent des
   données déjà collectées. Pas de nouveau collecteur, pas de modèle. Le régime est un
   moteur de règles transparent, pas un classificateur : un modèle non validé afficherait
   une probabilité confiante non mesurée — le défaut exact que ce projet a payé 25 fois.
   Le contrat d'interface rend le choix réversible (§ RegimeStrip).
5. **Placement global.** Le régime devient un bandeau permanent du shell, l'explicabilité un
   drawer global URL-adressable, le journal une page dédiée.

## Problème

Le terminal actuel montre *ce qui se passe* (flux, funnel, pipeline) mais répond mal à
trois questions qu'un opérateur se pose chaque jour :

- **Dans quel régime de marché sommes-nous ?** La lecture market-wide existe dans le
  scoring mais n'est exposée nulle part ; le funding, l'OI et la dominance sont collectés
  mais dispersés.
- **Pourquoi cette décision ?** Le breakdown par axe existe dans
  `Decision.payload["meta"]["breakdown"]`, les rejets dans `PipelineRejection`, la trace
  dans `/trace/{cid}` — mais il faut trois écrans et une requête SQL pour reconstituer
  l'histoire d'une décision.
- **L'edge fonctionne-t-il ?** Le journal contrefactuel juge les décisions mais n'expose
  qu'un résumé (`/systems/journal/summary`). Choisir le seuil reste un acte de foi assisté
  par `pick_threshold.py` en ligne de commande.

## Objectifs

- Un bandeau régime permanent, lisible en une seconde depuis n'importe quelle page, dont
  chaque terme est cliquable jusqu'à la règle et aux valeurs brutes.
- Un inspecteur de décision unique, ouvrable depuis tout endroit où un id de décision
  apparaît, qui raconte la décision de bout en bout : axes, verdicts d'étage, timeline,
  résultat contrefactuel.
- Une page `/journal` qui transforme le journal contrefactuel en outil de calibration :
  table des décisions jugées, simulation de seuil, attribution par axe.
- Quatre endpoints de lecture dans api-gateway, ajoutés au manifeste de contrat et au
  mock BFF, sans nouvelle écriture ni nouveau service.

## Non-objectifs

- Aucun classificateur de régime (HMM, probabilités bull/bear apprises) — documenté en
  vision, vague 3.
- Aucune métrique de qualité d'exécution (slippage, fill ratio) — nécessite de vrais fills
  et du travail trading-engine ; vague 2.
- Aucun mode par rôle, aucun workspace configurable, aucune refonte du design system.
- Aucune modification de `WEIGHTS`, du scoring, ou des trois copies de la liste d'axes
  (`scoring.py::WEIGHTS`, `dossier.py::AXIS_KEYS`, `dossier.ts::SCORE_AXES`).
- Pas de microstructure (order book) : c'est l'étage rapide de la spec positionnement
  Kraken Futures.

---

## Partie I — Vision : les douze zones du prompt face à CMI

| # | Zone du prompt | État CMI | Vague |
|---|---|---|---|
| 1 | Global Market Command Center (régime) | 🟡 lecture sentiment market-wide dans le scoring ; funding/OI/dominance collectés mais non agrégés | **1** (règles) → 3 (classificateur) |
| 2 | Alpha Control Center | ❌ pas de registre d'alphas ; l'analogue le plus proche est la liste des axes et leurs poids | 3 |
| 3 | Signal Ensemble Brain | ✅ huit axes renormalisés + confidence (dossier `/market`) | 1 (réutilisé par l'inspecteur) |
| 4 | AI Decision Explainability | ✅ breakdown, DecisionJournal, `/trace/{cid}`, `PipelineRejection` — dispersés | **1** |
| 5 | Portfolio Command | ✅ `/portfolio`, Redis `trading:positions*` | fait |
| 6 | Risk War Room | ✅ `/risk` + kill switch control-api ; manque VaR/stress | 2 (au passage en live) |
| 7 | Execution Intelligence | ❌ `execution.events` existe, aucune métrique de qualité calculée | 2 (exige de vrais fills) |
| 8 | Market Microstructure | ❌ aucune donnée carnet | 3 (étage rapide, spec séparée) |
| 9 | Data Quality Monitor | 🟡 `/systems` couvre volumes par étage ; manque score par source | 2 |
| 10 | Research Lab (backtests, notebooks) | ❌ | 3 |
| 11 | Autonomous Research Agent | ❌ | 3 |
| 12 | Post-Trade Analytics | 🟡 journal contrefactuel + `/systems/journal/summary` | **1** |

**Vague 1 (cette spec)** : zones 1 (règles), 4, 12 — les trois qui servent la calibration
en `dry_run`.
**Vague 2 (live-readiness)** : zones 6, 7, 9 — déclenchée par le passage en `demo`/`live`,
quand il y aura de vrais fills à mesurer.
**Vague 3 (recherche)** : zones 2, 8, 10, 11 et le classificateur de régime — chacune est
un projet de données à part entière, pas un chantier frontend.

**Extensions documentées, non construites :** presets de layout par rôle (composition des
mêmes panneaux — possible sans réécriture si la modularité est maintenue) ; workspaces
drag-and-drop.

**Principes de design du cockpit**, valables pour toute vague :

- **Inconnu = `null` = « — » ; un zéro mesuré s'écrit en toutes lettres.** La règle
  existante du graphe pipeline devient la règle de tout le cockpit.
- **Contrat d'abord.** Chaque panneau consomme un contrat TypeScript figé dans le manifeste
  de parité ; améliorer le producteur (règles → modèle) ne touche jamais le consommateur.
- **Drill-down partout.** Tout chiffre affiché doit pouvoir répondre à « pourquoi ? » en un
  clic : règle, valeurs brutes, fraîcheur.
- **Densité bornée.** Tables à hauteur fixe avec scroll interne (leçon `/market` :
  jamais `autoHeight`), drawers plutôt que nouvelles pages, pas de flux live dupliqué hors
  `/command`.

---

## Partie II — Exécution vague 1

### RegimeStrip — bandeau régime global

Un bandeau monospace permanent monté dans le layout `(app)`, au-dessus du contenu de page :

```
REGIME: ACCUMULATION ▲ | conf 72% | funding +0.018% crowded-long | OI 24h +6.2% | sent.mkt +0.31 | BTC.D 54.1% | [dry_run] [kill:off]
```

**Moteur de règles, pas de modèle.** Cinq drivers, chacun votant `bullish` / `bearish` /
`neutral` / absent selon des seuils lisibles :

| Driver | Source (déjà collectée) | Exemple de règle |
|---|---|---|
| `funding` | agrégat funding Binance (`features:*` Redis ; Kraken Futures quand le collecteur existera) | médiane > +0.0001 (fraction/8h, ≈2× la médiane mesurée) → crowded-long (contrarien : bearish) |
| `oi_delta` | delta OI 24h agrégé | hausse + prix en hausse → bullish |
| `market_sentiment` | lecture market-wide [-1, 1] du pipeline sentiment | > +0,2 → bullish |
| `btc_dominance` | dérivée de `prices.market_cap_usd` sur l'univers suivi (~200 tokens) — approximation nommée dans `detail` | dérive 7 j > +0.5 pt → risk-off des alts |
| `breadth` | part des tokens suivis à `market_trend` positif | > 60 % → bullish |

Le régime est l'agrégat des votes (majorité pondérée simple) ; `confidence` est la part de
poids des drivers effectivement mesurés — le même principe que la confidence du scoring.
**Si moins de trois drivers sont présents, `regime` est `null`** et le bandeau affiche
`REGIME: —` : un régime deviné vaut moins que pas de régime.

**Contrat (figé, nullable partout) :**

```ts
interface MarketRegime {
  regime: 'RISK_ON' | 'ACCUMULATION' | 'NEUTRAL' | 'DISTRIBUTION' | 'RISK_OFF' | null;
  confidence: number | null;          // part de poids mesurée, 0–1
  drivers: RegimeDriver[];
  computed_at: string;                // ISO 8601 UTC
}
interface RegimeDriver {
  key: 'funding' | 'oi_delta' | 'market_sentiment' | 'btc_dominance' | 'breadth';
  value: number | null;               // valeur brute
  state: 'bullish' | 'bearish' | 'neutral' | null;  // null = non mesuré
  detail: string;                     // règle appliquée, avec seuils, auditable
  as_of: string | null;               // fraîcheur de la mesure
}
```

Chaque terme du bandeau ouvre un popover : règle (`detail`), valeur brute, fraîcheur. Les
badges `[dry_run] [kill:off]` réutilisent le status trading déjà consommé par `KpiTicker`.
Le jour où un classificateur validé existe, il remplit le même contrat derrière le même
endpoint — le frontend ne change pas. Poll 30 s, aligné sur le cache serveur.

### Decision Inspector — drawer global d'explicabilité

Promotion du `DecisionTraceDrawer` en **drawer global URL-adressable** (`?decision=<id>`),
même patron que `?token=` sur `/market`, monté dans le layout `(app)`. Ouvrable depuis :
`LiveEventStream`, `AiDecisionFeed`, le drawer `/market`, les opportunités `/trading`, les
lignes `/journal`.

Contenu, de haut en bas :

1. **En-tête** — symbole, action, `opportunity_score` brut 0–100, confidence. L'inspecteur
   n'affiche que l'échelle brute ; la double échelle 0–1 / 0–100 (mappers `map_token` /
   `map_decision`) ne doit jamais y apparaître.
2. **Waterfall des axes** — depuis `Decision.payload["meta"]["breakdown"]`, rendu
   dynamiquement depuis `SCORE_AXES` (huit aujourd'hui — pas de constante « 7 » ou « 8 »
   en dur côté composant). Un axe absent s'affiche `— (absent, exclu du score)`, jamais 0.
3. **Verdict par étage** — triage Haiku (ses quatre facteurs `momentum`/`volume`/
   `sentiment`/`liquidity`, étiquetés « triage » car namespace disjoint des axes), Sonnet,
   risk. La raison de rejet vient de `PipelineRejection` avec `stage` normalisé
   (`persister.stage_for` produit la source d'événement, pas un id de
   `systems_pipeline::STAGE_SPECS`).
4. **Timeline des événements** — par correlation id quand il existe ; sinon repli
   `(symbol, fenêtre temporelle)` avec mention explicite « lien par symbole/temps » —
   ~95 % du flux n'a pas de lineage par id et l'inspecteur le dit plutôt que de le masquer.
5. **Résultat contrefactuel** — si le journal a jugé la décision : résultat simulé,
   horizon, verdict.

### Page `/journal` — post-trade et calibration

Nouvelle entrée de navigation. Trois panneaux :

1. **Décisions jugées** — table bornée (hauteur fixe, scroll interne) : date, symbole,
   action, score, seuil au moment de la décision, passé/rejeté, résultat simulé à
   l'horizon. Résultat pendant : « — ». Clic sur une ligne → Decision Inspector.
2. **Calibration de seuil** — slider de seuil simulé : rejoue le journal au seuil choisi et
   affiche nombre de trades, win-rate, PnL simulé, à côté des mêmes chiffres pour le seuil
   actuel. Interface du travail déjà entamé par `pick_threshold.py`, avec les mêmes
   définitions.
3. **Attribution par axe** — sur une fenêtre glissante (30 j par défaut) : corrélation
   simple entre chaque **facteur de triage Haiku** (`momentum`/`volume`/`sentiment`/`liquidity`, seuls présents pour toutes les décisions du journal) et le résultat simulé ; l'attribution par les 8 axes exige des décisions passées et viendra quand le seuil laissera passer un échantillon. Sous
   `n < 20` décisions jugées sur la fenêtre, le panneau affiche « — (échantillon
   insuffisant) ».

### Backend — quatre endpoints de lecture (api-gateway)

Même style que `dossier.py` : module d'assemblage pur + route fine, cache TTL 30 s,
réponses ajoutées au manifeste `read_contract.py`, routes miroir dans le mock BFF
(`NEXT_PUBLIC_USE_MOCK=1`). api-gateway gagne un accès Redis **en lecture seule** (`features:*`, `market:regime`) — précédent existant chez risk-engine et trading-engine.

| Endpoint | Rôle |
|---|---|
| `GET /market/regime` | assemble les cinq drivers et applique les règles ; les seuils vivent dans api-gateway (jamais importés de decision-engine) et sont restitués dans `detail` |
| `GET /decisions/{id}/explain` | agrégat pour l'inspecteur : décision + breakdown + verdicts d'étage + rejets + timeline + résultat journal — un appel au lieu de quatre |
| `GET /systems/journal/decisions` | table paginée/filtrable des décisions jugées |
| `GET /systems/journal/calibration?threshold=` · `GET /systems/journal/attribution?window=` | les deux panneaux analytiques |

Aucune écriture, aucun nouveau collecteur, aucun modèle. Un échec de requête amont produit
un champ `null` (donc « — ») ou un statut stale — jamais un zéro confiant.

## Erreurs et cas limites

- **Décision sans breakdown** (antérieure au scoring v2) : waterfall remplacé par
  « breakdown indisponible (décision pré-v2) », le reste de l'inspecteur fonctionne.
- **Id de décision introuvable** : le drawer affiche l'erreur et un lien pour fermer ;
  l'URL reste partageable sans casser la page hôte.
- **Journal vide ou jeune** : les trois panneaux de `/journal` affichent leur état vide en
  toutes lettres (« aucune décision jugée sur la fenêtre »), le slider de calibration est
  désactivé sous `n < 20`.
- **Drivers de régime absents** (collecteur en panne, cache expiré) : driver `state: null`
  rendu « — » ; sous trois drivers mesurés, régime `null`.
- **Propagation du null** : chaque champ des quatre contrats est nullable et le rendu
  distingue systématiquement `null` (« — ») de zéro mesuré (en toutes lettres). C'est le
  défaut récurrent n°1 du projet ; chaque PR de la vague passe une revue dédiée à ce point.

## Tests

- **Parité de contrat offline** : les quatre réponses ajoutées au manifeste
  `read_contract.py`, vérifiées par `tests/test_read_contract.py` contre les types TS.
- **Moteur de règles régime** : tests unitaires purs — driver absent, tous absents, votes
  contradictoires, seuils limites, `confidence` avec poids partiel.
- **Harness live** : `scripts/verify_read_live.py` étendu aux quatre endpoints.
- **Frontend** : le mock BFF sert les mêmes shapes (y compris cas `null`) ; les composants
  se développent en mode mock sans backend, règle existante du projet.

## Risques

- **Les règles de régime sont des opinions.** Assumé : elles sont affichées, auditables et
  bon marché à changer ; leur historique (journalisé via le journal) est la donnée de
  validation du futur classificateur.
- **`/decisions/{id}/explain` traverse quatre tables** ; le cache 30 s et la pagination du
  journal bornent le coût, à mesurer au plan si la table `PipelineRejection` grossit.
- **Quatrième copie de la liste d'axes** : interdite — l'inspecteur importe `SCORE_AXES`
  de `dossier.ts`. Le test de parité échoue si le breakdown serveur diverge du type TS.
