# Rapport de calibration du seuil — service à la demande sur `/journal`

**Date :** 2026-08-08
**Statut :** validé, prêt à planifier
**Services touchés :** `decision-engine`, `control-api`, `api-gateway`, `frontend`,
`migrations`, `scripts/pick_threshold.py`
**Relation aux specs antérieures :** rend consultable depuis le terminal l'outil de
`2026-08-04-decision-valve-calibration-design.md` ; complète le panneau de calibration de
`2026-08-06-quant-cockpit-design.md` (vague 1), qui simule un seuil sans jamais dire si les
données qui le nourrissent sont complètes.

## Problème

`scripts/pick_threshold.py` répond à la question la plus importante du projet — « les huit axes
sont-ils réellement présents, et quel seuil laisse passer un débit donné ? » — mais il n'est
accessible qu'en SSH dans un conteneur. En pratique il n'est donc pas lancé, et la page
`/journal` livrée hier propose un slider de calibration **sans dire si les données sous-jacentes
sont exploitables**. C'est exactement l'ordre inverse de celui que le script impose : son rapport
de présence par axe sort AVANT tout nombre, et il refuse de proposer un seuil si un axe est muet.

Le 4 août, `positioning` était à 0 sur 1 281 511 lignes ; une calibration lancée ce jour-là aurait
rendu un nombre parfaitement plausible et faux. Le garde existe, il n'est simplement pas sous les
yeux de l'opérateur.

## Périmètre et décisions de cadrage

1. **Périodique + à la demande** (option A retenue en session) : un scan tourne seul toutes les
   6 h, `/journal` affiche le dernier rapport avec son âge, un bouton en force un neuf. Une page
   qui n'affiche rien tant qu'on n'a pas cliqué, ou qu'on ne peut pas rafraîchir, ont toutes deux
   été écartées.
2. **Le scan vit dans `decision-engine`.** Il rejoue `score(features_from(...))` sur chaque ligne :
   seul le service qui possède `scoring.py` peut le faire sans violer la frontière qui justifie les
   trois copies indépendantes de la liste d'axes. api-gateway ne fait que **lire** le résultat.
3. **Une seule logique, deux faces.** L'analyse devient une fonction pure ; le CLI l'imprime en
   texte, le service la persiste en JSON. Le CLI et l'interface ne peuvent pas diverger.

## Contraintes mesurées (elles dictent le design)

- **Le scan ne tient pas en mémoire** : 1 414 216 lignes sur 7 jours, 368 Mo de JSONB compressé
  contre 1 996 Mo disponibles sur le VPS. Le parcours est un flux (`yield_per=5000`) qui
  n'accumule que des agrégats bornés. Ce n'est pas négociable, et interdit toute réponse HTTP
  synchrone.
- **Le coût CPU est réel** : re-scorer plus d'un million de lignes sur 2 vCPU qui perdent déjà
  24 % en vol d'hyperviseur, en concurrence avec le pipeline live.
- **Le volume varie d'un facteur ~15 d'un jour à l'autre** (671 955 lignes le 31 juillet, 0 le
  lendemain). Une cible « décisions par jour » moyennée sur une fenêtre non représentative ne veut
  rien dire — le rapport doit restituer la répartition jour par jour.

## Objectifs

- Voir le rapport complet depuis `/journal`, sans SSH, avec son âge toujours visible.
- Pouvoir en forcer un neuf après avoir corrigé un collecteur, sans attendre le cycle périodique.
- Conserver intégralement les verdicts de refus et leurs explications : c'est la partie qui a de
  la valeur, pas le nombre.
- Ne jamais faire tourner deux scans en même temps.

## Non-objectifs

- Aucun changement de `WEIGHTS`, du scoring, ni des trois copies de la liste d'axes.
- Aucune application automatique du seuil proposé : le rapport informe, l'opérateur décide et pose
  `RISK_MIN_SCORE` lui-même. Une vanne qui se règle toute seule n'est pas ce qu'on construit.
- Pas d'historique graphique de l'évolution de la présence par axe (les lignes sont conservées,
  leur exploitation viendra si le besoin se confirme).
- Pas de file d'attente de scans : une demande pendant un scan en cours est ignorée, pas empilée.

## Architecture

### 1. `decision-engine/app/threshold_scan.py` — l'analyse, pure

Le module extrait de `pick_threshold.py` :

- `Scan` (agrégats bornés) et `scan_window(session, days) -> Scan` : le parcours en flux, seule
  partie qui touche la base.
- `analyze(scan, *, days, target_per_day) -> ThresholdReport` : **fonction pure**, aucune I/O.

`ThresholdReport` est un dataclass sérialisable portant :

| Champ | Contenu |
|---|---|
| `window` | `days` demandés, `min_time` réellement vue, `total`, `no_evidence`, `by_day` |
| `axes[]` | par axe : `key`, `weight`, `seen`, `pct`, `mute` (bool) — **ordonnés par poids décroissant**, le compte brut à côté du pourcentage (à 5 décimales près, « 1 ligne sur 1 281 511 » s'affiche « 0.0% ») |
| `refusal` | `null`, ou `{code, title, detail}` — le garde qui a tiré et **son texte explicatif intégral** |
| `distribution` | percentiles du score, lignes scorées, part franchissant `RISK_MIN_CONFIDENCE` |
| `proposal` | `null` si refus, sinon `{threshold, target_per_day, actual_per_day, distinct_symbols, passing_pct}` |
| `warnings[]` | avertissements non bloquants (répartition journalière déséquilibrée, etc.) |
| `sonnet` | statistiques des scores Sonnet observés |

Les quatre codes de refus existants sont conservés tels quels (`MUTE_AXES`, `NO_REGIME`,
`REGIME_GAP`, et le refus de cible sans données), **avec leurs paragraphes d'explication mot pour
mot** : ce sont eux qui distinguent « la collecte est cassée » de « l'axe est légitimement rare »,
et qui rappellent que `MIN_PRESENCE_PCT` ne se contourne pas.

`scripts/pick_threshold.py` devient le formateur texte de ce même rapport. Son comportement CLI,
ses codes de sortie et ses garde-fous ne changent pas.

### 2. Exécution — deux déclencheurs, un verrou

Dans `decision-engine` :

- **Tâche périodique** : boucle asyncio, cadence `THRESHOLD_SCAN_INTERVAL_H` (défaut 6 ;
  **`0` désactive le périodique**, ne laissant que la demande — l'échappatoire si le VPS souffre).
- **Commande** : consommation de `control.commands`, nouveau type `RUN_THRESHOLD_SCAN`. Groupe de
  consumer unique par réplique, comme trading-engine, pour que toutes reçoivent la commande.
- **Verrou Redis** `Cache.lock("threshold-scan")` : une seule exécution à la fois, quelles que
  soient les répliques et le nombre de clics. **Le verrou est aussi l'état du job** — pas de
  machine à états à maintenir.

Fenêtre par défaut : `THRESHOLD_SCAN_DAYS` (défaut 7), cible `THRESHOLD_SCAN_TARGET_PER_DAY`
(défaut 200).

### 3. Persistance et lecture

Table `threshold_reports` (migration **0020**, table simple, pas d'hypertable) :
`time` (PK), `window_days`, `target_per_day`, `status` (`ok`/`error`), `error` (Text, nullable),
`duration_s`, `payload` (JSONB). Quelques Ko par ligne, quelques lignes par jour — aucune
politique de rétention nécessaire.

**Un scan qui échoue écrit une ligne `status="error"`.** L'interface dit alors « dernier scan
échoué à 14h02 » au lieu d'afficher un rapport périmé comme s'il était frais.

api-gateway sert `GET /systems/journal/threshold` : dernier rapport + `running` (bool, lu depuis
l'existence du verrou Redis — l'accès Redis en lecture seule existe depuis la vague market-data).
Entrée au manifeste de contrat, test de parité, route mock.

### 4. control-api — le bouton

`POST /analysis/threshold-scan` publie un `ControlCommandEvent(kind=RUN_THRESHOLD_SCAN)` sur
`control.commands`. Comme toute écriture du plan de contrôle, control-api n'écrit rien lui-même.
RBAC : même exigence que les autres actions opérateur.

### 5. Le panneau `/journal`

Un `SectionCard` « Rapport de scan — le seuil est-il calibrable ? », **placé au-dessus** des trois
panneaux existants : c'est la lecture qui doit précéder la simulation de seuil.

- En-tête : âge du rapport (« il y a 2 h »), bouton **Relancer** désactivé pendant un scan,
  indicateur « calcul en cours… » quand `running`.
- **Tableau de présence par axe** : poids, présence en %, compte brut, marqueur visuel sur tout axe
  muet. La partie critique.
- **Verdict** : soit le seuil proposé avec son débit réel, soit le bloc de refus complet — titre et
  texte explicatif tels que le script les rend. Jamais un nombre sans son verdict.
- Avertissements et répartition jour par jour.
- États vides honnêtes : « aucun scan encore effectué » avec le bouton ; « dernier scan échoué »
  avec l'horodatage et l'erreur ; un rapport de plus de 24 h affiche son âge en évidence.

## Erreurs et cas limites

- **Demande pendant un scan** : la commande est ignorée, l'endpoint renvoie `running: true`. Ni
  erreur, ni file d'attente.
- **Scan planté** : ligne `status="error"` avec le message ; le verrou expire par TTL, le bouton
  redevient actif, le dernier rapport valide reste affiché **avec son âge**.
- **Fenêtre vide** (aucune ligne de journal) : rapport `ok` avec `total: 0`, aucun axe présent,
  refus `MUTE_AXES` — cohérent avec le CLI, qui refuse aussi.
- **Un seul jour de données** : le rapport le dit dans `by_day` ; l'avertissement de répartition
  se déclenche.
- **`RISK_MIN_SCORE` absent du conteneur** : sans effet ici — ce rapport ne lit pas le seuil
  courant, il en propose un.

## Tests

- **`analyze()` pure**, sans base : garde de présence (un axe sous `MIN_PRESENCE_PCT` → `refusal`
  posé et `proposal: null`), régime absent, écart de régime, fenêtre vide, un seul jour.
- **Parité CLI/service** : un test vérifie que le CLI et le rapport structuré décrivent le même
  verdict sur un même `Scan` synthétique — c'est ce qui empêche les deux faces de diverger.
- **api-gateway** : entrée au manifeste, test de contrat, route mock miroir.
- **control-api** : la route publie bien un `ControlCommandEvent` du bon type (patron des tests de
  commandes existants).
- **Frontend** : panneau testé sur un rapport `ok`, un rapport en refus, un scan `running`, un
  état vide et un état erreur.

## Risques

- **Le scan concurrence le pipeline live.** Atténué par le verrou, la cadence configurable et le
  `0` qui désactive le périodique. À surveiller après la première mise en service.
- **Le rapport structuré peut dériver du texte du CLI** si quelqu'un modifie l'un sans l'autre.
  C'est précisément ce que le test de parité protège ; il doit rester au vert.
- **La fenêtre de 7 jours grossit avec le volume du journal.** Si le scan devient trop long, le
  levier est `THRESHOLD_SCAN_DAYS`, pas un échantillonnage — un échantillon fausserait la présence
  par axe, qui est la seule chose que ce rapport doit dire sans erreur.
