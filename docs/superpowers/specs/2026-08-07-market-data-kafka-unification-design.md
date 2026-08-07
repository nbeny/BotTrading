# Market data unifiée sur Kafka — persistance et diffusion des topics dérivés, ralliement de collector-kraken

**Date :** 2026-08-07
**Statut :** validé, prêt à planifier
**Services touchés :** `api-gateway` (persister), `websocket-gateway`, `collector-kraken`,
`frontend`, `libs/cmi_common` (événements, topics), `migrations`
**Relation aux specs antérieures :** inverse la décision « pas de Kafka » de
`2026-07-29-market-data-foundation-design.md` (sa justification était « aucun consommateur » —
il y en a désormais un) ; complète la vague 1 de
`2026-08-06-quant-cockpit-design.md` (as_of réels pour les drivers funding/OI) ; respecte
l'« étage rapide » de `2026-08-06-kraken-futures-positioning-design.md` (le carnet reste hors
Kafka, voir Non-objectifs).

## Périmètre et décisions de cadrage

Trois décisions prises en session :

1. **Ordre B puis A.** D'abord persister et diffuser les topics Kafka qui existent déjà sans
   consommateur aval (`market.derivatives.events`, `market.fundamentals.events`,
   `market.developer.events`) ; ensuite rallier `collector-kraken` au patron commun
   (publier les bougies sur Kafka au lieu d'écrire Postgres en direct).
2. **Flux générique ET push ciblé.** Les nouveaux événements apparaissent dans le
   `LiveEventStream` de `/command` (générique), et le `RegimeStrip` invalide sa query à
   l'arrivée d'un événement dérivés (ciblé) — le poll 30 s reste en filet de sécurité.
3. **Hypertables dédiées**, pas l'archive générique `events_signal` : c'est ce qui donne un
   historique requêtable au funding/OI (dérivées, moyennes fenêtrées) et des `as_of` réels.

## Problème

Le principe d'architecture de la plateforme est : collecteurs → Kafka → persister → Postgres,
et websocket-gateway → front. Deux familles de données y échappent :

- **Les topics dérivés existent mais s'évaporent.** `market.derivatives.events`,
  `fundamentals` et `developer` ne sont ni archivés (absents de la liste d'abonnement du
  persister) ni diffusés (absents des 10 topics du websocket-gateway). Leur seule trace est
  le FeatureStore Redis (TTL 900 s) et la copie `meta.features` embarquée dans les analyses.
  Conséquence concrète : le funding qui nourrit la carte régime n'a **aucun historique SQL ni
  horodatage** — la vague 1 a dû afficher « fraîcheur inconnue » sur des drivers mesurés.
- **`collector-kraken` écrit Postgres en direct** (bougies + carnet), par une décision datée
  du 2026-07-29 dont la justification explicite était « routing it through Kafka would add
  plumbing […] **for no consumer** ». Le front en subscription live est ce consommateur.

## Objectifs

- Chaque événement de marché publié sur Kafka est **persisté** (hypertable dédiée) et
  **diffusé** au front (websocket-gateway), sans exception nouvelle.
- Le funding/OI gagne un historique SQL : `as_of` réels pour les drivers du régime
  (suppression du cas « fraîcheur inconnue » pour funding/ΔOI), dérivées calculables.
- `collector-kraken` publie ses bougies comme événements typés ; la table `candles` et son
  lecteur SQL ne changent pas — seul le chemin d'écriture passe par le persister.
- Le `RegimeStrip` réagit en push (invalidation de query sur événement) tout en gardant le
  poll comme repli.

## Non-objectifs

- **Le carnet de profondeur reste en écriture directe Postgres.** Donnée la plus lourde et la
  plus périssable, aucun consommateur temps réel (pas d'UI carnet), et l'étage rapide de la
  spec positionnement la destine à trading-engine au plus près de `send_order` — un détour
  par Kafka ajouterait de la latence là où elle coûte. Si une UI carnet naît, elle aura son
  topic à ce moment-là. (Décision utilisateur : exception acceptée.)
- Aucun changement du FeatureStore, du scoring, de `WEIGHTS`, des trois copies d'axes.
- Aucun nouveau collecteur, aucune nouvelle règle de régime (les dérivées historiques
  nourriront de futures règles, pas celles-ci).
- Pas d'UI graphique de bougies live sur `/market` dans ce chantier — la plomberie rend la
  chose possible, l'UI viendra avec son propre cadrage.
- Pas de suppression de `market.news.events` / `market.social.events` (topics réservés,
  hors périmètre).

## Partie I — Chantier B : persister et diffuser l'existant

### Trois hypertables (migration Alembic, rétention 90 j)

| Table | Colonnes (outre `time` + `symbol`, PK composite) |
|---|---|
| `derivatives_snapshots` | `venue` (str8, nullable, défaut applicatif `binance` — anticipe le collecteur Kraken Futures), `funding_rate_8h` (float), `funding_annualized_pct` (float), `open_interest_usd` (Numeric), `open_interest_change_pct_24h` (float), `long_short_account_ratio` (float) — toutes nullables |
| `fundamentals_snapshots` | `tvl_usd` (Numeric), `tvl_change_pct_7d`, `fees_change_pct_7d`, `next_unlock_at` (timestamptz), `next_unlock_pct_supply` — toutes nullables |
| `developer_snapshots` | `commit_ratio_4w`, `pr_ratio_4w`, `days_since_push`, `star_growth_pct_7d`, `dev_repo_count` (int), `all_repos_archived` (bool) — toutes nullables |

Un champ absent de l'événement reste `NULL` en base — jamais coercé (règle n°1 du projet).
Volume : univers borné (~200 tokens, majors seulement pour l'OI) × republication ~5 min —
trivial pour Timescale ; la rétention 90 j borne le stockage.

### Persistance

Le persister existant de l'api-gateway (`app/persister.py` + la liste d'abonnements dans
`main.py`) gagne les trois topics et trois handlers d'insertion — même patron que les
handlers actuels, insertion simple (pas d'upsert : chaque republication est un instantané
daté, c'est précisément l'historique recherché).

### Diffusion

`websocket-gateway` passe de 10 à 13 topics (ajout des trois). Côté front :
`DerivativesEvent`/`FundamentalsEvent`/`DeveloperEvent` rejoignent l'union `CmiEvent`
(`src/lib/types/events.ts`) et le rendu du `LiveEventStream` (libellés + `UNTRACEABLE_TYPES`
si pas de correlation id — à vérifier sur les événements réels). Le mock BFF simule les
trois types dans son flux.

### Push ciblé — RegimeStrip

`RegimeStrip` s'abonne : `useEventSubscription(['DerivativesEvent'], …)` →
`queryClient.invalidateQueries({ queryKey: ['market', 'regime'] })`, avec un débounce
simple (les republications arrivent par rafale d'univers — une invalidation par rafale
suffit ; le cache serveur 30 s absorbe le reste). Le poll 30 s est conservé.

### Dividende régime — `as_of` réels

`regime_api._feature_rows` continue de lire Redis (fraîcheur), mais les drivers funding/ΔOI
reçoivent désormais `as_of = max(time)` de `derivatives_snapshots` (une requête légère dans
le gather existant, sous le même garde-fou try/except + rollback). Le popover cesse
d'afficher « fraîcheur inconnue » pour ces deux drivers dès que la table a des lignes.

## Partie II — Chantier A : rallier collector-kraken

- **Nouvel événement** `CandleEvent` (Pydantic v2, `libs/cmi_common/cmi_common/events/`) :
  `symbol`, `venue`, `interval` (str, ex. `1h`), `open/high/low/close` (Numeric), `volume`,
  `trades` (int, nullable), `candle_start` (timestamptz). Nouveau
  `Topic.CANDLES = "market.candle.events"`.
- **`collector-kraken`** : la couche `infrastructure/` remplace l'écriture SQL des bougies
  par `EventProducer.publish(Topic.CANDLES, …)` — le domaine (normalisation des paires,
  résolution d'univers) ne bouge pas. Le carnet garde son chemin SQL direct (Non-objectif).
- **Persister api-gateway** : handler `CandleEvent` → insertion dans la table `candles`
  **existante** (aucune migration, aucun changement du lecteur `SqlCandleReader` ni du read
  plane).
- **Diffusion** : 14ᵉ topic au websocket-gateway ; `CandleEvent` rejoint `CmiEvent` et le
  `LiveEventStream`. Pas d'UI graphique dans ce chantier.
- **Transition** : un cycle de déploiement où collector-kraken publie et le persister écrit
  suffit — pas de double écriture, pas de backfill (la table garde son historique existant,
  le flux reprend au même endroit).

## Erreurs et cas limites

- **Persister en retard ou down** : les bougies s'accumulent dans Kafka (rétention topic) et
  sont écrites au rattrapage — c'est une amélioration sur l'écriture directe, qui perdait le
  cycle en cas d'erreur DB. Les snapshots dérivés manqués sont sans gravité : le suivant
  arrive ~5 min plus tard, et le trou est visible (pas de ligne) plutôt que masqué.
- **Événement partiel** (funding sans OI, TVL sans fees) : colonnes `NULL`, jamais 0.
- **Republication ≠ doublon** : deux instantanés successifs identiques sont deux lignes
  datées — voulu (historique), la rétention 90 j borne le coût.
- **WS silencieux** : le RegimeStrip garde son poll 30 s ; l'invalidation par événement est
  un accélérateur, pas une dépendance.
- **Ordre de déploiement B** : migration d'abord, puis persister (sinon insertions en échec —
  le persister loggue et continue, même comportement que les handlers existants sur erreur).

## Tests

- **Persister** : tests unitaires des trois nouveaux handlers (+ candle) sur le patron des
  tests persister existants — événement complet, événement partiel (NULL préservés),
  événement malformé (loggé, pas de crash).
- **Migration** : réversibilité (upgrade/downgrade) comme les migrations existantes.
- **websocket-gateway** : la liste des topics diffusés est asserté par un test si un tel
  test existe déjà (à vérifier au plan) ; sinon, test léger sur la constante.
- **Frontend** : union `CmiEvent` étendue (typecheck) ; test du RegimeStrip vérifiant
  l'invalidation à la réception d'un `DerivativesEvent` simulé ; mock BFF émettant les
  nouveaux types dans le flux simulé.
- **Règle transverse** : revue null-vs-zéro dédiée sur chaque diff (colonnes nullables,
  mappers d'événements, rendu front).

## Risques

- **Volume `candles`** : inchangé (mêmes données, autre chemin). Volume Kafka ajouté :
  ~quelques centaines de messages/5 min — négligeable.
- **Un persister multi-responsabilités** : le persister api-gateway grossit (4 familles de
  plus). Accepté : c'est le patron établi ; si le fichier devient ingérable, la découpe se
  fera à ce moment-là, pas préventivement.
- **`DerivativesEvent` sans `venue`** aujourd'hui : la colonne existe déjà en base, le
  handler écrit `binance` par défaut ; quand la spec positionnement ajoutera le champ à
  l'événement, seule la valeur transmise changera.
