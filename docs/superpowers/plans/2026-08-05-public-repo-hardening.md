# Passage du dépôt en public — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre `nbeny/BotTrading` public sans exposer de credential ni d'infrastructure, avec un Environment GitHub comme source de vérité de la configuration VPS et une licence propriétaire interdisant tout usage.

**Architecture:** Sept étapes ordonnées, dont une bloquante (Task 5 : le déploiement doit passer au vert) avant toute opération irréversible. Les secrets migrent vers un Environment `production` ; `deploy.yml` gagne une étape qui régénère `/opt/bottrading/.env` à chaque déploiement, en écrivant le fichier depuis `os.environ` en Python plutôt que par interpolation shell. La réécriture d'historique (`git filter-repo`) arrive en dernier, une seule fois.

**Tech Stack:** `gh` CLI 2.96, GitHub Actions, `git-filter-repo` (module Python), Docker Compose sur VPS, Python 3.12.

**Spec:** `docs/superpowers/specs/2026-08-05-public-repo-hardening-design.md`

---

## ⚠️ Règles valables pour tout le plan

1. **Ne jamais afficher une valeur de secret dans une sortie de commande.** Pas de `cat .env`, pas de `echo $SECRET`, pas de `gh secret set` avec la valeur en argument (elle atterrirait dans l'historique du shell). Toujours passer par `--body-file -` sur stdin.
2. **Task 5 est un point d'arrêt.** Si le déploiement échoue, ne pas continuer vers Task 6. Diagnostiquer d'abord.
3. **Task 6 est irréversible pour les SHA.** Ne la lancer qu'après Task 5 verte.
4. La branche de travail est `master`. Les commits des Tasks 1–4 sont poussés normalement.
5. **L'IP du VPS n'apparaît nulle part dans ce plan, volontairement.** Ce document est
   commité dans `docs/` : y écrire l'IP en clair ferait que la Task 2 le scrube lui-même et
   corrompe l'instruction de remplacement de la Task 6. L'IP est donc capturée une fois en
   Task 2 dans `$IPFILE` (hors dépôt, jamais suivi) et relue ensuite. **Toute étape qui en a
   besoin commence par `IPFILE="$HOME/.bottrading-vps-ip"`.**

---

## Structure des fichiers

| Fichier | Rôle | Task |
|---|---|---|
| `LICENSE` | Créer — licence propriétaire, aucun droit accordé | 1 |
| `README.md` | Modifier — section **Usage** en tête, avant la doc technique | 1 |
| `docs/superpowers/**` (9 fichiers) | Modifier — IP VPS → `<VPS_HOST>` | 2 |
| `tests/test_api_gateway_auth.py:64` | Modifier — nom d'admin réel → `operator` | 2 |
| `.env.example:117-118` | Modifier — chemins Windows personnels → génériques | 2 |
| `scripts/render_vps_env.py` | Créer — rend le `.env` VPS depuis `os.environ` | 4 |
| `.github/workflows/deploy.yml` | Modifier — `environment: production` + étape de rendu | 4 |
| `docs/superpowers/plans/2026-08-05-rotation-checklist.md` | Créer — checklist de rotation | 8 |

---

## Task 1 : LICENSE propriétaire et mention d'usage

**Files:**
- Create: `LICENSE`
- Modify: `README.md` (insérer après la ligne 14, avant le `---` qui précède la section 1)

- [ ] **Step 1: Créer le fichier `LICENSE`**

```
Copyright (c) 2026 nbeny. Tous droits réservés. / All rights reserved.

================================================================================
FRANÇAIS
================================================================================

LICENCE PROPRIÉTAIRE — AUCUN DROIT ACCORDÉ

Ce dépôt est rendu consultable publiquement à des fins de transparence et de
démonstration uniquement. Sa publication ne constitue en aucun cas une mise à
disposition, une licence libre, une licence open source, ni une renonciation à
un quelconque droit.

Aucun droit n'est accordé. En particulier, et sans que cette liste soit
limitative, il est INTERDIT, à titre commercial comme non commercial :

  - d'utiliser, d'exécuter ou de déployer tout ou partie de ce logiciel ;
  - de le copier, reproduire, télécharger ou stocker, hors de la mise en cache
    technique strictement nécessaire à sa consultation ;
  - de le modifier, l'adapter, le traduire ou d'en créer des œuvres dérivées ;
  - de le distribuer, republier, sous-licencier, vendre, louer ou transférer ;
  - de l'incorporer, en tout ou partie, dans un autre projet, produit ou
    service, qu'il soit gratuit ou payant ;
  - de l'utiliser comme donnée d'entraînement, de mise au point ou d'évaluation
    d'un modèle d'apprentissage automatique ;
  - d'exploiter les méthodes, architectures ou concepts qu'il décrit.

Seule la consultation du code au moyen de l'interface de GitHub est tolérée.

Toute autre utilisation requiert une autorisation écrite préalable et expresse
du titulaire des droits. Aucune autorisation tacite ne peut être déduite de
l'absence de réponse.

ABSENCE DE GARANTIE. Le logiciel est fourni « en l'état », sans garantie
d'aucune sorte, expresse ou implicite. Il met en œuvre des opérations de
trading automatisé comportant un risque de perte financière. Le titulaire des
droits ne saurait être tenu responsable d'un quelconque dommage, perte ou
préjudice, direct ou indirect, résultant de sa consultation ou de son usage,
autorisé ou non.

================================================================================
ENGLISH (courtesy translation — the French text prevails)
================================================================================

PROPRIETARY LICENSE — NO RIGHTS GRANTED

This repository is made publicly viewable for transparency and demonstration
purposes only. Its publication is not a grant of rights, not an open source or
free software license, and not a waiver of any right whatsoever.

No rights are granted. In particular, and without limitation, the following are
PROHIBITED, for commercial and non-commercial purposes alike:

  - using, running or deploying this software in whole or in part;
  - copying, reproducing, downloading or storing it, beyond the technical
    caching strictly required to view it;
  - modifying, adapting, translating it or creating derivative works from it;
  - distributing, republishing, sublicensing, selling, renting or transferring
    it;
  - incorporating it, in whole or in part, into any other project, product or
    service, whether free or paid;
  - using it as training, fine-tuning or evaluation data for any machine
    learning model;
  - exploiting the methods, architectures or concepts it describes.

Only viewing the code through the GitHub interface is tolerated.

Any other use requires the prior express written permission of the rights
holder. No permission may be inferred from a lack of response.

NO WARRANTY. The software is provided "as is", without warranty of any kind,
express or implied. It performs automated trading operations carrying a risk of
financial loss. The rights holder shall not be liable for any damage, loss or
harm, direct or indirect, arising from viewing or using it, whether authorized
or not.
```

- [ ] **Step 2: Insérer la section Usage dans `README.md`**

Le README commence par le titre, un bloc `>` de description, deux paragraphes, puis une ligne `---` avant `## 1. Vue d'ensemble de l'architecture`. Insérer **juste avant** ce `---` :

```markdown
---

## ⚠️ Usage — tous droits réservés

Ce dépôt est **consultable, pas utilisable**. Il est publié pour la transparence et
la démonstration, sous une **licence propriétaire qui n'accorde aucun droit** :
pas d'usage commercial, pas d'usage non commercial, pas de copie, pas de
modification, pas de redistribution, pas d'œuvre dérivée, pas d'entraînement de
modèle. Voir [`LICENSE`](LICENSE).

Le fait que GitHub permette techniquement de forker ce dépôt ne vaut pas
autorisation. Toute utilisation requiert un accord écrit préalable.
```

- [ ] **Step 3: Vérifier que le README rend correctement**

Run: `head -35 README.md`
Expected: la section `## ⚠️ Usage` apparaît avant `## 1. Vue d'ensemble de l'architecture`, séparée par des `---`.

- [ ] **Step 4: Commit**

```bash
git add LICENSE README.md
git commit -m "docs(license): publier le depot sans en autoriser l'usage

Le depot devient consultable, pas utilisable. La licence n'accorde aucun
droit, ni commercial ni non commercial. GitHub permettra le fork malgre
tout -- ses CGU l'imposent a tout depot public -- donc le README le dit
explicitement plutot que de laisser croire que la licence l'empeche."
```

---

## Task 2 : Scrub de l'IP, du nom d'admin et des chemins personnels

**Files:**
- Modify: 9 fichiers sous `docs/superpowers/` (IP → `<VPS_HOST>`)
- Modify: `tests/test_api_gateway_auth.py:64`
- Modify: `.env.example:117-118`

- [ ] **Step 1: Capturer l'IP dans `$IPFILE`, hors du dépôt**

L'IP est la seule IPv4 publique présente dans `docs/`. On la détecte, on la stocke hors du
dépôt, et plus aucune étape n'a besoin de l'écrire en clair.

```bash
IPFILE="$HOME/.bottrading-vps-ip"
git grep -hoE '\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b' -- docs/ \
  | grep -vE '^(0\.|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|255\.)' \
  | sort | uniq -c | sort -rn > /tmp/ipcount.txt
cat /tmp/ipcount.txt
awk 'NR==1 {print $2}' /tmp/ipcount.txt > "$IPFILE"
echo "capturee, $(wc -c < "$IPFILE") octets"
```

Expected: une seule ligne dans `/tmp/ipcount.txt` (un seul candidat, ~30 occurrences), et
`capturee, 14 octets`. **S'il y a plusieurs candidats, s'arrêter** : le design n'en prévoit
qu'un, et scruber la mauvaise valeur casserait de la documentation.

- [ ] **Step 2: Confirmer la liste exacte des fichiers portant l'IP**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
git grep -lF "$(cat "$IPFILE")" | tee /tmp/ipfiles.txt | wc -l
```

Expected: `9`, et les 9 chemins tous sous `docs/superpowers/`. Si un fichier hors `docs/`
apparaît, s'arrêter et le signaler — le design ne le prévoit pas.

- [ ] **Step 3: Remplacer l'IP dans le working tree**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
while read -r f; do
  python -c "
import sys, pathlib
ip = pathlib.Path(sys.argv[2]).read_text().strip()
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text(encoding='utf-8').replace(ip, '<VPS_HOST>'), encoding='utf-8', newline='')
" "$f" "$IPFILE"
done < /tmp/ipfiles.txt
```

- [ ] **Step 4: Vérifier qu'il ne reste aucune occurrence**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
git grep -cF "$(cat "$IPFILE")" ; echo "exit=$?"
```

Expected: aucune ligne de résultat, `exit=1`.

- [ ] **Step 5: Neutraliser le nom d'admin dans le test**

Le nom en dur ligne 64 de `tests/test_api_gateway_auth.py` est la valeur réelle de
`CONTROL_ADMIN_USER`. Comme pour l'IP, on ne l'écrit pas dans ce document : on la lit
depuis `.env` et on la remplace par `operator`, un identifiant de fixture arbitraire. Le
test ne vérifie que la signature et le rôle, jamais la valeur de `sub` — le remplacer ne
change rien à ce qui est testé.

```bash
python - <<'PY'
import pathlib, re, sys

env = dict(
    line.split("=", 1)
    for line in pathlib.Path(".env").read_text(encoding="utf-8", errors="ignore").splitlines()
    if line.strip() and not line.startswith("#") and "=" in line
)
admin = env.get("CONTROL_ADMIN_USER", "").strip().strip('"').strip("'")
if not admin:
    print("FATAL: CONTROL_ADMIN_USER absent de .env"); sys.exit(1)

p = pathlib.Path("tests/test_api_gateway_auth.py")
t = p.read_text(encoding="utf-8")
n = t.count(f'"{admin}"')
p.write_text(t.replace(f'"{admin}"', '"operator"'), encoding="utf-8", newline="")
print(f"{n} occurrence(s) remplacee(s) par \"operator\"")
PY
```

Expected: `1 occurrence(s) remplacee(s) par "operator"`. Si le compte est `0`, le nom en dur
n'est pas celui de `.env` : l'inspecter à la main avant de continuer.

- [ ] **Step 6: Généraliser les chemins Windows personnels**

Lignes 117–118 de `.env.example`. Le nom d'utilisateur est remplacé par `<you>` sans être
écrit ici non plus.

```bash
python - <<'PY'
import pathlib, re
p = pathlib.Path(".env.example")
t = p.read_text(encoding="utf-8")
t2, n = re.subn(r"(CLAUDE_(?:DIR|CONFIG)=C:\\Users\\)[^\\\r\n]+", r"\1<you>", t)
p.write_text(t2, encoding="utf-8", newline="")
print(f"{n} chemin(s) generalise(s)")
PY
grep -n "CLAUDE_DIR\|CLAUDE_CONFIG" .env.example
```

Expected: `2 chemin(s) generalise(s)`, puis deux lignes affichant `C:\Users\<you>\.claude` et
`C:\Users\<you>\.claude.json`.

- [ ] **Step 7: Vérifier qu'aucune trace ne subsiste au niveau de HEAD**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
ADMIN=$(grep '^CONTROL_ADMIN_USER=' .env | cut -d= -f2- | tr -d '"'"'"' \r')
git grep -nE "$(cat "$IPFILE")|$ADMIN|Users.$(whoami)" ; echo "exit=$?"
```

Expected: aucune ligne, `exit=1`. Les trois motifs sont construits à l'exécution : les
écrire en clair ici les ferait apparaître dans ce document, qui est lui-même suivi et
public à la fin du parcours.

- [ ] **Step 8: Faire tourner le test modifié**

Run: `python -m pytest tests/test_api_gateway_auth.py -q`
Expected: tous les tests passent (notamment `test_read_route_accepts_a_properly_signed_token`).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore(privacy): retirer l'IP du VPS et l'identifiant d'admin

Un depot public expose son infrastructure autant que son code. L'IP
rendait la machine directement joignable sans passer par le DNS, et le
test portait la moitie d'un couple de login reel. Le domaine, lui, reste :
il est deja public en DNS et le retirer casserait deploy.yml."
```

---

## Task 3 : Peupler l'Environment `production`

Aucun Environment n'existe aujourd'hui (`gh api repos/nbeny/BotTrading/environments` → `total_count: 0`). Les 3 secrets de dépôt (`VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`) restent où ils sont — ils servent à joindre le VPS, pas à le configurer.

**Files:** aucun fichier du dépôt n'est modifié. Cette tâche n'agit que sur GitHub.

- [ ] **Step 1: Créer l'Environment `production`**

```bash
gh api -X PUT repos/nbeny/BotTrading/environments/production --silent
gh api repos/nbeny/BotTrading/environments --jq '.environments[].name'
```

Expected: `production`

- [ ] **Step 2: Pousser les 15 secrets depuis `.env`, sans jamais afficher de valeur**

Le script lit `.env`, et pour chaque clé attendue passe la valeur à `gh` **sur stdin**
(`--body-file -`), ce qui la garde hors de la ligne de commande et donc hors de
l'historique du shell et de la liste des processus.

```bash
python - <<'PY'
import os, subprocess, sys, pathlib

# nom dans .env -> nom du secret GitHub
SECRETS = {
    "DB_PASSWORD": "DB_PASSWORD",
    "JWT_SECRET": "JWT_SECRET",
    "CONTROL_ADMIN_USER": "CONTROL_ADMIN_USER",
    "CONTROL_ADMIN_PASSWORD": "CONTROL_ADMIN_PASSWORD",
    "CLAUDE_CODE_OAUTH_TOKEN": "CLAUDE_CODE_OAUTH_TOKEN",
    "TURNSTILE_SECRET_KEY": "TURNSTILE_SECRET_KEY",
    "NEWSDATA_API_KEY": "NEWSDATA_API_KEY",
    "NEYNAR_API_KEY": "NEYNAR_API_KEY",
    "YOUTUBE_API_KEY": "YOUTUBE_API_KEY",
    "BLUESKY_IDENTIFIER": "BLUESKY_IDENTIFIER",
    "BLUESKY_APP_PASSWORD": "BLUESKY_APP_PASSWORD",
    "TELEGRAM_API_ID": "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH": "TELEGRAM_API_HASH",
    "TELEGRAM_SESSION": "TELEGRAM_SESSION",
    # GitHub refuse tout secret prefixe GITHUB_ ; remappe au rendu du .env
    "GITHUB_TOKEN": "GH_COLLECTOR_TOKEN",
}

env = {}
for line in pathlib.Path(".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip().strip('"').strip("'")

missing = [k for k in SECRETS if not env.get(k)]
if missing:
    print("ABSENT ou vide dans .env :", ", ".join(missing))
    print("Corriger .env avant de continuer.")
    sys.exit(1)

for local, remote in SECRETS.items():
    subprocess.run(
        ["gh", "secret", "set", remote, "--env", "production",
         "-R", "nbeny/BotTrading", "--body-file", "-"],
        input=env[local].encode(), check=True,
    )
    print(f"  set {remote}  ({len(env[local])} caracteres)")
print(f"\n{len(SECRETS)} secrets pousses.")
PY
```

Expected: 15 lignes `set <NOM> (N caracteres)` puis `15 secrets pousses.` — des longueurs, jamais des valeurs.

- [ ] **Step 3: Pousser les 13 variables non sensibles**

`TRADING_MODE` va ici et non dans les secrets : c'est la valeur qui décide si le bot
passe de vrais ordres, elle doit rester lisible dans l'UI GitHub.

```bash
python - <<'PY'
import subprocess, pathlib, sys

VARS = [
    "LOG_LEVEL", "ENVIRONMENT", "DB_USER", "DB_NAME", "KAFKA_HEAP_OPTS",
    "HAIKU_REPLICAS", "ANTHROPIC_CLI_CONCURRENCY", "OTEL_TRACING_ENABLED",
    "TRADING_MODE", "GITHUB_POLL_INTERVAL", "GITHUB_MAX_REFRESH_PER_CYCLE",
    "GITHUB_UNIVERSE_SIZE", "GITHUB_LISTS_REFRESH_HOURS",
]

env = {}
for line in pathlib.Path(".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip().strip('"').strip("'")

missing = [k for k in VARS if k not in env]
if missing:
    print("ABSENT de .env :", ", ".join(missing)); sys.exit(1)

if env["TRADING_MODE"] != "dry_run":
    print(f"STOP: TRADING_MODE vaut {env['TRADING_MODE']!r} et pas 'dry_run'.")
    print("Confirmer que c'est voulu avant de continuer.")
    sys.exit(1)

for k in VARS:
    subprocess.run(
        ["gh", "variable", "set", k, "--env", "production",
         "-R", "nbeny/BotTrading", "--body-file", "-"],
        input=env[k].encode(), check=True,
    )
    print(f"  set {k}={env[k]}")
PY
```

Expected: 13 lignes `set K=valeur` (ces valeurs-là ne sont pas sensibles, les afficher est voulu), et un arrêt net si `TRADING_MODE` n'est pas `dry_run`.

- [ ] **Step 4: Vérifier le contenu de l'Environment**

Run:
```bash
gh secret list --env production -R nbeny/BotTrading | wc -l
gh variable list --env production -R nbeny/BotTrading | wc -l
```
Expected: `15` et `13`.

---

## Task 4 : `deploy.yml` régénère le `.env` du VPS

**Files:**
- Create: `scripts/render_vps_env.py`
- Modify: `.github/workflows/deploy.yml` (job `deploy`)

- [ ] **Step 1: Créer `scripts/render_vps_env.py`**

Le rendu se fait en Python depuis `os.environ` — jamais par interpolation `${{ }}` dans du
shell. Une valeur contenant `"`, `$`, une apostrophe ou un retour ligne casserait le fichier
ou s'échapperait dans les logs du runner.

```python
#!/usr/bin/env python3
"""Rend le /opt/bottrading/.env du VPS depuis l'environnement du runner.

Appele par .github/workflows/deploy.yml. Les valeurs arrivent par le bloc `env:`
de l'etape, donc par os.environ : aucune n'est interpolee dans du shell, ou un
guillemet ou un dollar suffirait a casser le fichier ou a fuiter dans les logs.

Ecrit exactement les cles que porte le .env de production. Les ~53 autres
variables lues par docker-compose.vps.yml gardent leur defaut inline.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Cles rendues, dans l'ordre du fichier. La valeur est le nom de la variable
# d'environnement du runner, qui differe pour GITHUB_TOKEN : GitHub refuse tout
# secret prefixe GITHUB_, donc le PAT du collector voyage sous GH_COLLECTOR_TOKEN.
KEYS: list[tuple[str, str]] = [
    ("DB_PASSWORD", "DB_PASSWORD"),
    ("JWT_SECRET", "JWT_SECRET"),
    ("CONTROL_ADMIN_USER", "CONTROL_ADMIN_USER"),
    ("CONTROL_ADMIN_PASSWORD", "CONTROL_ADMIN_PASSWORD"),
    ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ("REGISTRY", "REGISTRY"),
    ("TAG", "TAG"),
    ("LOG_LEVEL", "LOG_LEVEL"),
    ("ENVIRONMENT", "ENVIRONMENT"),
    ("DB_USER", "DB_USER"),
    ("DB_NAME", "DB_NAME"),
    ("KAFKA_HEAP_OPTS", "KAFKA_HEAP_OPTS"),
    ("HAIKU_REPLICAS", "HAIKU_REPLICAS"),
    ("ANTHROPIC_CLI_CONCURRENCY", "ANTHROPIC_CLI_CONCURRENCY"),
    ("OTEL_TRACING_ENABLED", "OTEL_TRACING_ENABLED"),
    ("TRADING_MODE", "TRADING_MODE"),
    ("NEWSDATA_API_KEY", "NEWSDATA_API_KEY"),
    ("NEYNAR_API_KEY", "NEYNAR_API_KEY"),
    ("YOUTUBE_API_KEY", "YOUTUBE_API_KEY"),
    ("BLUESKY_APP_PASSWORD", "BLUESKY_APP_PASSWORD"),
    ("BLUESKY_IDENTIFIER", "BLUESKY_IDENTIFIER"),
    ("TURNSTILE_SECRET_KEY", "TURNSTILE_SECRET_KEY"),
    ("TELEGRAM_API_ID", "TELEGRAM_API_ID"),
    ("TELEGRAM_API_HASH", "TELEGRAM_API_HASH"),
    ("TELEGRAM_SESSION", "TELEGRAM_SESSION"),
    ("GITHUB_TOKEN", "GH_COLLECTOR_TOKEN"),
    ("GITHUB_POLL_INTERVAL", "GITHUB_POLL_INTERVAL"),
    ("GITHUB_MAX_REFRESH_PER_CYCLE", "GITHUB_MAX_REFRESH_PER_CYCLE"),
    ("GITHUB_UNIVERSE_SIZE", "GITHUB_UNIVERSE_SIZE"),
    ("GITHUB_LISTS_REFRESH_HOURS", "GITHUB_LISTS_REFRESH_HOURS"),
]

# Sans elles la stack demarre mais ne fait rien d'utile : mieux vaut echouer ici,
# ou le message est lisible, que sur le VPS ou il faudrait lire des logs docker.
REQUIRED = {
    "DB_PASSWORD",
    "JWT_SECRET",
    "CONTROL_ADMIN_USER",
    "CONTROL_ADMIN_PASSWORD",
    "REGISTRY",
    "TAG",
}


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".env.render")

    missing = [dest for dest, src in KEYS if dest in REQUIRED and not os.environ.get(src)]
    if missing:
        print(f"FATAL: variables requises absentes ou vides: {', '.join(missing)}", file=sys.stderr)
        return 1

    lines = ["# Genere par .github/workflows/deploy.yml -- ne pas editer a la main.", ""]
    empty: list[str] = []
    for dest, src in KEYS:
        value = os.environ.get(src, "")
        if not value:
            empty.append(dest)
        # Pas de guillemets : docker-compose les prendrait pour une partie de la
        # valeur. Un retour ligne casserait le format, on le refuse plutot que de
        # produire un fichier silencieusement tronque.
        if "\n" in value or "\r" in value:
            print(f"FATAL: {dest} contient un retour ligne", file=sys.stderr)
            return 1
        lines.append(f"{dest}={value}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    out.chmod(0o600)

    # Des noms, jamais des valeurs.
    print(f"rendu {out} : {len(KEYS)} cles")
    if empty:
        print(f"cles vides (source desactivee) : {', '.join(empty)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Tester le script en local, avec de fausses valeurs**

```bash
cd "$(git rev-parse --show-toplevel)"
DB_PASSWORD='p@ss"with$pecial' JWT_SECRET=x CONTROL_ADMIN_USER=u \
CONTROL_ADMIN_PASSWORD=p REGISTRY=ghcr.io/nbeny TAG=abc123 \
TELEGRAM_SESSION='1BJWap$notreal' \
python scripts/render_vps_env.py /tmp/env.test && cat /tmp/env.test | head -8
```

Expected: `rendu /tmp/env.test : 30 cles`, une liste de clés vides, et un fichier dont la
première ligne de données est `DB_PASSWORD=p@ss"with$pecial` — **non échappée, non tronquée**.
C'est le comportement attendu : docker-compose lit la valeur littérale jusqu'au retour ligne.

- [ ] **Step 3: Vérifier que l'absence d'une variable requise fait échouer le script**

```bash
JWT_SECRET=x CONTROL_ADMIN_USER=u CONTROL_ADMIN_PASSWORD=p \
REGISTRY=r TAG=t python scripts/render_vps_env.py /tmp/env.fail; echo "exit=$?"
```

Expected: `FATAL: variables requises absentes ou vides: DB_PASSWORD` et `exit=1`.

- [ ] **Step 4: Nettoyer les fichiers de test**

Run: `rm -f /tmp/env.test /tmp/env.fail`

- [ ] **Step 5: Rattacher le job `deploy` à l'Environment**

Dans `.github/workflows/deploy.yml`, dans le job `deploy`, ajouter `environment: production`
juste après `runs-on: ubuntu-latest` :

```yaml
  deploy:
    needs: images
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    runs-on: ubuntu-latest
    environment: production
    permissions:
      contents: read
      packages: read
```

- [ ] **Step 6: Ajouter l'étape de rendu et de transfert**

Dans le job `deploy`, **entre** l'étape `Set up SSH` et l'étape `Sync deploy files to VPS`,
insérer :

```yaml
      - name: Render VPS .env from the production environment
        env:
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
          JWT_SECRET: ${{ secrets.JWT_SECRET }}
          CONTROL_ADMIN_USER: ${{ secrets.CONTROL_ADMIN_USER }}
          CONTROL_ADMIN_PASSWORD: ${{ secrets.CONTROL_ADMIN_PASSWORD }}
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          TURNSTILE_SECRET_KEY: ${{ secrets.TURNSTILE_SECRET_KEY }}
          NEWSDATA_API_KEY: ${{ secrets.NEWSDATA_API_KEY }}
          NEYNAR_API_KEY: ${{ secrets.NEYNAR_API_KEY }}
          YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}
          BLUESKY_IDENTIFIER: ${{ secrets.BLUESKY_IDENTIFIER }}
          BLUESKY_APP_PASSWORD: ${{ secrets.BLUESKY_APP_PASSWORD }}
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
          TELEGRAM_SESSION: ${{ secrets.TELEGRAM_SESSION }}
          GH_COLLECTOR_TOKEN: ${{ secrets.GH_COLLECTOR_TOKEN }}
          LOG_LEVEL: ${{ vars.LOG_LEVEL }}
          ENVIRONMENT: ${{ vars.ENVIRONMENT }}
          DB_USER: ${{ vars.DB_USER }}
          DB_NAME: ${{ vars.DB_NAME }}
          KAFKA_HEAP_OPTS: ${{ vars.KAFKA_HEAP_OPTS }}
          HAIKU_REPLICAS: ${{ vars.HAIKU_REPLICAS }}
          ANTHROPIC_CLI_CONCURRENCY: ${{ vars.ANTHROPIC_CLI_CONCURRENCY }}
          OTEL_TRACING_ENABLED: ${{ vars.OTEL_TRACING_ENABLED }}
          TRADING_MODE: ${{ vars.TRADING_MODE }}
          GITHUB_POLL_INTERVAL: ${{ vars.GITHUB_POLL_INTERVAL }}
          GITHUB_MAX_REFRESH_PER_CYCLE: ${{ vars.GITHUB_MAX_REFRESH_PER_CYCLE }}
          GITHUB_UNIVERSE_SIZE: ${{ vars.GITHUB_UNIVERSE_SIZE }}
          GITHUB_LISTS_REFRESH_HOURS: ${{ vars.GITHUB_LISTS_REFRESH_HOURS }}
          REGISTRY: ${{ env.REGISTRY }}
          TAG: ${{ github.sha }}
        run: |
          umask 077
          python3 scripts/render_vps_env.py .env.render

      - name: Back up and install the .env on the VPS
        run: |
          set -euo pipefail
          HOST="${{ secrets.VPS_USER }}@${{ secrets.VPS_HOST }}"
          # Le .env courant du VPS a pu recevoir une cle a la main. On le garde,
          # et on affiche le diff des NOMS de cles -- jamais des valeurs.
          ssh "$HOST" 'set -e; cd /opt/bottrading
            if [ -f .env ]; then
              cp -p .env ".env.bak.${{ github.sha }}"
              grep -oE "^[A-Za-z_][A-Za-z0-9_]*" .env | sort -u > /tmp/keys.old
            else
              : > /tmp/keys.old
            fi'
          scp .env.render "$HOST:/opt/bottrading/.env.new"
          ssh "$HOST" 'set -e; cd /opt/bottrading
            grep -oE "^[A-Za-z_][A-Za-z0-9_]*" .env.new | sort -u > /tmp/keys.new
            echo "== cles retirees par ce deploiement =="
            comm -23 /tmp/keys.old /tmp/keys.new || true
            echo "== cles ajoutees par ce deploiement =="
            comm -13 /tmp/keys.old /tmp/keys.new || true
            mv .env.new .env
            chmod 600 .env
            rm -f /tmp/keys.old /tmp/keys.new'
          rm -f .env.render
```

- [ ] **Step 7: Vérifier que le YAML est valide**

Run: `python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/deploy.yml',encoding='utf-8')); print('jobs:', list(d['jobs'])); print('env deploy:', d['jobs']['deploy'].get('environment')); print('steps:', [s.get('name','uses:'+s.get('uses','')) for s in d['jobs']['deploy']['steps']])"`

Expected: `jobs: ['test', 'images', 'deploy']`, `env deploy: production`, et la liste des étapes
montrant `Render VPS .env…` et `Back up and install the .env on the VPS` **avant**
`Sync deploy files to VPS`.

- [ ] **Step 8: Commit et push**

```bash
git add scripts/render_vps_env.py .github/workflows/deploy.yml
git commit -m "ci(deploy): faire de GitHub la source de verite du .env du VPS

deploy-vps.sh lit un /opt/bottrading/.env pose a la main : des secrets
stockes dans GitHub y restaient donc inertes. Le workflow le regenere
desormais a chaque deploiement, depuis l'Environment production.

Le rendu passe par Python et os.environ, pas par une interpolation
\${{ }} dans du shell : un guillemet ou un dollar dans une valeur y
casserait le fichier ou s'echapperait dans les logs. L'ancien .env est
sauvegarde et le diff des NOMS de cles est journalise -- c'est le seul
moyen de voir qu'une cle avait ete posee a la main sur la machine.

Le PAT du collector voyage sous GH_COLLECTOR_TOKEN : GitHub refuse tout
secret prefixe GITHUB_."
git push origin master
```

---

## Task 5 : ⛔ Point de contrôle — le déploiement doit passer au vert

**Cette tâche ne modifie rien. Elle vérifie.** Ne pas enchaîner sur la Task 6 tant qu'elle
n'est pas concluante : l'historique est encore intact ici, donc le retour arrière est trivial.

- [ ] **Step 1: Suivre le run déclenché par le push**

Run: `gh run watch -R nbeny/BotTrading $(gh run list -R nbeny/BotTrading -L 1 --json databaseId --jq '.[0].databaseId') --exit-status`
Expected: le run se termine en succès. Les trois jobs `test`, `images`, `deploy` passent.

- [ ] **Step 2: Lire le diff des clés dans les logs**

Run: `gh run view -R nbeny/BotTrading --log --job "$(gh run list -R nbeny/BotTrading -L 1 --json databaseId --jq '.[0].databaseId')" 2>/dev/null | grep -A5 "cles retirees\|cles ajoutees"`

Expected: **une seule clé retirée, `TURNSTILE_SITE_KEY`.** C'est attendu et sans effet :
`docker-compose.vps.yml` ne la lit jamais (seul `TURNSTILE_SECRET_KEY` y figure), la moitié
publique étant injectée au build par `deploy.yml`. Elle traînait dans le `.env` sans être
utilisée.

**Toute autre clé sous « retirees » est un signal d'arrêt** : le VPS portait une
configuration que le `.env` local ignore. Récupérer `/opt/bottrading/.env.bak.<sha>` sur la
machine, ajouter les clés manquantes à l'Environment et à la liste `KEYS` de
`scripts/render_vps_env.py`, puis relancer le déploiement.

- [ ] **Step 3: Vérifier que la stack tourne toujours**

Run: `curl -sS -o /dev/null -w "%{http_code}\n" https://crypto.nbeny.fr/`
Expected: `200` (ou `3xx` vers `/login`).

- [ ] **Step 4: Vérifier que le mode de trading n'a pas bougé**

Run: `gh variable list --env production -R nbeny/BotTrading | grep TRADING_MODE`
Expected: `TRADING_MODE  dry_run  …`

> **Si l'une de ces vérifications échoue, s'arrêter ici et diagnostiquer.** Le retour arrière
> est `git revert` du commit de la Task 4 puis push — le VPS retrouve son `.env` via
> `/opt/bottrading/.env.bak.<sha>`.

---

## Task 6 : Réécriture d'historique

Irréversible sur les SHA. À ne lancer qu'après une Task 5 verte.

**Files:** aucun fichier modifié dans le working tree — l'opération porte sur les objets git.

- [ ] **Step 1: Sauvegarder le dépôt avant toute chose**

```bash
cd "$(git rev-parse --show-toplevel)/.."
tar -czf BotTrading-backup-$(date +%Y%m%d-%H%M%S).tar.gz --exclude=node_modules --exclude=.next BotTrading/.git
ls -lh BotTrading-backup-*.tar.gz
```

Expected: une archive listée, de taille non nulle. C'est le filet si filter-repo se passe mal.

- [ ] **Step 2: Vérifier que les 4 worktrees sont propres**

```bash
cd "$(git rev-parse --show-toplevel)"
for w in .claude/worktrees/telegram-collector .worktrees/github-ingestion \
         .worktrees/market-data-foundation .worktrees/normalization-core; do
  echo "--- $w"; git -C "$w" status --porcelain
done
```

Expected: aucune sortie sous chaque en-tête. **Si un worktree a du travail non commité,
s'arrêter** : le commiter ou le stasher d'abord.

- [ ] **Step 3: Démonter les worktrees**

filter-repo réécrit toutes les refs ; les worktrees pointeraient ensuite sur des commits
morts.

```bash
git worktree remove .claude/worktrees/telegram-collector
git worktree remove .worktrees/github-ingestion
git worktree remove .worktrees/market-data-foundation
git worktree remove .worktrees/normalization-core
git worktree list
```

Expected: seule la racine du dépôt reste listée.

- [ ] **Step 4: Écrire le fichier de remplacements**

Le format de `--replace-text` est `ancien==>nouveau`, une règle par ligne. L'ancienne valeur
vient de `$IPFILE`, capturé en Task 2.

```bash
IPFILE="$HOME/.bottrading-vps-ip"
test -s "$IPFILE" || { echo "FATAL: $IPFILE vide ou absent, rejouer Task 2 Step 1"; exit 1; }
printf '%s==><VPS_HOST>\n' "$(cat "$IPFILE")" > /tmp/replacements.txt
wc -l /tmp/replacements.txt
```

Expected: `1 /tmp/replacements.txt`. Ne pas afficher le contenu du fichier : il porte l'IP.

- [ ] **Step 5: Lancer filter-repo sur toutes les refs**

`git-filter-repo` n'est pas installé en sous-commande git mais le module Python l'est.
`--force` est requis car le dépôt n'est pas un clone frais ; la sauvegarde du Step 1 couvre
ce risque.

```bash
python -m git_filter_repo --replace-text /tmp/replacements.txt --force
```

Expected: une barre de progression puis `Completely finished after …`.

- [ ] **Step 6: Vérifier que l'IP a disparu de tout l'historique**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
git rev-list --objects --all | wc -l
git rev-list --all | xargs git grep -lF "$(cat "$IPFILE")" 2>/dev/null; echo "exit=$?"
```

Expected: un nombre d'objets non nul, aucun chemin listé, `exit=1`.

- [ ] **Step 7: Remettre le remote (filter-repo le retire par sécurité)**

```bash
git remote add origin git@github.com:nbeny/BotTrading.git || git remote set-url origin git@github.com:nbeny/BotTrading.git
git remote -v
```

Expected: `origin git@github.com:nbeny/BotTrading.git` en fetch et en push.

- [ ] **Step 8: Force-push les 3 refs présentes sur origin**

Seules `master`, `feat/live-read-endpoints` et `worktree-telegram-collector` existent
côté origin — ce sont les seules jamais exposées publiquement.

```bash
git push --force-with-lease origin master
git push --force origin feat/live-read-endpoints
git push --force origin worktree-telegram-collector
```

Expected: trois `forced update`.

> `--force-with-lease` échouera sur `master` si filter-repo a détaché la ref de suivi.
> Dans ce cas, vérifier d'abord que `git log --oneline -3` correspond bien au travail
> attendu, puis utiliser `git push --force origin master`.

- [ ] **Step 9: Vérifier côté GitHub**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
gh api repos/nbeny/BotTrading/contents/docs/superpowers/specs/2026-07-25-vps-deployment-design.md \
  --jq '.content' | base64 -d > /tmp/remote.md
grep -c "VPS_HOST" /tmp/remote.md
grep -cF "$(cat "$IPFILE")" /tmp/remote.md; echo "ip_exit=$?"
rm -f /tmp/remote.md
```

Expected: un compte non nul pour `VPS_HOST`, puis `0` et `ip_exit=1` pour l'IP.

- [ ] **Step 10: Remonter les worktrees**

```bash
git worktree add .worktrees/github-ingestion feat/github-ingestion
git worktree add .worktrees/market-data-foundation feat/market-data-foundation
git worktree add .worktrees/normalization-core feat/normalization-core
git worktree add .claude/worktrees/telegram-collector worktree-telegram-collector
git worktree list
```

Expected: les 5 entrées (racine + 4 worktrees).

---

## Task 7 : Réglages du dépôt et bascule en public

- [ ] **Step 1: Activer secret scanning et push protection**

Gratuits sur un dépôt public. La push protection bloque en amont un futur `git push`
contenant un token — c'est la garantie que l'état propre le reste.

```bash
gh api -X PATCH repos/nbeny/BotTrading \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' \
  --jq '.security_and_analysis'
```

> Sur un dépôt encore privé, GitHub peut refuser ces réglages (Advanced Security requise).
> Si l'appel échoue, passer au Step 2 puis rejouer cette commande une fois le dépôt public.

- [ ] **Step 2: Dernière vérification avant bascule**

```bash
IPFILE="$HOME/.bottrading-vps-ip"
ADMIN=$(grep '^CONTROL_ADMIN_USER=' .env | cut -d= -f2- | tr -d '"'"'"' \r')
git grep -nE "$(cat "$IPFILE")|$ADMIN|Users.$(whoami)" ; echo "scrub_exit=$?"
git ls-files | grep -E '(^|/)\.env$' ; echo "env_tracked_exit=$?"
test -f LICENSE && echo "LICENSE present"
gh secret list --env production -R nbeny/BotTrading | wc -l
```

Expected: `scrub_exit=1`, `env_tracked_exit=1`, `LICENSE present`, `15`.

- [ ] **Step 3: Basculer en public**

```bash
gh repo edit nbeny/BotTrading --visibility public --accept-visibility-change-consequences
gh repo view nbeny/BotTrading --json visibility,licenseInfo --jq '{visibility, license: .licenseInfo.name}'
```

Expected: `{"visibility": "PUBLIC", "license": null}`. Le `null` est attendu : GitHub ne
reconnaît pas les licences propriétaires et n'affichera pas de badge SPDX.

- [ ] **Step 4: Vérifier que rien de sensible n'est servi publiquement**

```bash
curl -sS "https://raw.githubusercontent.com/nbeny/BotTrading/master/.env" -o /dev/null -w "%{http_code}\n"
curl -sS "https://raw.githubusercontent.com/nbeny/BotTrading/master/LICENSE" | head -3
```

Expected: `404` pour `.env`, et les premières lignes de la licence pour `LICENSE`.

- [ ] **Step 5: Rejouer le Step 1 si le secret scanning avait été refusé**

Run: `gh api repos/nbeny/BotTrading --jq '.security_and_analysis'`
Expected: `secret_scanning` et `secret_scanning_push_protection` à `enabled`.

---

## Task 8 : Checklist de rotation

Hors périmètre décidé — les valeurs actuelles ont été poussées telles quelles. Cette tâche
produit le document, elle n'exécute aucune rotation.

**Files:**
- Create: `docs/superpowers/plans/2026-08-05-rotation-checklist.md`

- [ ] **Step 1: Écrire la checklist**

```markdown
# Checklist de rotation des credentials

Le dépôt n'a jamais porté ces valeurs — l'audit du 2026-08-05 sur 562 commits est
formel. Le risque ne vient donc pas du passage en public, mais du fait que
plusieurs ont été collées en clair dans des conversations passées.

Après chaque rotation : `gh secret set <NOM> --env production -R nbeny/BotTrading --body-file -`
(valeur sur stdin, jamais en argument), puis mettre à jour le `.env` local, puis
pousser un commit vide sur `master` pour redéployer.

## Par ordre de gravité

- [ ] **`TELEGRAM_SESSION`** — vaut un accès complet au compte Telegram, pas seulement
      en lecture. Révoquer la session depuis l'app : Réglages → Appareils → terminer
      la session concernée. Puis regénérer via `python scripts/telegram_session.py`.
- [ ] **`TELEGRAM_API_ID` / `TELEGRAM_API_HASH`** — https://my.telegram.org/apps.
      Note : Telegram ne permet pas de regénérer un `api_hash` en libre-service ;
      il faut créer une nouvelle application.
- [ ] **`GH_COLLECTOR_TOKEN`** (PAT GitHub) — https://github.com/settings/tokens →
      révoquer, recréer avec la portée minimale (lecture de dépôts publics uniquement).
- [ ] **`CLAUDE_CODE_OAUTH_TOKEN`** — `claude setup-token` sur une machine connectée.
- [ ] **`CONTROL_ADMIN_PASSWORD`** — mot de passe du terminal web. Le changer invalide
      les sessions en cours.
- [ ] **`JWT_SECRET`** — partagé entre control-api et websocket-gateway. Le changer
      déconnecte tous les clients WebSocket ; ils se reconnectent après re-login.
- [ ] **`DB_PASSWORD`** — Postgres n'est jamais publié hors du réseau docker. Rotation
      moins urgente, mais elle impose un `ALTER USER cmi WITH PASSWORD …` sur le VPS
      **avant** le redéploiement, sinon la stack ne se connecte plus.
- [ ] **`BLUESKY_APP_PASSWORD`** — Bluesky → Réglages → App Passwords → révoquer, recréer.
- [ ] **`NEWSDATA_API_KEY`, `NEYNAR_API_KEY`, `YOUTUBE_API_KEY`** — clés de collecte à
      quota. Une fuite coûte du quota, pas un accès. Rotation opportuniste.
- [ ] **`TURNSTILE_SECRET_KEY`** — dashboard Cloudflare → Turnstile → widget
      crypto.nbeny.fr → rotate. Attention : le gate échoue **fermé**, donc une clé
      invalide bloque tous les logins.

## Non concernés

`KRAKEN_API_KEY` / `KRAKEN_API_SECRET` ne sont pas renseignés (le compte est en spot,
pas en Futures, et `TRADING_MODE=dry_run`). Rien à faire tant qu'ils restent vides.
```

- [ ] **Step 2: Commit et push**

```bash
git add docs/superpowers/plans/2026-08-05-rotation-checklist.md
git commit -m "docs(security): lister les credentials a regenerer

Aucun n'a fuite par le depot -- l'audit des 562 commits est formel. Ils
ont fuite par des conversations, ce que rendre le depot public ne change
ni en bien ni en mal. La liste est ordonnee par gravite reelle : la
session Telegram vaut un acces complet au compte, une cle YouTube ne
coute que du quota."
git push origin master
```

---

## Résumé des vérifications finales

Avec `IP="$(cat "$HOME/.bottrading-vps-ip")"` et `ADMIN` lu depuis `CONTROL_ADMIN_USER`.

| Vérification | Commande | Attendu |
|---|---|---|
| Aucune donnée personnelle au niveau HEAD | `git grep -nE "$IP\|$ADMIN\|Users.$(whoami)"` | rien, exit 1 |
| Aucun `.env` suivi | `git ls-files \| grep -E '(^\|/)\.env$'` | rien, exit 1 |
| IP absente de tout l'historique | `git rev-list --all \| xargs git grep -lF "$IP"` | rien, exit 1 |
| Environment peuplé | `gh secret list --env production \| wc -l` | 15 |
| Variables peuplées | `gh variable list --env production \| wc -l` | 13 |
| Mode de trading inchangé | `gh variable list --env production \| grep TRADING_MODE` | `dry_run` |
| Déploiement fonctionnel | `curl -o /dev/null -w "%{http_code}" https://crypto.nbeny.fr/` | 200 / 3xx |
| Dépôt public | `gh repo view --json visibility` | `PUBLIC` |
| Licence présente | `curl .../master/LICENSE` | texte de la licence |
| Push protection active | `gh api repos/nbeny/BotTrading --jq '.security_and_analysis'` | `enabled` |
