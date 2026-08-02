# GitHub comme source d'information — design

**Date :** 2026-08-02
**Statut :** validé, prêt pour le plan d'implémentation

## Objectif

Ajouter l'activité de développement GitHub comme signal mesuré par token, de la
résolution `coin → repos` jusqu'à un huitième axe de scoring `developer_activity`
consommé par `decision-engine`.

Accessoirement, constituer un registre des projets crypto (URL GitHub, URL du site
officiel, catégorie, description) extrait des deux listes de référence
`lukasmasuch/best-of-crypto` et `dylanhogg/awesome-crypto`.

## Ce que le signal est, et ce qu'il n'est pas

**Ce n'est pas du sentiment.** `sentiment-service` fait tourner CryptoBERT sur du
*texte* d'opinion. « le repo Aave a reçu 12 commits et 3 PR mergées cette semaine »
est une mesure, pas une opinion : la passer dans un modèle de sentiment produirait un
score inventé. GitHub mesure de l'**activité développeur**, cousine des fondamentaux
(TVL, fees, unlocks) et non du social.

**Ce n'est pas un niveau absolu.** Bitcoin a 1 200 contributeurs, un token récent en a
quatre. Un axe bâti sur des volumes bruts classerait mécaniquement les grandes
capitalisations en tête et ne dirait rien que le pipeline ne sache déjà — il
dupliquerait la capitalisation sous un autre nom.

**C'est un momentum relatif au projet lui-même.** Chaque repo est comparé à sa propre
baseline sur 52 semaines. Un petit projet qui accélère score haut ; un géant qui
stagne score bas. C'est le seul cadrage qui apporte de l'information indépendante aux
sept axes existants.

## Périmètre

**Dans le périmètre :** un service `collector-github`, la résolution `coin → repos`,
le parsing des deux README, le registre de projets, l'événement Kafka
`market.developer.events`, le huitième axe et sa propagation dans les trois copies de
la liste d'axes, un script de vérification de la distribution en conditions réelles.

**Hors périmètre, explicitement :**

- **L'étape 2 : le texte GitHub vers `raw_content`.** Titres de PR, notes de release et
  descriptions de projet nouvellement listés peuvent alimenter `raw_content` en
  `kind="news"` et être scorés par CryptoBERT comme le reste. C'est une capacité
  distincte, qui n'a de valeur qu'une fois la qualité du mapping `repo → ticker`
  validée en production. Elle fera l'objet de sa propre spec.
- **Les métriques inline des README.** Les deux listes affichent des compteurs
  (⭐, 👨‍💻, 🔀, stars/semaine). Ce sont des snapshots régénérés par script une fois par
  semaine : les lire reviendrait à lire une photo périmée d'une donnée que l'API rend
  fraîche. Les README ne servent qu'à la découverte de repos et d'URLs.
- **Les événements éditoriaux des listes** (entrée d'un projet, montée de rang). Ces
  listes sont régénérées par un générateur, pas par un jugement humain quotidien ; le
  signal serait rare et bruyant. Le registre conserve `first_seen_at`, ce qui laisse la
  porte ouverte sans rien construire dessus aujourd'hui.
- **Un axe « santé du repo »** (ratio issues ouvertes/fermées, délai médian de merge).
  Écarté au profit du momentum seul, pour garder l'axe interprétable au premier
  déploiement.
- **La parité avec le BFF mock.** Le mock ne sert pas de dossier par token construit
  depuis les axes réels ; rien à répliquer.

## Choix d'architecture

Un service dédié `collector-github`, sur le modèle de `collector-defillama` et
`collector-binance-futures` : son propre limiteur de débit, son propre `/health` et
`/metrics`, ses propres tests, et un huitième axe nommé pour ce qu'il mesure.

Les alternatives écartées :

- **Fondre GitHub dans l'axe `fundamentals` existant** évitait le huitième axe et sa
  triple copie. Mais cela mélange « le protocole capte de la valeur » et « le code
  bouge », deux choses qui divergent précisément quand c'est intéressant : un fork
  abandonné garde sa TVL des mois. Et le signal devient indébuggable — un
  `fundamentals` à 0.4 ne dirait plus lequel des deux composants parle.
- **Un provider dans `collector-social`** ne convient pas : à l'étape 1, le service ne
  produit pas de `RawItem` mais des métriques.

Le moment est favorable : `DECISION_THRESHOLD` est encore à 101, donc le pipeline ne
trade pas sur ces scores. C'est la fenêtre où ajouter un axe coûte le moins cher, et la
rebalance des poids devra de toute façon être refaite lors du recalcul du seuil sur des
features v2 réelles.

## Deux horloges, pas une

C'est le point structurant du service, et la contrainte vient d'en aval : le
`FeatureStore` de `decision-engine` **expire à 900 s**. Un cycle de rafraîchissement de
12 h publierait l'axe quinze minutes toutes les douze heures — absent 98 % du temps.

Le service tourne donc toutes les **600 s** comme ses voisins, et à chaque cycle :

1. il **republie tous les symboles présents en cache** vers Kafka, qu'ils aient été
   rafraîchis ou non ;
2. il **rafraîchit depuis GitHub un petit lot en round-robin** — environ 7 repos par
   cycle, soit un balayage complet des ~500 repos en 12 h.

Le budget borne les téléchargements, jamais le reporting. C'est exactement la leçon
déjà payée sur les unlocks DefiLlama, où un plafond appliqué à l'appartenance à la
carte (et non aux téléchargements) avait fait déclarer « aucun calendrier connu » à 37
tokens sur 40 dont le calendrier était déjà en cache — et comme la renormalisation note
mieux un axe absent qu'un axe mesuré mauvais, le plafond promouvait silencieusement
tout ce qu'il sautait.

Le rafraîchissement complet en 12 h est un horizon, pas une fréquence de signal.

## Chaîne de données

```
Token(coin_id, symbol) ─► CoinGecko /coins/{id} ─► links.repos_url.github ──┐
                          (cache Postgres 30 j, refresh round-robin)        │
                                                                            ▼
README awesome-lists (hebdo) ─► crypto_project_registry ────────────►  coin_repo_map
    best-of-crypto  : github_url                                            │
    awesome-crypto  : github_url + homepage_url                             ▼
                                                    GitHub API ─► github_repo_snapshot
                                                                            │
                                              domain/activity.py (pur) ─────┤
                                                                            ▼
                                                DeveloperEvent ─► market.developer.events
                                                                            ▼
                                                FeatureStore ─► axe developer_activity
```

## Mapping `repo → ticker`

C'est ce qui décide si l'axe mesure quelque chose ou l'invente. Sur les ~8 400 entrées
cumulées des deux listes, la grande majorité sont des bibliothèques sans token
(`ccxt`, `web3.py`, OpenZeppelin, compilateurs Solidity).

**Colonne vertébrale : CoinGecko.** `GET /coins/{id}` expose
`links.repos_url.github`, une liste d'URLs — le rattachement officiel du coin à ses
dépôts. `Token.coin_id` est déjà peuplé et joint directement. Ce mapping change au
rythme des listings, pas des polls : il est mis en cache dans Postgres avec un TTL de
30 jours et rafraîchi en round-robin, quelques coins par cycle. Le premier remplissage
prend plusieurs cycles ; c'est acceptable et cela respecte le quota gratuit de
CoinGecko (10 à 30 appels/min).

**Appoint : les deux listes.** Elles fournissent (a) les URLs de sites officiels
demandées, (b) des repos candidats supplémentaires. Un candidat n'est promu dans
`coin_repo_map` que si le nom du dépôt ou de l'organisation résout **sans ambiguïté**
contre le `SymbolLexicon`. Les homographes que le lexicon signale déjà (ONE, KEEP,
FLOW…) ne sont jamais promus par cette voie.

**Le reste est conservé, jamais scoré.** Une entrée de registre sans symbole résolu
garde `symbol = NULL`. Elle n'entre dans aucun agrégat et ne produit aucun événement.
`NULL` signifie « pas de rattachement connu », ce qui n'est pas la même affirmation que
« ce projet n'a aucune activité ».

Différence de couverture entre les deux listes, à ne pas maquiller : **seule
`awesome-crypto` publie une URL de site officiel**, sur une ligne dédiée.
`best-of-crypto` n'expose que le lien GitHub. `homepage_url` sera donc `NULL` pour les
projets issus de la seule première liste, et le registre le reflète tel quel.

## Modules

Convention clean architecture du dépôt : `domain/` pur, `application/` cas d'usage,
`infrastructure/` I/O, `main.py` FastAPI + `run_periodic`.

### `infrastructure/github_client.py`

Client REST authentifié par `GITHUB_TOKEN`, auto-throttlé sur les en-têtes
`x-ratelimit-remaining` / `x-ratelimit-reset` — le même principe que les providers
sociaux, qui apprennent leur limite de l'API plutôt que de la coder en dur.

Appels par dépôt (4) :

| Endpoint | Ce qu'on en tire |
|---|---|
| `GET /repos/{o}/{r}` | `stargazers_count`, `forks_count`, `pushed_at`, `archived`, `fork` |
| `GET /repos/{o}/{r}/stats/commit_activity` | 52 semaines de commits — la baseline **et** la fenêtre récente en un seul appel |
| `GET /search/issues?q=repo:{o}/{r}+is:pr+is:merged+merged:>{J-28}` | `total_count` — PR mergées sur 4 semaines |
| `GET /search/issues?q=repo:{o}/{r}+is:pr+is:merged+merged:>{J-364}` | `total_count` — baseline PR sur 52 semaines |

Trois comportements de l'API à traiter explicitement, parce qu'ils échouent en
silence :

- **`202 Accepted` sur `/stats/*`.** GitHub calcule ces statistiques de façon
  asynchrone et renvoie un corps vide en attendant. Le client retourne `None` — jamais
  une liste vide, jamais `0` — et le dépôt repasse au tour suivant. Un `0` ici
  signifierait « ce projet n'a pas commité de l'année », ce qui est une affirmation
  autrement plus forte que « GitHub n'a pas fini de compter ».
- **`404`.** Dépôt renommé, supprimé ou passé privé. Écrit au tableau noir jusqu'au
  redémarrage, comme `_dead` dans le provider Telegram : un handle mort coûte un appel,
  pas un appel par cycle.
- **L'API `/search` a son propre seau.** 30 requêtes/minute pour un compte
  authentifié, indépendant des 5 000/heure du cœur REST. Elle a donc son propre
  limiteur ; les mélanger ferait passer le service pour dans les clous jusqu'au premier
  `403` de rate-limit secondaire.

Budget : ~7 dépôts/cycle × 4 appels ≈ 28 requêtes/600 s, dont 14 sur le seau `/search`.
Très en deçà des deux plafonds.

### `infrastructure/awesome_lists.py`

Récupère les deux README bruts et les parse. Cadence hebdomadaire — les listes sont
régénérées à cette fréquence.

- `best-of-crypto` : entrées en blocs `<details><summary>` ; les URLs de dépôt sont les
  `href` `https://github.com/{owner}/{repo}`. ~3 100 projets, 11 catégories.
- `awesome-crypto` : entrées `### [nom](url_github)`, suivies d'une ligne d'URL de site
  puis d'une ligne d'URL GitHub, description et tags. ~5 326 dépôts.

Le parsing est tolérant par conception : une entrée non reconnue est comptée et
ignorée, jamais devinée. Le compteur d'entrées non parsées est exposé en métrique —
une liste dont le format change doit se voir, pas se traduire par un registre qui
rétrécit sans bruit.

### `domain/activity.py`

Pur et synchrone, sans I/O : mêmes entrées, mêmes sorties, testable hors ligne sur des
fixtures capturées en conditions réelles. Il transforme les statistiques brutes d'un
dépôt en sous-signaux normalisés sur `[0, 1]`, chacun optionnel :

| Sous-signal | Calcul | Poids |
|---|---|---|
| `commit_momentum` | `r = commits_4s / (médiane_hebdo_52s × 4)`, puis `clamp(0.5 + 0.5·log(r)/log(3), 0, 1)` | 0.40 |
| `pr_momentum` | même forme, sur PR mergées 4 s vs baseline hebdo 52 s | 0.25 |
| `freshness` | jours depuis `pushed_at` : `1.0` à ≤ 7 j, `0.0` à ≥ 90 j, linéaire entre | 0.25 |
| `star_growth` | `0.3 + 0.7·clamp(croissance_7j / 0.02, 0, 1)` | 0.10 |

L'échelle logarithmique du momentum est symétrique par construction : un projet à son
rythme habituel vaut 0.5, un projet qui triple vaut 1.0, un projet tombé au tiers vaut
0.0. Une médiane de baseline nulle rend le ratio indéfini, donc le sous-signal est
`None` — pas `0.0`, et pas `1.0` non plus au motif que « tout commit est une
accélération infinie ».

`star_growth` est `None` au premier passage sur un dépôt : un delta demande deux
snapshots. Un premier cycle qui rapporterait `0` inventerait une stagnation.

L'axe agrège ses sous-signaux **sur le poids présent**, comme le scoring principal
agrège ses axes. Aucun sous-signal disponible ⇒ l'axe est absent (`None`), pas nul.

### `domain/aggregate.py`

Un coin a N dépôts. Les dépôts `archived` ou `fork` sont **exclus** de l'agrégat, pas
comptés à zéro. Sur les dépôts restants : commits et PR sommés, momentum recalculé sur
l'agrégat, fraîcheur = le `pushed_at` le plus récent.

Un seul zéro est légitime dans toute cette chaîne : lorsqu'un coin a des dépôts connus
et que **tous** sont archivés ou forkés, l'axe rapporte `0.0` comme valeur mesurée. On
a regardé, et c'est mort — c'est une observation, pas une absence.

### `application/collector.py`

Un cycle : rafraîchir la carte des symboles, publier depuis le cache, rafraîchir le lot
round-robin, persister les snapshots. Les compteurs du cycle sont journalisés séparément
(`eligible`, `cached`, `fetched`, `deferred_202`, `failed`) : leur somme ne permettrait
pas de distinguer « rien n'était éligible » de « tout a échoué », deux situations qui
appellent des réactions opposées d'un opérateur.

## Modèle de données

Trois tables, une migration Alembic.

**`crypto_project_registry`** — le registre issu des listes.
`id`, `name`, `github_url` (unique), `homepage_url` (nullable), `description`,
`category`, `source_list`, `symbol` (nullable), `first_seen_at`, `last_seen_at`.

**`coin_repo_map`** — le mapping de confiance.
`coin_id`, `symbol`, `owner`, `repo`, `origin` (`coingecko` | `awesome_list`),
`resolved_at`. Clé unique `(coin_id, owner, repo)`.

**`github_repo_snapshot`** — l'historique nécessaire aux deltas.
`owner`, `repo`, `observed_at`, `stars`, `forks`, `commits_4w`, `commits_median_52w`,
`pr_merged_4w`, `pr_merged_52w`, `pushed_at`, `archived`, `is_fork`. Toutes les
colonnes de mesure sont nullables. Rétention 90 jours.

## Contrat d'événement

Dans `cmi_common` : `EventType.DEVELOPER`, `Topic.DEVELOPER = "market.developer.events"`,
entrée correspondante dans `TOPIC_EVENT`, et le modèle Pydantic v2 :

```python
class DeveloperEvent(BaseEvent):
    event_type: Literal[EventType.DEVELOPER] = EventType.DEVELOPER
    symbol: str
    coin_id: str
    repo_count: int                          # dépôts retenus dans l'agrégat
    commit_momentum: float | None = None
    pr_momentum: float | None = None
    days_since_push: int | None = None
    star_growth_7d: float | None = None
    all_repos_archived: bool = False         # le seul zéro légitime
```

Chaque champ de mesure est `X | None`. `repo_count` est un décompte, pas une mesure :
il vaut `0` quand aucun dépôt n'est retenu, et l'événement n'est alors pas publié.

## Scoring : le huitième axe

`_norm_developer_activity` consomme les champs de l'événement et retourne `None` si
aucun sous-signal n'est présent — l'axe est alors **exclu** de la renormalisation, pas
scoré `0.0`.

Poids existants rescalés ×0.92 pour laisser 0.08 au nouvel axe, somme exacte à 1.0 afin
que `_MIN_PRESENT_WEIGHT = 0.20` garde exactement le sens qu'il a aujourd'hui :

| axe | avant | après |
|---|---|---|
| `volume_growth` | 0.1875 | 0.1725 |
| `social_score` | 0.1500 | 0.1380 |
| `news_score` | 0.1500 | 0.1380 |
| `market_trend` | 0.1500 | 0.1380 |
| `positioning` | 0.1500 | 0.1380 |
| `liquidity_score` | 0.1125 | 0.1035 |
| `fundamentals` | 0.1000 | 0.0920 |
| **`developer_activity`** | — | **0.0800** |

L'axe est **spécifique au symbole**, il compte donc dans `confidence` — contrairement à
la lecture de régime de marché, identique pour tous les symboles.

**Les trois copies bougent dans le même commit.** La liste d'axes existe en trois
exemplaires indépendants, dont aucun n'importe les autres :

- `services/decision-engine/app/scoring.py::WEIGHTS`
- `services/api-gateway/app/dossier.py::AXIS_KEYS`
- `frontend/src/lib/types/dossier.ts::SCORE_AXES`

Rien ne vérifie aujourd'hui qu'elles restent alignées : un axe oublié dans l'une des
trois serait simplement invisible dans le drawer `/market`, sans erreur ni test rouge.
Cette spec ajoute donc **un test de parité entre les trois listes**, qui lit les clés
des trois fichiers et échoue si elles divergent. Il manque aujourd'hui, et il aurait
attrapé la classe de défaut que CLAUDE.md décrit.

## Configuration et sécurité

| Variable | Défaut | Rôle |
|---|---|---|
| `GITHUB_TOKEN` | vide | PAT ; sans lui le service démarre mais reste inactif et le signale bruyamment |
| `GITHUB_POLL_INTERVAL` | `600` | cycle de publication |
| `GITHUB_MAX_REFRESH_PER_CYCLE` | `7` | plancher à 1, comme `DEFILLAMA_MAX_UNLOCK_FETCHES` |
| `GITHUB_UNIVERSE_SIZE` | `250` | nombre de coins suivis, par capitalisation |
| `GITHUB_LISTS_REFRESH_HOURS` | `168` | rafraîchissement des README |

Le token est une variable d'environnement, ajoutée à `.env.example` avec une valeur
**vide** et au `docker-compose`. Il n'entre jamais dans un fichier suivi par git.

Sans `GITHUB_TOKEN`, le service ne bascule pas sur l'API anonyme (60 req/h, inutilisable
ici) : il journalise l'absence à chaque cycle et ne publie rien. Un axe absent est
correctement traité en aval ; un axe alimenté par un quota anonyme épuisé produirait des
mesures partielles indiscernables de mesures complètes.

**Le PAT communiqué pendant la conception de cette spec est à considérer comme
compromis et doit être régénéré avant tout déploiement.**

## Tests

- **`domain/activity.py` et `domain/aggregate.py`** : purs, testés hors ligne. Cas
  nominaux et, surtout, tous les cas `None` — baseline nulle, premier snapshot, `202`
  en attente, dépôt archivé, tous dépôts archivés. Chaque test qui produit `None` doit
  aussi vérifier que ce n'est pas `0.0` : c'est la classe de défaut qui a coûté le plus
  cher sur ce projet et aucun de ces défauts n'avait fait échouer un test.
- **`infrastructure/awesome_lists.py`** : fixtures capturées des deux README réels,
  incluant des entrées malformées et une entrée sans URL de site.
- **`infrastructure/github_client.py`** : transport factice ; `202`, `404`, `403`
  secondaire, en-têtes de rate-limit.
- **Parité des trois listes d'axes** : nouveau test transverse.
- **`scripts/verify_github_activity.py`** : harnais live, dans la lignée de
  `verify_read_live.py`. Il sort la distribution réelle de l'axe sur l'univers — c'est
  le test de vérité avant de faire confiance au poids de 0.08.

## Critères de succès

1. Le mapping `coin → repos` couvre au moins 60 % de l'univers suivi après un balayage
   complet.
2. Sur un cycle en conditions réelles, aucun symbole ne publie `0.0` sur un sous-signal
   dont la donnée source était absente.
3. La distribution de `developer_activity` sur l'univers n'est pas dégénérée : elle
   n'est ni concentrée sur une valeur, ni ordonnée comme la capitalisation.
4. Les trois copies de la liste d'axes contiennent `developer_activity`, et le test de
   parité échoue si l'une est modifiée seule.

## Distribution observée — 2026-08-02

Premier passage du harnais `scripts/verify_github_activity.py` contre les API
réelles, 20 coins.

| | |
|---|---|
| Couverture mapping | 10/20 — mais les 10 coins auxquels CoinGecko a répondu déclarent **tous** au moins un dépôt ; les 10 autres sont des `429`, pas des absences |
| Médiane de l'axe | 0.568 |
| Écart-type | 0.201 |
| Déciles | `[1, 0, 0, 0, 1, 4, 2, 2, 0, 0]` |
| Corrélation au rang de capitalisation | **τ = −0.42** |

**Verdict : la distribution n'est pas dégénérée, le poids de 0.08 est conservé.**
Les valeurs se répartissent sur cinq dixièmes et le plus chargé n'en contient que
4 sur 10, loin du seuil de 70 %. Le τ négatif est le résultat qui compte : l'axe
n'ordonne pas comme la capitalisation — c'était le reproche fondateur au cadrage
en niveau absolu, et le cadrage relatif l'évite. Le signe mérite d'être noté : le
momentum relatif favorise structurellement les projets plus petits, qui accélèrent
plus facilement. C'est le comportement voulu, mais c'est un biais de taille, pas
une absence de biais.

### Trois faits que seule l'exécution réelle a révélés

**Le mapping CoinGecko est périmé pour une partie des coins, et cela produit un
zéro *mesuré* là où le projet est en réalité très actif.** Solana déclare
`solana-labs/solana`, archivé depuis la migration vers `anza-xyz/agave` ; Aave
déclare un dépôt de la même génération. Les deux sortent à 0.000. Le mécanisme
est correct — tous les dépôts connus sont archivés, donc l'axe rapporte un zéro
observé plutôt que d'inventer de l'activité — mais la conclusion est fausse, et
du mauvais côté : ces projets seront pénalisés.

C'est exactement le trou que `promote_list_entries` devait combler. Les
awesome-lists connaissent `anza-xyz/agave`, CoinGecko non. La fonction est
écrite et testée mais branchée sur rien, report assumé en attendant cette
mesure. **La mesure tranche : il faut la brancher.**

**`/search/issues` répond `422` — et non `404` — pour un dépôt qu'il ne peut pas
indexer.** Observé sur 4 dépôts distincts sur ~25, tous appartenant à des
organisations renommées (`nearprotocol/*`, `input-output-hk/plutus`). Non
traité, ce cas faisait perdre la mesure à tous les tokens suivants du cycle. Il
est désormais lu comme une mesure absente.

**Le quota gratuit de CoinGecko est plus étroit qu'annoncé.** Un intervalle de
6,5 s entre appels ne suffit pas : la moitié des requêtes ont reçu `429`. Sans
conséquence en production, où le collector n'interroge qu'un coin par cycle de
600 s, mais tout balayage groupé doit prévoir un repli ou une clé démo.

## Risque connu, assumé

Sur ~500 dépôts, ceux des chaînes majeures bougent quotidiennement tandis que ceux des
capitalisations moyennes sont souvent des monorepos, ou au contraire des forks quasi
morts. Le momentum y sera bruyant. Le critère 3 est ce qui tranche : si la distribution
est dégénérée, le poids de 0.08 est ramené à 0 et l'axe reste observable sans influencer
le score, le temps d'affiner l'agrégation multi-dépôts.
