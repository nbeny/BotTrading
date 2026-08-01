# Refonte de `/market` — tableau borné + drawer « dossier token »

**Date :** 2026-08-01
**Statut :** design validé, prêt pour plan d'implémentation

## Problème

Sur `https://crypto.nbeny.fr/market`, tout ce qui suit le tableau des tokens passe inaperçu.

La cause est mécanique, pas esthétique. `TokensTable` monte un `DataGrid` en `autoHeight`
sans pagination (`frontend/src/components/market/TokensTable.tsx:144`), et
`GET /market/tokens` (`services/api-gateway/app/read_api.py:350`) renvoie une ligne par
symbole ayant un prix, sans `limit`. À 56 px la ligne et ~124 symboles suivis, le tableau
seul mesure plusieurs milliers de pixels. Le graphique de prix, les décisions des workers,
les news et le flux live sont poussés six à sept écrans plus bas.

Le corollaire est ce qui compte : **hors écran = inexistant**. Réduire le tableau ne suffit
pas si le contenu spécifique à un token reste dispersé sous le pli.

## Ce que la page doit servir

Les quatre usages ont été confirmés comme également importants :

1. Balayer le marché — repérer les tokens qui bougent ou scorent haut.
2. Étudier un token — prix, sentiment, ce que les workers en ont dit, news le concernant.
3. Surveiller le raisonnement de l'IA — décisions Haiku/Sonnet.
4. Suivre l'actualité crypto générale.

Une page ne peut pas donner la première place à quatre choses. La résolution retenue :
**la page sert le balayage (1, 3, 4 en version globale), le drawer sert l'étude d'un token
(2, plus les versions filtrées de 3 et 4).**

## Architecture retenue

### Corps de page

```
┌─ Intelligence de marché ─────────────────────────────────────┐
├──────────────────────────────────────────────────────────────┤
│ Tokens surveillés      [🔍 recherche]  [Trier par : Score ▾] │
│ BTC  Bitcoin   $67 210  +2.4%  1.2B  87  🔥                  │
│ … 15 lignes …                                                │
│                                    15 sur 124 · voir tout →  │
├───────────────────────────┬──────────────────────────────────┤
│ Décisions IA récentes     │ News                             │
│ (tout le marché, ↕)       │ (tout le marché, ↕)              │
└───────────────────────────┴──────────────────────────────────┘
```

- `TokensTable` perd `autoHeight`. Hauteur fixe correspondant à 15 lignes.
- Tri par défaut : `opportunity_score` décroissant. Sélecteur de tri exposant aussi
  variation 24 h, volume, liquidité.
- Recherche client sur `symbol` et `name` — la liste complète est déjà en mémoire, aucun
  aller-retour réseau n'est nécessaire.
- « Voir tout » bascule vers la liste complète dans **la même hauteur**, en scroll interne.
  La hauteur de la page ne dépend jamais du nombre de tokens.
- Rangée du bas : deux colonnes plafonnées (~420 px) à scroll interne, alimentées par
  `/market/decisions` et `/market/news` sans filtre de symbole.

### Drawer « dossier token »

`Drawer anchor="right"`, sur le modèle de `DecisionTraceDrawer`
(`frontend/src/components/command/DecisionTraceDrawer.tsx:12`) : fond vitré
`rgba(8,11,20,0.92)` + `backdropFilter: blur(16px)`.

- Largeur `{ xs: '100%', sm: 640 }`, bouton d'élargissement à ~1040 px.
- Ouverture au clic sur une ligne du tableau.
- **La sélection vit dans l'URL** (`/market?token=SOL`), pas seulement dans un `useState` :
  le dossier devient partageable et le bouton retour du navigateur le referme.

Sept sections, dans cet ordre :

| # | Section | Source |
|---|---|---|
| 1 | En-tête — symbole, nom, prix, variation 24 h, badges *trending* / score | `MarketToken` déjà en mémoire, réaffiché immédiatement (pas d'attente réseau) |
| 2 | Prix — graphique 1d/7d/30d + volume, liquidité, market cap, sentiment | `GET /market/tokens/{symbol}/prices` (existant) |
| 3 | Décomposition du score — les 7 axes | `Decision.payload["meta"]["breakdown"]` |
| 4 | Verdict du pipeline — étage atteint, étage de mort, motif | `DecisionJournal` + `PipelineRejection` |
| 5 | Décisions des workers sur ce symbole | `Decision` filtré par `symbol` |
| 6 | News & social mentionnant ce symbole | `RawContent.symbols` |
| 7 | Exposition — positions et trades sur ce symbole | `_portfolio_basis` / `Trade` filtrés par `symbol` |

Les sections 3, 4 et 7 n'existent nulle part dans le frontend aujourd'hui.

Les justifications des workers (section 5) sont tronquées à 3 lignes avec expansion au clic —
sans quoi une seule carte remplit le drawer.

#### Section 3 — d'où vient la décomposition

**Attention au faux ami.** `DecisionJournal.factors` **n'est pas** la décomposition v2 : c'est le
triage Haiku à quatre facteurs — `momentum`, `volume`, `sentiment`, `liquidity`
(`ai-worker-haiku/app/scorer.py:178`), recopié tel quel dans le journal par
`ai-worker-sonnet/app/journal.py:54`. Lire les 7 axes dedans renverrait un dict vide en
permanence : sept tirets à l'écran, pour toujours, indiscernables d'un vrai « rien mesuré ».

La décomposition v2 vit dans `Decision.payload["meta"]["breakdown"]` :
`decision-engine/app/engine.py:190` publie `meta={"breakdown": result.breakdown}`, et le
persister sérialise l'événement entier dans la colonne `payload`
(`api-gateway/app/persister.py:257`). Ses clés sont les axes de
`services/decision-engine/app/scoring.py:37` : `volume_growth`, `social_score`, `news_score`,
`market_trend`, `liquidity_score`, `positioning`, `fundamentals`.

Deux corollaires :

- **`dominant_factor` ne figure pas dans le dossier.** `journal.py:22` le calcule dans l'espace
  quatre facteurs de Haiku, donc il nommerait un facteur absent de la liste des sept axes
  affichés juste à côté. Une incohérence garantie à l'écran, pour un champ purement décoratif.
- **Un `breakdown` vide n'est pas un score de zéro.** Sous `_MIN_PRESENT_WEIGHT`,
  `scoring.py:288` renvoie `ScoreResult(0, 0.0, {})` — « trop peu de preuves pour renormaliser
  honnêtement ». Rendre « 0/100 » dans ce cas serait précisément le défaut que ce panneau
  existe pour empêcher. La réponse porte donc un booléen `insufficient_evidence`, et `value`
  vaut `null` — pas `0` — quand il est vrai.

**Un axe absent est une clé absente du dict, pas une clé à `0.0`.** Le scoring v2 renormalise
sur le poids présent : un axe non mesuré est *exclu*, pas noté zéro. L'affichage doit refléter
exactement ça :

- axe présent → barre + valeur numérique ;
- axe absent → `—` grisé, barre vide, et mention explicite « exclu du calcul » ;
- jamais de `0` là où la mesure manque.

Le drawer affiche également le nombre d'axes présents (« 6 axes sur 7 ») et la confiance,
qui est la part de poids adossée à des preuves spécifiques au symbole.

C'est la contrainte la plus importante de cette spec. Quatorze défauts de cette forme exacte
ont déjà été trouvés dans ce projet, aucun n'ayant levé d'erreur ni fait échouer un test :
une valeur non mesurée qui fuit en lecture confiante déplace toujours le score dans la
direction de cette lecture.

#### Section 4 — vocabulaire

Deux compteurs voisins ne doivent pas être confondus dans l'UI :

- `factors_present` (0–4) est le compteur de **triage Haiku** — variation, volume, sentiment,
  liquidité (`services/ai-worker-haiku/app/scorer.py:93`).
- Le nombre d'axes présents (0–7) est celui du **decision-engine**.

Le drawer n'affiche que le second, sous le libellé « axes ». Le premier reste interne.

## Backend

### Nouvel endpoint

```
GET /market/tokens/{symbol}/dossier
```

Monté sur `read_api.router`, donc **authentifié par construction** :
`services/api-gateway/app/main.py:127` inclut ce routeur avec
`dependencies=[Depends(require_principal)]`. Aucun travail d'authentification à faire, et
aucune exception à créer.

Côté client, `frontend/src/lib/api/client.ts` injecte déjà `Authorization: Bearer <token>`
depuis le localStorage et émet `cmi:unauthorized` sur 401. Le drawer passe par le client
`api` existant (`API_BASE` → `/api/gateway`).

Le graphique de prix **reste sur son endpoint séparé** `/market/tokens/{symbol}/prices` :
il dépend du sélecteur 1d/7d/30d et se recharge indépendamment du reste du dossier.

Forme de la réponse :

```jsonc
{
  "symbol": "SOL",
  "score": {
    "value": 84,              // int | null — null si aucune décision, ou preuves insuffisantes
    "confidence": 0.62,       // float | null
    "axes": {                 // uniquement les axes MESURÉS
      "volume_growth": 0.81,
      "social_score": 0.74,
      "news_score": 0.60,
      "market_trend": 0.88,
      "liquidity_score": 0.70,
      "positioning": 0.93
      // "fundamentals" absent — non mesuré
    },
    "axes_total": 7,
    // true = une décision existe mais le poids présent était sous le seuil de
    // renormalisation. Distinct de « aucune décision », où il vaut false.
    "insufficient_evidence": false,
    "computed_at": "2026-08-01T09:12:00Z"   // str | null
  },
  "pipeline": {
    // Vocabulaire de systems_pipeline.py::STAGE_SPECS : collect, sentiment,
    // triage, senior, decision, risk, execute.
    "reached_stage": "risk",       // str | null
    "blocked_at": "risk",          // str | null — null si aucun blocage OBSERVÉ
    "block_reason": "score_below_threshold",  // str | null
    // bool | null. `null` quand la seule trace est un PipelineRejection : sans
    // ligne de journal, on ne sait pas si Haiku avait escaladé en parallèle.
    "escalated": true,
    "sonnet_called": true,
    "sonnet_validated": false,     // bool | null
    "last_event_at": "2026-08-01T09:12:00Z"  // str | null
  },
  "decisions": [ /* WorkerDecision[] — même forme que /market/decisions */ ],
  "content":   [ /* NewsItem[] — même forme que /market/news, news + social */ ],
  "exposure": {
    "open_positions": [ /* même forme que /portfolio/positions, filtré sur le symbole */ ],
    "recent_trades":  [ /* même forme que /portfolio/trades,    filtré sur le symbole */ ]
  }
}
```

Règles de nullité, non négociables :

- Une section sans donnée renvoie ses champs à `null` (ou une liste vide pour les listes),
  **jamais** des zéros de remplissage.
- `axes` ne contient que les axes mesurés. L'absence d'une clé est l'information.
- Une requête qui échoue doit propager l'erreur, pas dégrader vers un dossier vide qui se
  lirait comme « ce token n'a rien ».

### Requêtes assemblées

| Champ | Requête |
|---|---|
| `score` | dernière `Decision` du symbole — `opportunity_score`, `confidence`, `created_at`, et `payload["meta"]["breakdown"]` pour les axes |
| `pipeline.*` sauf `blocked_at` | dernier `DecisionJournal` du symbole (`escalated`, `sonnet_*`, `skip_reason`, `risk_verdict`, `execution_event_id`) |
| `pipeline.blocked_at`, `block_reason` | dernier `PipelineRejection` du symbole (`stage`, `reason`), en repli quand aucune ligne de journal n'existe |

**Deux vocabulaires d'étages coexistent, et il faut les réconcilier.**
`PipelineRejection.stage` vaut `decision_engine` ou `risk_engine` — `stage_for` mappe la *source*
de l'événement (`persister.py:59`) — tandis que le reste de la plateforme, y compris le graphe du
Command Center, utilise les ids de `systems_pipeline.py::STAGE_SPECS` : `decision`, `risk`, etc.
Le dossier normalise vers les ids `STAGE_SPECS`, qui sont ceux que le frontend sait libeller. Une
source non mappée passe telle quelle, pour la même raison que `stage_for` le fait déjà : un
rejeteur inattendu doit rester visible plutôt que d'être silencieusement renommé.
| `decisions` | `Decision` where `symbol = :sym` order by `created_at` desc, limite 20 |
| `content` | `RawContent` where `symbols @> [:sym]` order by **`fetched_at`** desc, limite 20 — même prédicat *et même tri* que `/data/content` (`read_api.py:463`) |

**Le tri est `fetched_at`, pas `published_at`.** Sur les sept sources de `collector-social`, seules
`lens`, `neynar` et `youtube` renseignent `published_at` ; `bluesky`, `reddit`, `mastodon` et
`fourchan` le laissent à `None`. Un `ORDER BY published_at DESC NULLS LAST` reléguerait donc la
majorité du social en fin de tri quelle que soit sa fraîcheur, et la section « News & social »
n'afficherait que des news — sans erreur ni test rouge. `data_content` trie déjà par `fetched_at`
pour cette raison exacte.
| `exposure` | `_portfolio_basis(session)` (`read_api.py:983`) puis filtre par symbole, plus `Trade` where `symbol = :sym` order by `created_at` desc, limite 20 |

Aucune migration, aucune donnée nouvelle à collecter : tout existe en base et n'est
simplement exposé nulle part.

Il n'y a pas de table `positions` : une position ouverte est dérivée des `Trade` par
`_open_positions`, et `map_position` convertit une *fraction* de taille en quantité à l'aide
du capital de référence. Le dossier doit donc **réutiliser `_portfolio_basis`** et filtrer son
résultat, jamais re-dériver les positions localement — sinon le drawer et `/portfolio`
affichent des tailles différentes pour la même position.

### Ce qu'on ne fait pas

`/market/decisions` et `/market/news` ne gagnent **pas** de paramètre `?symbol=`. Le filtrage
par symbole vit dans le dossier, en un seul aller-retour. Ajouter des filtres aux endpoints
globaux créerait deux chemins pour la même donnée.

## Contrat et tests

Ce repo verrouille les formes de réponse par manifeste. Le nouvel endpoint doit y entrer :

- entrée `market/token/dossier` dans `services/api-gateway/app/read_contract.py` ;
- test de parité offline `services/api-gateway/tests/test_read_contract.py` étendu ;
- harnais live `services/api-gateway/scripts/verify_read_live.py` mis à jour.

Tests spécifiques à écrire :

1. **Axe non mesuré** — une `Decision` dont `payload["meta"]["breakdown"]` omet `fundamentals`
   produit une réponse où `axes` omet la clé. Assertion explicite que la valeur n'est ni `0`,
   ni `0.0`, ni `null` *dans* le dict : la clé est absente.
2. **Symbole sans historique** — un symbole avec un prix mais aucune `Decision` renvoie
   `score.value = null`, `axes = {}`, `insufficient_evidence = false`, listes vides, et
   **200**, pas 404.
2 bis. **Preuves insuffisantes** — un `breakdown` vide sur une décision existante donne
   `insufficient_evidence = true` et `value = null`, jamais `value = 0`.
3. **Authentification** — l'endpoint répond 401 sans jeton. Test de régression contre un
   futur montage hors du routeur authentifié.
4. **Rendu frontend** — un axe absent rend `—`, jamais `0`.

## Frontend — inventaire des changements

### Créés

| Fichier | Rôle |
|---|---|
| `frontend/src/components/market/TokenDossierDrawer.tsx` | conteneur du drawer, orchestre les sections |
| `frontend/src/components/market/ScoreBreakdown.tsx` | section 3 — les 7 axes, `—` pour un axe absent |
| `frontend/src/components/market/PipelineVerdict.tsx` | section 4 — étage atteint / étage de mort |
| `frontend/src/components/market/TokenExposure.tsx` | section 7 — positions et trades |
| `frontend/src/app/api/mock/market/tokens/[symbol]/dossier/route.ts` | parité mock |

### Modifiés

| Fichier | Changement |
|---|---|
| `frontend/src/app/(app)/market/page.tsx` | nouvelle disposition, sélection portée par l'URL, ouverture du drawer |
| `frontend/src/components/market/TokensTable.tsx` | `autoHeight` retiré, hauteur fixe, recherche, sélecteur de tri, bascule « voir tout » |
| `frontend/src/components/market/TokenPricePanel.tsx` | déplacé dans le drawer, en-tête allégé (le drawer porte déjà le titre) |
| `frontend/src/components/market/WorkerDecisionsPanel.tsx` | justifications tronquées à 3 lignes + expansion ; réutilisé en version globale sur la page et filtrée dans le drawer |
| `frontend/src/lib/api/endpoints.ts` | `marketApi.dossier(symbol)` |
| `frontend/src/lib/types/domain.ts` | types `TokenDossier`, `ScoreBreakdown`, `PipelineVerdict` |

### Retiré

`LiveEventStream` disparaît de `/market`. C'est un doublon : `/command` monte déjà
`LiveEventStream`, `AiDecisionFeed` et `MarketHeatPanel`
(`frontend/src/app/(app)/command/page.tsx:10,14,17`). Le flux temps réel est le métier du
Command Center ; `/market` est une surface d'analyse.

## Mock

Le terminal mock reste en parité complète — `cd frontend && npm run dev` doit continuer de
fonctionner sans backend Python.

La route mock du dossier fournit des données plausibles incluant **un axe absent**, pour que
le rendu `—` soit exerçable sans backend. C'est le seul détail du mock qui compte
réellement : c'est le cas que le développement front risque de ne jamais voir autrement.

## Responsive

- `≥ lg` : disposition décrite ci-dessus, drawer à 640 px superposé.
- `md` : rangée du bas empilée en une colonne, drawer toujours à 640 px.
- `xs`/`sm` : drawer plein écran (`width: '100%'`), tableau réduit aux colonnes symbole,
  prix, variation 24 h, score.

## Critères d'acceptation

1. La hauteur de `/market` ne dépend plus du nombre de tokens suivis.
2. À 1080 p, aucun panneau de la page n'est entièrement sous le pli.
3. Un clic sur une ligne ouvre le dossier ; l'URL reflète la sélection ; le retour navigateur
   le referme.
4. Le dossier montre les 7 axes, dont les absents en `—` avec la mention « exclu du calcul »,
   et les axes proviennent de `Decision.payload["meta"]["breakdown"]` — un dossier de token
   réellement scoré affiche des valeurs, pas sept tirets.
5. Le dossier dit à quel étage le signal est mort et pourquoi.
6. Un token sans historique de décision affiche un dossier honnête (`—` partout), pas une
   erreur et pas des zéros.
7. `make lint` et `make test` passent ; le test de parité de contrat couvre le nouvel endpoint.
8. `NEXT_PUBLIC_USE_MOCK=1` reste pleinement fonctionnel sur cette page.
