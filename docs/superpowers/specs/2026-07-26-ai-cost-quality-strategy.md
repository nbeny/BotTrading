# Stratégie coût/qualité de la couche IA — analyse et recommandation

**Date:** 2026-07-26
**Statut:** analyse, décision opérateur en attente
**Mesures:** production `crypto.nbeny.fr`, fenêtre 24 h, 2026-07-26

---

## 1. Reformulation : le coût n'est pas le problème

La question posée suppose que Sonnet est un poste de coût à maîtriser. Les mesures
disent le contraire.

| Mesure (24 h) | Valeur |
|---|---|
| Symboles analysés | 51 |
| Analyses produites | 7 735 |
| Escalades émises | 400 — issues de **2 symboles** (DEXE ×382, BANK ×18) |
| Skips `cooldown/budget` | 507 |
| **Appels Sonnet réels** | **11** |
| Budget configuré | `ANTHROPIC_MAX_CALLS_PER_HOUR=12` → 288/jour |
| **Utilisation du budget** | **3,8 %** |
| Décisions produites | 1 (DEXE, `watch`, score 52, confiance 0,45) |
| Trades | 0 |

Le budget n'est pas contraignant : il est consommé à moins de 4 %. Les 507 skips
ne sont pas de la couverture perdue — c'est le cooldown de 15 min qui déduplique
**un seul token réévalué 382 fois**.

### Coût réel

Estimation par appel : ~400 tokens d'entrée (system ~120 + prompt ~250) et
~300 tokens de sortie. À mesurer précisément avec `messages.count_tokens` avant
toute décision engageante.

| Scénario | Appels/jour | Coût Sonnet 5 ($3/$15) | Coût Haiku 4.5 ($1/$5) |
|---|---|---|---|
| **Aujourd'hui** | 11 | **0,06 $/j** (~1,90 $/mois) | 0,02 $/j |
| Toutes escalades, sans déduplication | 400 | 2,28 $/j (~68 $/mois) | 0,76 $/j |
| Déduplication par changement d'état | ~40 | 0,23 $/j (~7 $/mois) | 0,08 $/j |
| Pool élargi (20 symboles qualifiants) + dédup | ~150 | 0,86 $/j (~26 $/mois) | 0,29 $/j |

**Six centimes par jour.** Même multiplié par cent, le coût en dollars reste
anecdotique pour un bot de trading.

### La vraie unité : le quota, pas les dollars

Les workers tournent en transport CLI sur un abonnement **Claude Max partagé avec
le travail de développement de l'opérateur** (`ANTHROPIC_TRANSPORT=cli`,
`CLAUDE_CODE_OAUTH_TOKEN`). Voir [[ai-cost-architecture]].

La contrainte n'est donc pas une facture mais la **contention de quota** : chaque
appel du bot est un appel indisponible pour le dev. C'est ce qui justifie que le
plafond existe — et c'est pourquoi la bonne métrique est **appels/jour**, pas
dollars/mois. À 11 appels/jour la contention est nulle ; à 400 elle devient réelle.

### Conséquence

Le problème n'est pas « comment dépenser moins » — c'est déjà quasi gratuit.
C'est **comment rendre chaque appel plus utile**, et **pourquoi seuls 2 symboles
sur 51 atteignent l'analyste**.

---

## 2. Verdict sur les quatre options proposées

Les quatre options répondent à un problème de débit. Le problème mesuré est un
problème de sélectivité et de couverture. Aucune ne s'applique telle quelle.

### Option A — Sonnet sur toutes les opportunités qualifiées

**Déjà le cas.** Les 400 escalades atteignent toutes le gate ; seul le cooldown
les réduit à 11 appels. Retirer le cooldown coûterait 2,28 $/jour et 400 appels de
quota — dont **382 sur le même token**. Ce n'est pas un arbitrage
couverture/coût, c'est de la redondance pure.

**Rejetée** : coûte du quota sans gagner un seul signal distinct.

### Option B — Haiku filtre, Sonnet sur les cas prometteurs

**Déjà l'architecture.** `ai-worker-haiku` est un scorer déterministe sans LLM :
il filtre 7 735 → 400 pour un coût nul. La question réelle serait d'insérer un
étage **Haiku-LLM** entre les deux.

Haiku 4.5 coûte 3× moins que Sonnet 5. Une cascade ne rentabilise ce tiers que si
Haiku rejette de façon fiable un volume significatif. À 11 appels/jour, l'étage
supplémentaire ajoute un mode de défaillance, une latence et du code pour
économiser **4 centimes par jour**.

**Rejetée aujourd'hui**, à reconsidérer si le volume dépasse ~200 appels/jour.

### Option C — Système adaptatif selon le régime de marché

Résout un problème inexistant : le budget est utilisé à 3,8 %. Il n'y a rien à
moduler. C'est aussi la plus complexe des quatre.

**Rejetée.** Mais l'intuition sous-jacente est correcte et se réalise déjà
naturellement : en marché calme, moins de symboles qualifient — c'est exactement
ce qu'on observe (2 sur 51). L'adaptation au régime est **déjà implicite dans le
scorer**. La rendre explicite ajouterait un mécanisme pour reproduire un
comportement acquis.

### Option D — Quota strict + priorité au meilleur potentiel

Le quota strict **existe déjà** et ne mord pas.

La partie **priorité** a toutefois une valeur réelle et non implémentée :
aujourd'hui le seau de jetons est servi **premier arrivé**. Quand le budget
deviendra contraignant — après élargissement du pool — dépenser le jeton sur le
meilleur candidat plutôt que sur le premier est strictement supérieur, à coût
identique.

**Retenue partiellement** : la priorisation, pas le quota. À implémenter avant
que le budget ne devienne contraignant, pas après.

---

## 3. Les leviers qui comptent réellement

Classés par rapport valeur/effort, mesurés sur les données de production.

### S1 — Dédupliquer sur le changement d'état, pas sur l'horloge

**Le levier le plus rentable, et il réduit le coût *et* améliore la couverture.**

Le cooldown actuel est temporel : 15 min par symbole. Il est grossier dans les
deux sens :

- il peut dépenser un appel sur un DEXE **inchangé** à la minute 16 ;
- il bloque un setup **génuinement nouveau** à la minute 14.

Remplacer par un hash du vecteur de features bucketisé : on n'appelle l'analyste
que si l'état matériel a changé. Coût d'implémentation faible, aucun coût
d'inférence.

**Effet estimé :** les 382 escalades DEXE se réduisent aux quelques états
réellement distincts, et un vrai retournement est traité immédiatement au lieu
d'attendre la fin d'une fenêtre arbitraire.

**Compromis :** un choix de granularité de bucket trop grossier fait manquer des
transitions ; trop fin et on retombe sur le comportement actuel. Se calibre sur
les données historiques, sans risque.

### S2 — Escalader sur la proximité de la frontière de décision, pas sur la hauteur du score

**Le levier de qualité le plus important.**

La valeur de Sonnet est de **trancher l'ambiguïté**. L'appeler sur un signal que
la voie déterministe trancherait identiquement est un gaspillage pur, quel que
soit le prix.

Le gate actuel est `score ≥ 60 ET (ambigu OU vol≥0,6 OU mom≥0,6)` — c'est un
critère de **force**, pas d'**incertitude**. Un score de 78 que le risk-engine
approuverait de toute façon n'a besoin d'aucun avis LLM ; un 68 aux facteurs
contradictoires en a besoin.

Reformulation : escalader quand **l'avis de l'analyste peut changer l'issue** —
score dans une bande autour du seuil de décision, ou désaccord entre facteurs.

**Effet :** à budget constant, chaque appel porte sur un cas où il change quelque
chose. C'est l'amélioration du ratio performance/coût par le numérateur, pas par
le dénominateur.

**Compromis :** un setup très fort mais piégeux (manipulation, pool mort) ne serait
plus escaladé. Atténuation : conserver une escalade sur *anomalie* (liquidité
ténue mesurée, spike volume extrême) indépendamment de la proximité du seuil.

### S3 — Élargir le vivier avant de dépenser plus par candidat

**2 symboles sur 51 qualifient.** C'est la contrainte réelle sur la découverte
d'opportunités, et **elle ne coûte aucun token LLM à lever** — c'est le problème
de couverture des facteurs identifié en phase 1c :

- `volume_spike_ratio` n'est émis que si le turnover dépasse 30 % de la
  capitalisation (`collector-coingecko/app/domain/mapper.py:45`) — jamais pour les
  majeures ;
- `liquidity_usd` n'existe que via DexScreener, qui ne couvre pas les CEX.

Dépenser davantage en Sonnet pendant que 2 symboles seulement qualifient revient à
optimiser la mauvaise extrémité de la chaîne.

**Ordre correct : S3 avant toute augmentation de budget.**

### S4 — Regrouper plusieurs candidats par appel

Sans objet aujourd'hui (11 appels/jour). Devient le mécanisme central dès que S3
élargit le vivier : évaluer N candidats en un appel fait croître le coût avec les
**tokens**, pas avec le **nombre d'appels** — et c'est le nombre d'appels qui
consomme le quota d'abonnement.

À implémenter au moment où le volume approche du plafond, pas avant.

### S5 — Cache de prompt : inapplicable

Le `SYSTEM` de `ai-worker-sonnet` fait ~120 tokens. Le préfixe minimum cacheable
est de **1 024 tokens** pour Sonnet 5 (et 4 096 pour Haiku 4.5). Rien à cacher, et
poser un `cache_control` ne ferait que payer la prime d'écriture sans lecture.

**À reconsidérer** uniquement si le prompt système grossit au-delà du plancher —
ce qui n'est pas un objectif en soi.

### S6 — Batch API : hors décisions live, utile en calibration

−50 % sur tous les tokens, mais latence d'une heure à 24 h. Inutilisable pour un
signal d'entrée.

**Usage légitime :** rejeu historique pour calibrer les seuils hors ligne — faire
réévaluer par Sonnet un lot de candidats passés à moitié prix, sans contrainte de
latence. C'est un outil de calibration, pas de production.

---

## 4. Seuils du risk-engine : mesurer avant de toucher

La consigne opérateur est explicite : ne pas simplement abaisser les protections,
proposer des ajustements mesurables avec simulation.

### Le constat qui commande tout le reste

**Il y a une décision. Une seule.** `DEXE, watch, score 52, confiance 0,45`,
refusée par un risk-engine qui exige 70 et 0,55.

Il n'existe **aucune base statistique** pour un changement de seuil. Modifier un
plancher de risque sur un échantillon de 1 serait de l'ajustement à la
superstition. Trois lectures restent également défendables :

1. Sonnet est mal calibré et devrait scorer plus haut ;
2. les planchers sont trop stricts pour une décision déjà validée par un analyste ;
3. Sonnet a eu raison de scorer bas — le système fonctionne en refusant.

Aucune donnée ne permet de départager.

### Ce qu'il faut construire d'abord : le journal contrefactuel

Plutôt que du paper trading, enregistrer pour **chaque décision et chaque refus** :

```
decision_id, symbol, direction, score, confidence, risk_reward,
exposure_at_decision, thresholds_in_force, rejection_stage, rejection_reason,
price_at_decision, price_at_+1h, price_at_+4h, price_at_+24h
```

Cela permet de calculer **hors ligne**, pour n'importe quelle combinaison de
seuils, le P&L qu'elle aurait produit — sur les mêmes données, sans exécuter un
seul ordre.

**Pourquoi c'est supérieur au paper trading :** le paper trading ne teste que les
seuils réellement déployés. Le journal contrefactuel teste **toutes les
combinaisons simultanément** à partir d'un seul enregistrement, et rétroactivement.

Risque : nul. Coût d'inférence : nul.

### La question à trancher avant celle des seuils

Avant d'optimiser le coût de la couche IA, il faut vérifier qu'elle a une **valeur
prédictive**. Question calculable dès que le journal tourne, sans aucun risque :

> Le rendement à +4 h / +24 h des signaux **escaladés et validés** est-il
> supérieur à celui des signaux non escaladés, à conditions de marché comparables ?

Si la réponse est non, aucun réglage de seuil n'aidera et le problème est en
amont. Si la réponse est oui, l'écart mesuré donne la valeur économique d'un
appel Sonnet — et **c'est ce nombre, pas une intuition, qui doit fixer le budget**.

### Protocole de changement de seuil

Une modification de plancher de risque n'est promue que si :

1. le journal couvre **≥ 30 décisions** sur ≥ 2 semaines ;
2. le balayage de seuils montre une amélioration d'espérance sur ce corpus ;
3. l'amélioration survit à un découpage temporel (première moitié / seconde
   moitié) — protection minimale contre le surapprentissage ;
4. le changement est déployé en `dry_run` d'abord, et comparé au contrefactuel
   pendant ≥ 1 semaine.

Aucun de ces critères n'est satisfaisable aujourd'hui. **C'est le résultat de
l'analyse, pas une esquive.**

---

## 5. Recommandation

**Ne pas augmenter le budget Sonnet.** Il est utilisé à 3,8 % ; l'augmenter
n'achèterait que des réévaluations redondantes du même token.

Ordre d'exécution, du plus rentable au moins urgent :

| # | Action | Effet coût | Effet qualité | Effort |
|---|---|---|---|---|
| 1 | **Journal contrefactuel** (§4) | nul | débloque toute décision ultérieure | moyen |
| 2 | **S1** — dédup par changement d'état | **baisse** | **hausse** (réaction immédiate) | faible |
| 3 | **S3** — élargir le vivier (facteurs) | nul en LLM | hausse forte (2 → N symboles) | moyen |
| 4 | **S2** — escalade sur frontière de décision | neutre | hausse (chaque appel compte) | moyen |
| 5 | **D-partiel** — priorité au meilleur candidat | neutre | hausse quand le budget mord | faible |
| 6 | **S4** — regroupement par lot | baisse à volume élevé | neutre | moyen |

Les points 1 et 2 sont indépendants et peuvent avancer en parallèle. Le point 3
conditionne l'utilité des points 4 à 6 : tant que 2 symboles qualifient, affiner
la sélectivité n'a presque rien sur quoi s'exercer.

### Ce que la recommandation refuse explicitement

- **Augmenter `ANTHROPIC_MAX_CALLS_PER_HOUR`** — non justifié par les mesures.
- **Ajouter un étage Haiku-LLM** — 4 centimes/jour d'économie pour un mode de
  défaillance de plus.
- **Baisser un plancher de risque** — aucune base statistique ; à revoir quand le
  journal aura ≥ 30 décisions.
- **Activer le cache de prompt** — prompt système sous le plancher cacheable.
- **Utiliser la Batch API en production** — latence incompatible avec un signal
  d'entrée ; réservée à la calibration hors ligne.

### Objectif reformulé

Le ratio performance/coût ne s'améliore pas ici en réduisant le dénominateur : il
est déjà proche de zéro. Il s'améliore en augmentant le numérateur — **plus de
candidats réels (S3), et chaque appel placé là où il change l'issue (S2)** — puis
en mesurant ce que cela rapporte réellement (§4) avant d'engager davantage.
