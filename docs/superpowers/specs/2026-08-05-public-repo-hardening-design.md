# Passage du dépôt en public — audit, secrets et licence

**Date :** 2026-08-05
**Statut :** validé, prêt à planifier

## Objectif

Rendre `nbeny/BotTrading` public sans exposer de credential ni d'infrastructure, en
déplaçant la configuration sensible du VPS vers un Environment GitHub qui devient la
source de vérité, et en verrouillant l'usage par une licence propriétaire.

## Résultat de l'audit

Scan de l'intégralité de l'historique : 562 révisions, 5496 objets, 1763 blobs. Deux
passes indépendantes — une recherche de **chaque valeur réelle** du `.env` local, et une
recherche par motifs (`sk-ant-`, `ghp_`, `github_pat_`, `gho_`, `AKIA…`, `xox[baprs]-`,
`AIza…`, `BEGIN … PRIVATE KEY`, assignations génériques `password|secret|api_key|token = "…"`,
numéro de téléphone personnel, adresses e-mail, IPv4 publiques).

**Aucun secret n'a jamais été commité.** Aucun `.env` réel dans l'historique : seuls
`.env.example`, `.env.vps.example` et `frontend/.env.local.example`, tous à valeurs vides ou
`change-me`. `.env`, `frontend/.env.local`, `.worktrees/*/.env`, `.claude/` et
`scripts/telegram_channel_id.py` sont correctement ignorés et non suivis.

Faux positifs écartés : les 644 chaînes base64 sont les hashes d'intégrité de
`frontend/package-lock.json` ; `ENVIRONMENT=production` et `KAFKA_HEAP_OPTS` sont de la
configuration ; la clé Turnstile de `deploy.yml` est la **moitié publique**, déjà présente
dans le bundle JS servi aux navigateurs.

### Ce qui reste à traiter

| Trouvaille | Emplacement | Nature |
|---|---|---|
| IP publique du VPS (en clair) | 9 fichiers `docs/superpowers/`, HEAD **et** historique | Divulgation d'infrastructure |
| Nom d'admin réel `alesio` | `tests/test_api_gateway_auth.py:64` | Moitié d'un couple de login |
| `CLAUDE_DIR=C:\Users\nbeny\.claude` | `.env.example` | Chemin personnel, cosmétique |

Le domaine `crypto.nbeny.fr` (9 fichiers) est **conservé** : il est déjà public en DNS, et le
retirer casserait `deploy.yml` ainsi que la documentation de déploiement.

## Contrainte structurante

`deploy.yml` ne transmet aujourd'hui aucune variable au VPS. `scripts/deploy-vps.sh` lit un
`/opt/bottrading/.env` maintenu à la main sur la machine et s'arrête si le fichier manque.
Pousser des secrets dans un Environment GitHub est donc **inerte** tant que le workflow ne
régénère pas ce fichier. C'est ce que ce design change.

## Architecture

### Environment `production`

Le dépôt n'a aujourd'hui aucun Environment et 3 secrets de dépôt (`VPS_HOST`, `VPS_USER`,
`VPS_SSH_KEY`), qui restent où ils sont. Un Environment `production` est créé ; le job
`deploy` s'y rattache via `environment: production`.

**Secrets** (`gh secret set --env production`) — 15 entrées, valeurs lues depuis le `.env`
local et jamais affichées :

`DB_PASSWORD`, `JWT_SECRET`, `CONTROL_ADMIN_USER`, `CONTROL_ADMIN_PASSWORD`,
`CLAUDE_CODE_OAUTH_TOKEN`, `TURNSTILE_SECRET_KEY`, `NEWSDATA_API_KEY`, `NEYNAR_API_KEY`,
`YOUTUBE_API_KEY`, `BLUESKY_IDENTIFIER`, `BLUESKY_APP_PASSWORD`, `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `TELEGRAM_SESSION`, `GH_COLLECTOR_TOKEN`.

> **`GH_COLLECTOR_TOKEN` porte le PAT du collector-github.** GitHub refuse tout secret dont
> le nom commence par `GITHUB_` ; le rendu du `.env` le remappe vers `GITHUB_TOKEN=`.

**Variables** (`gh variable set --env production`) — non sensibles, donc lisibles et
auditables dans l'UI plutôt que masquées :

`LOG_LEVEL`, `ENVIRONMENT`, `DB_USER`, `DB_NAME`, `KAFKA_HEAP_OPTS`, `HAIKU_REPLICAS`,
`ANTHROPIC_CLI_CONCURRENCY`, `OTEL_TRACING_ENABLED`, `TRADING_MODE`, `GITHUB_POLL_INTERVAL`,
`GITHUB_MAX_REFRESH_PER_CYCLE`, `GITHUB_UNIVERSE_SIZE`, `GITHUB_LISTS_REFRESH_HOURS`.

`TRADING_MODE` est délibérément une variable et non un secret : c'est la valeur qui décide si
le bot passe de vrais ordres. En variable, elle est visible dans l'UI GitHub sans avoir à lire
le VPS. Elle est figée sur sa valeur courante, `dry_run`.

`REGISTRY` et `TAG` restent posés par le workflow lui-même.

### Rendu du `.env` sur le VPS

Une étape `Render .env` dans le job `deploy`, avant la synchronisation des fichiers.

- Les secrets et variables transitent par un bloc `env:` de l'étape. Le fichier est écrit par
  un script Python qui lit `os.environ`. **Aucune interpolation `${{ }}` dans du shell** : une
  valeur contenant `"`, `$`, une apostrophe ou un retour ligne casserait le fichier ou
  s'échapperait dans les logs.
- Le rendu écrit exactement les 32 clés que porte le `.env` actuel — ni plus, ni moins. Les
  ~53 autres variables référencées par `docker-compose.vps.yml` gardent leur défaut inline.
- `umask 077` au rendu, transfert par `rsync`, `chmod 600` à l'arrivée, suppression du fichier
  temporaire du runner.
- **Le `.env` existant sur le VPS est sauvegardé en `.env.bak.<sha>` avant écrasement**, et
  l'étape logue la liste des clés ajoutées et supprimées — **les noms seulement, jamais les
  valeurs**. C'est le seul garde-fou contre une clé posée à la main sur le VPS que le `.env`
  local ignorerait.

### Nettoyage de contenu et réécriture d'historique

Au niveau de HEAD : l'IP du VPS → `<VPS_HOST>` dans les 9 fichiers `docs/`, `alesio`
remplacé par un nom de fixture neutre dans `test_api_gateway_auth.py`, `CLAUDE_DIR` rendu
générique dans `.env.example`.

Puis `git filter-repo --replace-text` sur les 562 commits. Deux contraintes vérifiées :

- `git-filter-repo` n'est pas installé en sous-commande git mais le module Python l'est ; il
  est invoqué via Python.
- Les 4 worktrees actifs (`telegram-collector`, `github-ingestion`, `market-data-foundation`,
  `normalization-core`) sont **propres** — vérifié. filter-repo réécrit toutes les refs, donc
  les worktrees pointeraient sur des commits morts : ils sont démontés avant et remontés après.

`origin` ne porte que 3 refs (`master`, `feat/live-read-endpoints`,
`worktree-telegram-collector`) : ce sont les seules force-pushées, donc les seules jamais
exposées publiquement. Les 11 branches locales sont réécrites elles aussi, pour qu'un push
ultérieur ne réintroduise pas l'IP.

### Licence

Un fichier `LICENSE` propriétaire accordant **zéro droit** : lecture seule ; aucun usage
commercial ni non-commercial ; aucune copie, modification, redistribution, œuvre dérivée ni
exploitation du code ou des concepts. Une section **Usage** en tête de `README.md` le répète en
clair, avant toute documentation technique.

Deux limites qu'aucune licence ne lève, et qui doivent être assumées explicitement :

- Rendre le dépôt public **accorde de facto le droit de voir et de forker** au titre des CGU
  GitHub (section D.5). L'interdiction par licence est opposable juridiquement mais rien ne
  l'empêche techniquement, et un fork déjà pris survit à un repassage en privé.
- GitHub affichera « licence non reconnue » plutôt qu'un badge SPDX. C'est le comportement
  normal pour une licence propriétaire.

### Réglages du dépôt

Secret scanning et **push protection** activés — gratuits sur un dépôt public, et la push
protection bloque en amont un futur `git push` qui contiendrait un token. Puis
`gh repo edit --visibility public`.

## Ordre d'exécution

L'ordre est contraint : ne pas empiler deux opérations risquées, et ne pas passer en public
avant d'avoir confirmé que le déploiement tient toujours.

1. Nettoyage du contenu — IP, nom d'admin, chemin perso, `LICENSE`, mention d'usage (commit normal)
2. Secrets et variables → Environment `production` — avant de toucher au workflow, sinon celui-ci tourne à vide
3. `deploy.yml` rend le `.env` sur le VPS (commit + push normal)
4. **Point de contrôle : le déploiement passe au vert.** L'historique est encore intact, le retour arrière est trivial
5. Réécriture d'historique + force-push — une seule fois, à la fin
6. Secret scanning + push protection
7. Bascule en public

L'étape 5 redéclenche un déploiement par force-push sur `master`. C'est idempotent : mêmes
images, même `.env` rendu.

## Hors périmètre

**Rotation des credentials.** Décision prise de pousser les valeurs actuelles. Le dépôt ne les
a jamais portés, donc le passage en public ne crée pas ce risque — mais
`CLAUDE_CODE_OAUTH_TOKEN`, le PAT GitHub et `TELEGRAM_SESSION` (qui vaut un accès complet au
compte Telegram) ont été exposés en clair dans des conversations passées. Une checklist de
rotation est fournie en fin de parcours, à exécuter quand voulu.

## Risques et vérifications

| Risque | Garde-fou |
|---|---|
| Le `.env` rendu écrase une clé posée à la main sur le VPS | Sauvegarde `.env.bak.<sha>` + diff des noms de clés dans les logs |
| Une valeur avec caractère spécial casse le `.env` | Rendu Python depuis `os.environ`, jamais d'interpolation shell |
| `TRADING_MODE` bascule accidentellement en `live` | Figé en variable `dry_run`, lisible dans l'UI |
| Un secret fuite dans les logs du runner | Bloc `env:` (masquage GitHub) + logs limités aux noms de clés |
| La réécriture d'historique casse les worktrees | Propreté vérifiée avant, démontage et remontage encadrés |
| Le déploiement casse après le changement de workflow | Étape 4 bloquante avant toute opération irréversible |
