# Spec — Transport CLI (abonnement) pour ai-worker-haiku & ai-worker-sonnet

**Date :** 2026-07-21
**Statut :** Validé (design), en attente de revue utilisateur avant plan d'implémentation
**Approche retenue :** A — transport enfichable dans `ClaudeClient`

## 1. Objectif

Faire tourner les deux workers d'analyse Claude (`ai-worker-haiku` = triage rapide,
`ai-worker-sonnet` = validation senior) via le **CLI `claude` sous abonnement OAuth**
au lieu de l'**API Anthropic facturée au token**, sur le modèle du projet AutoDevClaude.

Le rôle métier des workers, leurs prompts et les schémas d'événements **ne changent pas**.
Seul le backend d'exécution des appels Claude change.

### Contraintes clés

- **Indépendance stricte des requêtes.** Chaque appel lance un process `claude` frais,
  one-shot, fermé après réponse. **Jamais** `--continue` ni `--resume` : aucune session
  ni fenêtre partagée entre deux demandes.
- **Parallélisme d'abord, file en dernier recours.** Plusieurs process `claude` peuvent
  tourner en parallèle sur un même conteneur worker (plafond configurable). La mise en
  file d'attente ne se déclenche qu'au-dessus du plafond, car la latence compte.
- **Dégradation propre.** Sur timeout / échec / quota, le worker skippe l'événement sans
  crasher (comme le comportement actuel sur erreur de parsing).

## 2. Architecture

Le point d'appel unique reste inchangé :

```
ClaudeClient.complete(system, prompt, service) -> ClaudeResponse
```

`ClaudeClient` devient un **dispatcher** sur un transport choisi à la construction :

| Transport       | Rôle                                                        |
|-----------------|-------------------------------------------------------------|
| `ApiTransport`  | Chemin SDK Anthropic actuel (déplacé tel quel).             |
| `CliTransport`  | **Nouveau** : lance `claude -p` en subprocess.              |
| `StubTransport` | Stub offline déterministe actuel (déplacé).                 |

Le module `ai/` reste **découplé de `config.py`** : `main.py` construit un petit objet
`CliOptions` (dataclass définie dans `ai/`) à partir de `settings.ai` et le passe au
client. Les fichiers `worker.py` des deux services ne changent pas.

### Fichiers touchés

- `libs/cmi_common/cmi_common/ai/claude.py` — refactor en dispatcher + transports.
  (ou éclatement en `ai/transports/` : `api.py`, `cli.py`, `stub.py`, `base.py` — à
  trancher au plan ; l'interface publique `ClaudeClient` / `ClaudeResponse` reste stable.)
- `libs/cmi_common/cmi_common/config.py` — nouveaux champs dans `AISettings`.
- `services/ai-worker-haiku/app/main.py` — construit `CliOptions` depuis `settings.ai`.
- `services/ai-worker-sonnet/app/main.py` — idem.
- `libs/cmi_common/cmi_common/observability/metrics.py` — nouveau compteur `AI_CLI_CALLS`.
- `docker/Dockerfile.ai-worker` — **nouveau** (base + Node + CLI).
- `docker-compose.yml` — build + volumes auth + env pour les 2 workers.
- `.env.example` — nouvelles variables.
- Tests unitaires du `CliTransport` (faux CLI).

## 3. CliTransport — comportement détaillé

À chaque `complete(system, prompt, service)` :

1. **Acquiert le sémaphore** (`asyncio.Semaphore`, taille = `cli_concurrency`). Si le
   plafond est atteint, l'appel attend qu'un slot se libère (file = dernier recours).
2. **Crée un cwd scratch dédié** (répertoire temporaire unique), supprimé en `finally`
   → isolation entre process concurrents, aucune collision de fichiers.
3. **Lance un process frais** via `asyncio.create_subprocess_exec` :
   ```
   claude -p \
     --model <model> \
     --output-format json \
     --dangerously-skip-permissions \
     --append-system-prompt "<SYSTEM>" \
     <désactivation des outils>   # classification pure, aucun accès fichier/tool
   ```
   - `<model>` = `settings.ai.haiku_model` ou `sonnet_model` (accepte alias `haiku`/`sonnet`
     ou ID complet).
   - **Aucun** `--continue` / `--resume`.
   - Le mécanisme exact de désactivation des outils (`--disallowed-tools`/équivalent) est à
     confirmer contre la version du CLI au moment du plan.
4. **Envoie le prompt via stdin**, puis `await asyncio.wait_for(proc.communicate(), timeout)`
   avec `timeout = cli_timeout_ms`.
5. **Parse l'enveloppe JSON** du CLI (`--output-format json` → objet
   `{"result": "...", "usage": {...}, "total_cost_usd": ...}`). Le champ `result`
   devient le `text` de `ClaudeResponse`. Le worker fait ensuite `resp.json()` comme
   aujourd'hui pour extraire son JSON métier.
6. **Métriques :** `usage.input_tokens`/`output_tokens` → `AI_TOKENS` (déjà existant, utile
   même sous abonnement) ; incrémente `AI_CLI_CALLS{outcome=success}`.
7. **Erreurs :**
   - Timeout → kill du process, `AI_CLI_CALLS{outcome=timeout}`, dégradation.
   - Exit non-zéro / auth / quota → log stderr, `AI_CLI_CALLS{outcome=error|quota}`,
     dégradation (retourne un `ClaudeResponse` au texte vide → `resp.json()` lève →
     le worker skippe : haiku score 0, sonnet ne publie pas).
   - **Aucune attente bloquante type AutoDevClaude sur quota.** Pipeline temps réel : on
     skippe et on avance.
8. Libère le sémaphore et nettoie le scratch dir en `finally`.

### Concurrence réelle

Parallélisme total côté abonnement = `nb_réplicas × cli_concurrency` (ex. `HAIKU_REPLICAS=2`).
Le plafond par worker protège la RAM du conteneur (chaque `claude -p` = process Node,
~1–3 s de démarrage, plusieurs centaines de Mo) et les limites de débit de l'abonnement.

## 4. Configuration (`AISettings`)

Ajouts (préfixe env `ANTHROPIC_`) :

```python
transport: str = "api"          # ANTHROPIC_TRANSPORT=cli  → active le CLI
cli_path: str = "claude"        # ANTHROPIC_CLI_PATH
cli_timeout_ms: int = 120000    # ANTHROPIC_CLI_TIMEOUT_MS
cli_concurrency: int = 4        # ANTHROPIC_CLI_CONCURRENCY
```

Conservés : `api_key`, `haiku_model`, `sonnet_model`, `escalation_threshold`.
`max_tokens` devient sans objet en mode CLI (le CLI n'expose pas de plafond de tokens de
sortie équivalent ; le prompt de classification produit déjà un petit JSON).

Le transport par défaut reste `api` → aucun changement de comportement tant que
`ANTHROPIC_TRANSPORT=cli` n'est pas positionné. Rollback = repasser la variable à `api`.

## 5. Docker & authentification

- **`docker/Dockerfile.ai-worker`** (dédié, pour ne pas alourdir les 9 autres services) :
  image de base des services + Node.js + installation globale du CLI
  `@anthropic-ai/claude-code`.
- **`docker-compose.yml`**, pour `ai-worker-haiku` et `ai-worker-sonnet` uniquement :
  - `build` → `docker/Dockerfile.ai-worker`.
  - `volumes` (lecture seule) :
    - `${CLAUDE_DIR}:<HOME>/.claude:ro`
    - `${CLAUDE_CONFIG}:<HOME>/.claude.json:ro`
    - `<HOME>` = home de l'utilisateur du conteneur ; `HOME` réglé en conséquence.
  - `environment` : `ANTHROPIC_TRANSPORT=cli`, `ANTHROPIC_CLI_CONCURRENCY`,
    `ANTHROPIC_CLI_TIMEOUT_MS`, `ANTHROPIC_CLI_PATH`.
- **`.env.example`** : ajout de `CLAUDE_DIR`, `CLAUDE_CONFIG` + les variables ci-dessus.

### ⚠️ Prérequis de vérification (première étape du plan)

Sur le host Windows, `C:\Users\<you>\.claude` doit contenir le **fichier de credentials
OAuth** (pas seulement un keychain OS), sinon le `claude` du conteneur Linux ne sera pas
authentifié. Étape de validation : lancer `claude -p --output-format json` dans le conteneur
avec les volumes montés et confirmer une réponse authentifiée avant tout le reste.

## 6. Observabilité & gestion d'erreur

- Nouveau compteur `AI_CLI_CALLS{service, model, outcome}` avec
  `outcome ∈ {success, timeout, error, quota}`.
- `AI_TOKENS` continue d'être alimenté depuis l'enveloppe CLI.
- Toute erreur d'exécution CLI → log + métrique + dégradation ; jamais de crash du worker
  ni d'arrêt du consumer Kafka.

## 7. Tests

- **Tests unitaires `CliTransport` avec un faux CLI** (petit script exécutable qui lit
  stdin et écrit une enveloppe JSON canonique) :
  - argv correct : bon `--model`, présence de `--output-format json`, **absence** de
    `--continue` / `--resume`.
  - prompt bien transmis via stdin.
  - parsing de l'enveloppe → `ClaudeResponse.text` = `result`.
  - alimentation de `AI_TOKENS` depuis `usage`.
  - timeout (faux CLI qui dort) → kill + dégradation + `outcome=timeout`.
  - exit non-zéro → dégradation + `outcome=error`.
  - **plafond du sémaphore** : N appels simultanés ne lancent jamais plus de
    `cli_concurrency` process en même temps.
- Les tests existants (stub / api) restent verts (transport par défaut inchangé).
- **Aucun appel abonnement réel en CI.**

## 8. Hors périmètre (YAGNI)

- **Sidecar CLI (approche C)** — service dédié centralisant l'exécution ; évolution future
  si le nombre de réplicas grandit fortement.
- **Attente bloquante / auto-resume sur quota** (style AutoDevClaude) — inadapté à un
  pipeline temps réel.
- **Collecteur X/Twitter** et toute évolution du système de sentiment — sujet séparé, non
  lié au transport CLI.
- Logique métier, prompts et schémas d'événements des workers — inchangés.
