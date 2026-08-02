# Telegram comme source d'ingestion — design

**Date :** 2026-08-02
**Statut :** validé, prêt pour le plan d'implémentation

## Objectif

Ajouter Telegram (MTProto / Telethon) comme douzième source de contenu, de la lecture
des canaux jusqu'au score de sentiment consommé par `decision-engine`.

## Périmètre

**Dans le périmètre :** un `TelegramProvider` dans `collector-social`, sa liste de canaux
pilotable depuis le terminal, sa remontée de santé, ses tests.

**Hors périmètre, explicitement :**

- **La classification IA d'événements** (Listing, Hack, Partnership, Airdrop, Governance).
  Elle n'existe aujourd'hui pour aucune des onze sources déjà branchées, `raw_content` n'a
  pas de colonne de catégorie, et c'est une capacité transverse. La concevoir autour de
  Telegram la contraindrait à tort. Elle fera l'objet d'une spec séparée.
- **Un topic Kafka `telegram.raw.events`.** L'ingestion social/news est DB-sourced depuis
  le refactor `db-sourced-ingestion` : les collecteurs écrivent dans Postgres `raw_content`
  et `sentiment-service` scanne les lignes non scorées. Les topics Kafka social/news sont
  orphelins. Telegram entre par la même porte que les autres sources.
- **Une détection de symboles spécifique à Telegram.** `cmi_common.sources.normalize::ContentNormalizer`
  est le point de passage unique de tous les providers (cashtags, tickers, noms, gate de
  pertinence crypto, repli sur `MARKET`). Un second détecteur divergerait du premier.
- **La parité avec le BFF mock.** Le mock n'expose aucune route `/collectors/runtime` ;
  `SourcesPanel` est déjà en erreur sous `NEXT_PUBLIC_USE_MOCK=1`. Rien à répliquer.

## Choix d'architecture

Provider *pull* dans le service `collector-social` existant, piloté par
`AdaptivePollLoop`. Trois raisons :

1. Telethon ne renvoie pas d'en-têtes HTTP de rate-limit ; il lève `FloodWaitError` avec
   un attribut `seconds`, qui se mappe exactement sur le `RateLimitedError(retry_after=…)`
   que le loop sait déjà attendre.
2. La liste de canaux doit être éditable à chaud. Un modèle pull la relit à chaque cycle ;
   un listener push devrait désabonner et réabonner ses handlers à chaque changement.
3. Rien en aval n'exploite la sub-seconde : `sentiment-service` est un worker périodique
   sur la base et le `FeatureStore` expire à 900 s.

Les alternatives écartées : un service dédié `collector-telegram` (un conteneur et une
image GHCR pour un unique provider, alors que `collector-social` en héberge sept) ; un
listener push `@client.on(events.NewMessage)` (les messages émis pendant une déconnexion
sont perdus sans un rattrapage `min_id`, c'est-à-dire sans réimplémenter le modèle pull
par-dessus, et il faudrait recâbler toggles, budget et backoff hors du loop).

## Flux

```
Telethon (StringSession)  ─►  TelegramProvider.fetch()  ─►  RawItem[]
                                                              │
                        AdaptivePollLoop (existant) ──────────┤
                          · toggle collectors:runtime          │
                          · budget Redis + FloodWait           │
                          · LexiconNormalizer  ◄── résolution des symboles
                          · SqlContentRepository.insert_items
                                                              ▼
                                                    Postgres raw_content
                                                              │
                                    sentiment-service (worker périodique)
                                                              ▼
                                       content_sentiment_agg + SentimentEvent
                                                              ▼
                                                     decision-engine
```

## Composants

### `services/collector-social/app/providers/telegram.py` (neuf)

`TelegramProvider`, conforme au protocole `Provider` :

- `name = "telegram"`, `kind = "social"`.
- `rate_limit = (1000, 300)`, délibérément non contraignant. `AdaptivePollLoop` consomme
  un jeton **par cycle**, pas par appel API ; ce budget ne peut donc pas borner le nombre
  réel d'appels, qui est fonction du nombre de canaux. Le vrai limiteur est
  `FloodWaitError`, et le vrai plafond est la limite de 25 canaux.
- Construit avec `api_id`, `api_hash`, `session` (une `StringSession`) et le handle `Cache`
  — dont il a besoin pour les curseurs et pour la clé de santé.
- Client Telethon construit paresseusement au premier `fetch()`, sur le modèle de
  `BlueskyProvider._ensure_session()`. Aucun fichier `.session` à monter.
- `fetch()` lit la liste de canaux actifs depuis `collectors:runtime`, puis pour chacun :
  `client.get_messages(channel, min_id=cursor, limit=50)`.
- `close()` déconnecte le client.

Provider key-gated dans `_build_providers()`, comme `neynar` et `youtube` : il n'est
instancié que si `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` et `TELEGRAM_SESSION` sont tous
présents.

**Cadence.** `collector-social` passe aujourd'hui un unique `SOCIAL_POLL_INTERVAL` (300 s)
à toutes ses boucles, alors qu'`AdaptivePollLoop` prend déjà `poll_interval` par instance.
Telegram reçoit donc son propre `TELEGRAM_POLL_INTERVAL`, dont la valeur par défaut est
`SOCIAL_POLL_INTERVAL` : deux lignes dans `main.py`, et la latence Telegram devient
réglable sans toucher aux onze autres sources.

### Curseurs

Un `min_id` par canal dans Redis, clé `telegram:cursor:{channel}`, avancé au
`max(message.id)` du lot. Une perte de Redis ne provoque qu'un refetch borné par
`limit=50` : le vrai filet anti-doublon est la contrainte `UNIQUE(source, external_id)`
déjà en base.

### Mapping message → `RawItem`

| champ | source | note |
|---|---|---|
| `source` | `"telegram"` | |
| `kind` | `"social"` | |
| `external_id` | `f"{channel_id}:{message.id}"` | unique inter-canaux ; `channel_id` est l'**identifiant numérique**, pas le username — un canal renommé produirait sinon des doublons de tout son historique |
| `text` | `message.message` | vide sur média seul |
| `title` | `None` | |
| `author` | username du canal, à défaut son id numérique | l'unité pertinente est le canal, pas l'auteur du post |
| `url` | `https://t.me/{username}/{id}` | seulement si le canal a un username public, sinon `None` |
| `published_at` | `message.date` | déjà tz-aware UTC |
| `engagement` | somme vues + transferts + réactions | **`None` si aucune des trois n'est rapportée** |
| `symbols` | laissé vide | résolu par `LexiconNormalizer` |
| `lang` | `None` | non fourni par MTProto |

**`engagement` doit rester `None` quand la donnée est absente, jamais `0.0`.** Les groupes
non-broadcast n'ont aucun compteur de vues. Un zéro confiant remonterait dans
`engagement_sum` de `content_sentiment_agg` et tirerait le signal social vers le bas comme
s'il avait été mesuré. C'est le défaut récurrent de ce pipeline : une valeur non mesurée
qui entre comme une lecture assurée déplace toujours le score dans la direction de cette
lecture.

Un message sans texte produit quand même un `RawItem` ; c'est le normalizer qui le rejette
en `empty_text`. La responsabilité du rejet reste à un seul endroit.

### Liste de canaux pilotable

Stockée dans la clé Redis `collectors:runtime` sous `telegram_channels: list[str]`, que
`control-api` écrit déjà. Aucun canal de configuration neuf.

- `cmi_common.sources.runtime` : `default_runtime()` porte une constante
  `TELEGRAM_SEED_CHANNELS`, sur le modèle de `SEED_LEXICON`. Pas de variable d'env, pour
  éviter deux sources de vérité.
- **Cette graine ship vide.** Aucune liste de canaux crypto n'a été fournie, et inventer
  des usernames non vérifiés produirait des canaux introuvables signalés en erreur à
  chaque cycle. L'opérateur peuple la liste depuis le terminal au premier démarrage ; d'ici
  là `fetch()` renvoie une liste vide sans rien consommer. Une graine non vide peut être
  posée dans cette constante plus tard sans autre changement.
- `set_runtime` traite `telegram_channels` en **remplacement** intégral, pas en merge
  (contrairement à `platforms`).
- La lecture doit distinguer absent et vide avec un test `is None`, jamais un `or`. Tant
  que la graine est vide les deux se comportent pareil ; le jour où une graine non vide y
  est posée, un `cfg.get("telegram_channels") or SEED` ferait silencieusement revivre les
  canaux que l'opérateur vient de supprimer.
- `KNOWN_PLATFORMS["social"]` gagne `"telegram"`, ce qui fait apparaître son interrupteur
  dans `SourcesPanel` sans toucher au rendu (le composant itère sur `known_platforms`).

Côté `control-api` (`app/routers/collectors.py`) : `RuntimePatch` gagne
`telegram_channels: list[str] | None = None`. À l'écriture, chaque entrée est normalisée
(`@nom`, `t.me/nom`, `https://t.me/nom` → `nom`) et les liens d'invitation (`t.me/+hash`,
`joinchat`) sont **rejetés en 422** : ils supposent un flux d'adhésion que ce provider ne
fait pas. Liste plafonnée à 25 entrées, pour borner le nombre d'appels par cycle.

Côté frontend : `CollectorRuntime` gagne `telegram_channels: string[]` et
`source_status: Record<string, { ok: boolean; reason?: string }>`, la signature de
`collectorsApi.setRuntime` gagne le champ optionnel correspondant, `LABELS` gagne
`telegram: 'Telegram'`, et `SourcesPanel` affiche sous l'interrupteur Telegram un éditeur
de chips (ajout / suppression).

## Gestion d'erreurs

`AdaptivePollLoop` avale toute exception et repart en backoff de 120 s. C'est adapté au
transitoire, dangereux pour une panne permanente : une `StringSession` révoquée — ce qui
arrive dès qu'on se déconnecte depuis un autre appareil — produirait un avertissement
toutes les deux minutes pendant des semaines sans que rien ne le signale.

**Santé.** Le provider tient lui-même une clé `collectors:status:telegram` dans Redis :
`{ok: true}` après un cycle réussi, `{ok: false, reason}` sur erreur d'authentification.
`GET /collectors/runtime` la renvoie sous `source_status` (dictionnaire indexé par nom de
plateforme, pour que d'autres providers puissent s'y ajouter plus tard) et `SourcesPanel`
affiche une pastille rouge à côté de l'interrupteur. Pas de vérification au démarrage et
pas de crash au boot : faire tomber `collector-social` pour une session Telegram invalide
tuerait sept collecteurs sains.

**Rate limit.** `FloodWaitError` → `RateLimitedError(exc.seconds)`. Le loop met la source
en pause exactement ce temps-là.

**Canal injoignable** (inexistant, privé, non rejoint) : attrapé **par canal**, jamais au
niveau du cycle. Un `@nom` mal saisi ne doit pas empêcher les vingt-quatre autres d'être
lus. Le canal fautif est reporté dans la clé de santé pour affichage.

**Réseau transitoire :** laissé remonter, backoff standard du loop.

## Tests

Cœur testé sans réseau, avec un faux client Telethon :

- `engagement` vaut `None` quand ni vues, ni transferts, ni réactions ne sont rapportés ;
  et vaut leur somme quand ils le sont. **C'est le test central.**
- `external_id` distinct pour deux messages de même `id` dans deux canaux différents.
- `FloodWaitError` → `RateLimitedError` portant le bon `retry_after`.
- Un canal en erreur laisse passer les messages des autres canaux du même cycle.
- Curseur avancé au `max(message.id)` et relu au cycle suivant.
- Message sans texte : le `RawItem` est émis, le rejet appartient au normalizer.
- `url` à `None` pour un canal sans username public.

Côté `runtime.py` : avec `TELEGRAM_SEED_CHANNELS` remplacée par une graine non vide dans
le test, une liste `telegram_channels` explicitement vide en Redis ne retombe pas dessus.
Tester contre la graine vide livrée ne prouverait rien.

Côté `control-api` : normalisation des trois formats de canal, rejet en 422 des liens
d'invitation, rejet au-delà de 25 entrées.

`ContentNormalizer` n'est pas modifié ; ses tests existants couvrent déjà la résolution de
symboles sur du texte de type Telegram.

Une vérification live (un canal réel, N messages listés) reste un script hors CI, sur le
modèle de `scripts/verify_read_live.py`.

## Déploiement

`telethon>=1.36` dans `services/collector-social/pyproject.toml`.

Trois secrets, tous requis ensemble pour activer le provider : `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `TELEGRAM_SESSION` (une `StringSession`). Déclarés dans le bloc
`environment` de `collector-social` dans `docker-compose.vps.yml`, et renseignés dans
`/opt/bottrading/.env` sur le VPS. `.github/workflows/deploy.yml` n'est **pas** modifié :
il ne transporte aucun secret applicatif — il `rsync` le compose puis lance
`scripts/deploy-vps.sh` par ssh, et c'est pourquoi `NEYNAR_API_KEY` n'y figure pas non
plus. La session est un identifiant de compte complet : elle ne doit jamais être commitée
ni journalisée.

Une variable optionnelle : `TELEGRAM_POLL_INTERVAL`, qui vaut `SOCIAL_POLL_INTERVAL` par
défaut.

## Critères de succès

1. Des lignes `source = 'telegram'` apparaissent dans `raw_content`, avec des `symbols`
   résolus par le normalizer commun.
2. `sentiment-service` les score et publie les `SentimentEvent` correspondants, sans
   modification de ce service.
3. Couper Telegram depuis `SourcesPanel` arrête la collecte au cycle suivant ; éditer la
   liste de canaux prend effet au cycle suivant, sans redéploiement.
4. Une session invalide se voit dans le terminal, et n'empêche aucun autre collecteur de
   tourner.
5. Un message de groupe sans compteur de vues produit `engagement IS NULL` en base, pas
   `0.0`.
