# Déduplication par changement d'état — design

**Date:** 2026-07-26
**Statut:** design, en attente de validation opérateur
**Remplace:** le cooldown temporel `ai:cooldown:{symbol}` (900 s) de `ai-worker-sonnet`
**Contexte:** [[2026-07-26-ai-cost-quality-strategy]] — levier S1

---

## 1. Le problème, mesuré

`ai-worker-sonnet/app/worker.py:70-76` protège le budget LLM par un cooldown
temporel : un symbole escaladé n'est pas réévalué avant 900 s.

C'est grossier dans les deux sens :

- **Gaspillage** — un symbole dont l'état n'a pas bougé consomme un appel à la
  minute 16 simplement parce que l'horloge a tourné.
- **Cécité** — un retournement réel à la minute 14 attend jusqu'à 15 minutes.

Mesuré en production sur 24 h : 400 escalades issues de **2 symboles**
(DEXE ×382, BANK ×18), réduites à 11 appels par le cooldown.

### La mesure qui détermine la conception

Évolution horaire des features de DEXE :

| Heure | Analyses | `chg_24h` | `volume_spike` | `sentiment` | Score |
|---|---|---|---|---|---|
| 23:00 | 122 | 26,8 → 41,2 | 12,5 → 34,3 | 0,30 → 0,43 | 75 → 78 |
| 22:00 | 119 | 34,8 → 47,5 | 12,6 → 33,8 | 0,28 → 0,30 | 74 → 75 |
| 21:00 | 119 | 10,4 → 39,9 | 11,2 → 34,9 | 0,28 | 57 → 74 |

Les features brutes varient énormément. Le score ne bouge presque pas. La cause
est le plafonnement dans `scorer.py` :

```
mom = clamp(|chg| / 15.0, 0, 1)      → 26,8 % et 41,2 % donnent tous deux 1.0
vol = clamp((ratio - 1) / 4.0, 0, 1) → ×12,5 et ×34,3 donnent tous deux 1.0
```

**Conséquence directe : un hash des features brutes déclencherait sur chaque
poll — ~120 escalades/heure pour DEXE, soit dix fois pire que le cooldown
actuel.**

Le seul facteur qui bouge réellement est le sentiment, d'environ **0,15 par
heure**. C'est cette amplitude qui calibre le seuil de variation significative.

### Décision de conception

La déduplication porte sur les **facteurs normalisés post-plafonnement**
(`meta.factors` — déjà calculés par `scorer.py` et déjà transportés sur
l'`AnalysisEvent`), jamais sur les features brutes.

C'est la bonne abstraction parce que c'est exactement ce qui détermine la
décision : deux états produisant les mêmes facteurs normalisés produisent le même
score, donc la même issue. Les demander à l'analyste deux fois ne peut rien
apprendre.

---

## 2. Pourquoi pas un simple hash

La consigne demandait « hash du vecteur de features ». Un hash pur, même sur les
facteurs normalisés, a un défaut structurel qu'il faut traiter :

**Le battement de frontière.** Une valeur qui oscille autour d'un bord de bucket
— `mom = 0,599` puis `0,601` puis `0,599` — fait basculer le hash à chaque poll,
alors que rien de matériel n'a changé. Le remède naïf (buckets plus larges) crée
le défaut symétrique : une inversion réelle à l'intérieur d'un bucket devient
invisible.

**La parade : ancrer la comparaison sur le dernier état *appelé*, pas sur le
dernier état *observé*.** On ne compare pas deux buckets successifs, on compare
l'état courant à l'état qui a justifié le dernier appel. L'hystérésis devient
intrinsèque : tant que la dérive cumulée depuis l'ancre reste sous le seuil, on
n'appelle pas, quel que soit le nombre d'oscillations intermédiaires.

Le hash conserve un rôle, en voie rapide : si le dict de facteurs est
**strictement identique** à l'ancre, on sort immédiatement sans calculer de
distance. C'est le cas majoritaire (DEXE aux facteurs saturés), et il coûte une
comparaison de chaîne.

---

## 3. Mécanisme

### État persisté

Une clé Redis par symbole, `ai:anchor:{symbol}`, sans TTL (l'expiration est gérée
explicitement, pas par Redis — voir §3.4) :

```json
{
  "fingerprint": "sha1 des facteurs arrondis",
  "factors": {"momentum": 1.0, "volume": 1.0, "sentiment": 0.35, "liquidity": 0.5},
  "score": 76,
  "factors_present": 3,
  "chg_sign": 1,
  "sent_sign": 1,
  "regime": "r7",
  "called_at": 1785801600
}
```

### 3.1 Déclencheurs — l'un d'eux suffit

| # | Déclencheur | Règle | Motivation |
|---|---|---|---|
| **T1** | Premier contact | pas d'ancre pour ce symbole | jamais vu, rien à comparer |
| **T2** | Dérive matérielle | `max_i \|factor_i − anchor_i\| ≥ DELTA` | le levier principal |
| **T3** | Inversion de direction | changement de signe de `chg_24h` **ou** de `sentiment_score` | une inversion est matérielle quelle que soit son amplitude |
| **T4** | Franchissement de seuil | le score traverse `HAIKU_ESCALATE_SCORE`, `DECISION_THRESHOLD` ou `RISK_MIN_SCORE` | ce n'est plus l'entrée qui change, c'est **l'issue** |
| **T5** | Couverture modifiée | `factors_present` change | le score ne veut plus dire la même chose |
| **T6** | Changement de régime | le jeton de régime diffère | le même symbole dans un autre marché est un autre cas |
| **T7** | Péremption de sûreté | `now − called_at > MAX_AGE` | garde-fou contre une ancre figée |

**T3 est la réponse directe à « rater une vraie inversion ».** Un retournement de
+35 % à −2 % ne franchit aucun seuil de dérive sur `mom` (les deux plafonnent
différemment mais le signe change) — c'est le signe qui le capte, immédiatement.

**T4 mérite un mot** : c'est le seul déclencheur qui raisonne en termes de
conséquence plutôt que d'entrée. Un score qui passe de 68 à 71 franchit
`DECISION_THRESHOLD` — la dérive des facteurs peut être minime, l'issue change du
tout au tout. C'est aussi le pont vers le levier S2 (escalade sur proximité de
frontière) : les deux partagent la même notion de bord de décision.

### 3.2 Plancher anti-battement

Tous les déclencheurs sont **subordonnés** à :

```
now − called_at ≥ MIN_INTERVAL
```

C'est le résidu du cooldown, mais court : il borne le pire cas absolu
(un appel toutes les `MIN_INTERVAL`) sans imposer d'aveuglement de 15 minutes.
Un symbole en pleine inversion est réanalysé en 2 minutes au lieu de 15.

**T7 est exempté** de ce plancher — la péremption ne peut pas, par construction,
survenir plus tôt que `MAX_AGE`.

### 3.3 Valeurs par défaut proposées

| Paramètre | Défaut | Justification |
|---|---|---|
| `DELTA` | **0,15** | Amplitude horaire mesurée du sentiment DEXE. En dessous, on rattrape du bruit ; au-dessus, on rate la dérive lente. |
| `MIN_INTERVAL` | **120 s** | Plancher dur. Ramène la latence de détection d'un retournement de 900 s à 120 s. |
| `MAX_AGE` | **4 h** | Une position qu'on n'a pas réévaluée depuis 4 h mérite un regard, même à état stable. |
| Arrondi du hash | **2 décimales** | Les facteurs sont dans [0,1] ; deux décimales suffisent, et 0,01 est très en dessous de `DELTA`. |

Toutes exposées en variables d'environnement (`SONNET_DEDUP_*`), avec ces valeurs
en défaut. **Ce sont des points de départ raisonnés, pas des optima** — le journal
contrefactuel les recalibrera sur données réelles.

### 3.4 Pas de TTL Redis

L'ancre n'expire pas par TTL. Deux raisons :

1. `MAX_AGE` (T7) est un **déclencheur**, pas un oubli : on veut réévaluer un
   symbole périmé, pas perdre son historique.
2. Une ancre expirée silencieusement rendrait T1 indiscernable de T7 dans
   l'entonnoir — on perdrait la raison de l'appel.

Purge : les symboles disparus du vivier sont nettoyés par un balayage périodique
(ou laissés — 51 symboles × ~300 octets est négligeable).

---

## 4. Changement de régime

### Définition

Un jeton grossier, recalculé toutes les 5 minutes et stocké dans `ai:regime` :

```
regime = bucket(BTC chg_24h, pas de 2,5 %) : bucket(part des symboles à |chg| > 5 %, pas de 20 %)
```

Deux composantes délibérément : la direction du marché (BTC comme proxy) et sa
**dispersion**. Un marché où tout monte n'est pas le même qu'un marché où un seul
token s'envole — et pour un bot qui cherche des anomalies, c'est la dispersion qui
porte l'information.

### Le piège : la ruée

**Un changement de régime invalide toutes les ancres simultanément.** Avec 51
symboles et un budget de 12 appels/heure, une transition de régime provoquerait
une ruée qui viderait le budget en une minute — et le viderait dans l'ordre
d'arrivée des événements, c'est-à-dire au hasard.

**Mitigation : le changement de régime marque les ancres comme périmées, il ne
force pas l'escalade immédiate.** Un symbole à ancre périmée devient *éligible*,
et c'est la file de priorité qui décide de l'ordre.

### Couplage à surfacer

Ce point crée une **dépendance dure** : le déclencheur de régime (T6) n'est sûr
que si la priorisation existe. Sans elle, T6 transforme une transition de marché
en épuisement aléatoire du budget.

**Recommandation : livrer T1-T5 et T7 d'abord, et n'activer T6 qu'avec la
priorisation** (levier « D-partiel » de la stratégie coût/qualité). Le mécanisme
reste correct sans T6 — il perd seulement la sensibilité au contexte macro.

---

## 5. Priorisation (prérequis de T6)

Quand plusieurs symboles sont éligibles et que le budget est contraint, servir le
plus prometteur plutôt que le premier arrivé. Rang proposé :

```
priorité = score × (factors_present / 4) × (1 + amplitude_de_dérive)
```

- `score` — potentiel brut ;
- `factors_present / 4` — **pénalise le score bâti sur peu de preuves**, ce qui
  reprend directement le travail de la phase 1 ;
- `amplitude_de_dérive` — `max_i |factor_i − anchor_i|`, privilégie ce qui vient
  réellement de bouger.

Implémentation : sorted set Redis, drainé par le worker à hauteur du budget
disponible. Hors périmètre de ce design ; spécifié ici parce que T6 en dépend.

---

## 6. Effet attendu, honnêtement

**Sur DEXE, le gain en nombre d'appels est modeste.** Ses facteurs sont saturés
et seul le sentiment dérive, d'environ 0,15/heure — soit à peu près un appel par
heure avec `DELTA = 0,15`, contre quatre avec le cooldown. Réduction réelle mais
pas spectaculaire.

**Les gains véritables sont ailleurs :**

1. **Latence de détection** — un retournement passe de 900 s à 120 s au pire.
   C'est le gain qui compte pour un bot de trading, et il n'est pas visible dans
   un compteur d'appels.
2. **États figés** — un symbole réellement immobile (BANK, 18 escalades) cesse de
   consommer du budget à l'horloge.
3. **Passage à l'échelle** — c'est le gain décisif, et il est prospectif. Quand le
   vivier passera de 2 à ~20 symboles (levier S3), le cooldown produirait
   ~80 appels/heure contre un budget de 12 : le budget deviendrait contraignant et
   serait consommé au hasard. Avec la déduplication par état, il va aux symboles
   qui ont réellement bougé.

**Je ne présente pas ce changement comme une économie.** C'est un changement de
*critère d'allocation* : à budget constant, chaque appel porte sur un état
nouveau plutôt que sur une horloge écoulée.

---

## 7. Modes de défaillance et parades

| Défaillance | Conséquence | Parade |
|---|---|---|
| Ancre corrompue / illisible | symbole jamais réévalué | échec ouvert : à la moindre erreur de lecture, traiter comme T1 (premier contact) |
| Redis indisponible | plus aucune déduplication | échec fermé sur le budget : le seau de jetons reste la garde ultime |
| `DELTA` trop bas | retour au comportement par poll | `MIN_INTERVAL` borne le pire cas à 30 appels/heure/symbole |
| `DELTA` trop haut | dérive lente jamais captée | T7 (`MAX_AGE`) garantit une réévaluation au plus tard toutes les 4 h |
| Collecteur figé (features gelées) | état inchangé, jamais réévalué | T7 encore — un état trop stable devient suspect par construction |
| Régime oscillant sur une frontière | invalidations en rafale | l'ancre stocke le régime *au moment de l'appel* ; hystérésis identique à T2 |

Le principe transversal : **toute incertitude se résout en faveur de l'appel.**
Un appel superflu coûte quelques centimes ; une inversion manquée coûte une
position.

---

## 8. Périmètre

**Dans le périmètre**
- `services/ai-worker-sonnet/app/worker.py` — remplacer `_may_call`
- Nouveau module `services/ai-worker-sonnet/app/dedup.py` — logique pure, testable sans Redis
- Instrumentation : le motif de déclenchement (`T1`…`T7`) est joint à la
  `DecisionEvent` et remonté dans `/systems/funnel`, pour que la calibration de
  `DELTA` s'appuie sur des comptages et non sur une intuition
- Variables d'environnement `SONNET_DEDUP_*` exposées dans les deux composes

**Hors périmètre**
- La file de priorité (§5) — spécifiée seulement pour établir la dépendance de T6
- Le déclencheur de régime T6 — livré désactivé par défaut, activé avec la priorisation
- Le journal contrefactuel — chantier suivant
- Toute modification des seuils de risque

**Testabilité :** `dedup.py` ne fait aucune I/O — il prend un état courant et une
ancre, et renvoie `(doit_appeler, motif)`. Chacun des sept déclencheurs se teste
en table de vérité, y compris les cas de battement de frontière que le cooldown
temporel masquait.

---

## 9. Calibration de DELTA — mesurée, pas postulée

> Cette section **corrige le §3.3**. Le `DELTA = 0,15` uniforme qui y était
> proposé est erroné ; il est conservé plus haut pour la traçabilité du
> raisonnement, mais ne doit pas être implémenté.

### 9.1 L'erreur

`DELTA = 0,15` était dérivé de la dérive horaire du sentiment de DEXE, puis
généralisé aux quatre facteurs. La distribution réelle montre que c'est faux.

Mesure sur les 7 735 analyses en base, **1 639 transitions de 5 minutes, 52
symboles** (base de temps normalisée à 5 min pour que DEXE, analysé toutes les
30 s, soit comparable à un symbole calme) :

| Facteur | p50 | p90 | p95 | **p99** | max |
|---|---|---|---|---|---|
| `momentum` | 0 | 0,017 | 0,040 | **0,114** | 0,307 |
| `sentiment` | 0 | 0 | 0,077 | **0,370** | 0,798 |
| `volume` | 0 | 0 | 0 | **0,049** | 0,343 |
| `liquidity` | 0 | 0 | 0 | **0** | 0 |

Trois enseignements :

1. **La médiane est nulle pour les quatre facteurs.** La majorité des transitions
   de 5 minutes ne produisent aucun changement. C'est le régime dominant, et il
   valide le principe même de la déduplication.
2. **Les quatre distributions n'ont rien de commun.** Le momentum bouge souvent et
   faiblement ; le sentiment bouge rarement mais par sauts (queue lourde, max
   0,798) ; le volume ne bouge presque jamais.
3. **`DELTA = 0,15` est au-dessus du p99 du momentum (0,114) et du volume
   (0,049).** Un seuil uniforme à 0,15 aurait rendu la dérive de ces deux facteurs
   **structurellement indétectable** — il ne se serait déclenché que sur le
   sentiment. Vérifié : 79 déclenchements sur 24 h, tous d'origine sentiment.

**Un seuil par facteur n'est pas un raffinement, c'est une nécessité.**

### 9.2 La distribution n'est pas dominée par un actif

C'était la crainte explicite de l'opérateur. Vérification :

| | Transitions non nulles | Symboles concernés | dont DEXE |
|---|---|---|---|
| `momentum` | 311 | **48** | **2** (0,6 %) |
| `sentiment` | 99 | **25** | 4 (4 %) |

La base est large. DEXE, qui domine pourtant le compte d'escalades, ne contribue
presque rien à la distribution des dérives — précisément parce que ses facteurs
sont saturés et donc constants. La calibration groupée est saine.

### 9.3 Méthode : l'opérateur choisit un taux, le système en dérive le seuil

`DELTA` n'est pas un nombre à deviner. C'est **un quantile de la distribution
observée des dérives**, calculé par facteur et groupé sur tous les symboles.

Le quantile est le bon estimateur ici, et une alternative courante ne marcherait
pas : la médiane des dérives étant nulle, un écart médian absolu (MAD) vaudrait
zéro lui aussi et serait inutilisable. Les distributions sont trop asymétriques
pour un écart-type. Les quantiles absorbent les deux.

Taux de déclenchement mesurés pour plusieurs choix :

| Jeu de seuils (mom/vol/sen) | Déclenchements/24 h | Symboles distincts |
|---|---|---|
| p95 — 0,040 / 0,001 / 0,077 | 246 | 41 |
| **p99 — 0,114 / 0,049 / 0,370** | **51** | **19** |
| p99,5 — 0,150 / 0,080 / 0,500 | 28 | 11 |

**Recommandation : p99.** 51 déclenchements/jour représentent 18 % du budget de
288 — confortable — et chacun correspond à un événement au centile 99 de sa propre
distribution, c'est-à-dire réellement anormal.

**Nuance importante à ne pas surinterpréter :** ces 51 déclenchements portent sur
tous les symboles analysés, mais seuls ceux qui franchissent le gate d'escalade
atteignent Sonnet — aujourd'hui **2**. La déduplication ne peut donc pas, seule,
élargir la couverture ; elle remplace un critère d'horloge par un critère d'état
sur les symboles déjà éligibles. Les 19 symboles ne deviennent atteignables
qu'une fois le vivier élargi (levier S3). Effet à court terme sur DEXE et BANK :
quelques appels par jour au lieu de 11.

### 9.4 Facteur `liquidity` : déclencheur désactivé

Variance **strictement nulle** sur les 1 639 transitions. Cause connue :
`liquidity_usd` est presque toujours absent, donc `liq_f` retombe sur son neutre
constant de 0,5 (voir le levier S3). Un quantile calculé sur une constante n'a pas
de sens.

`DELTA_liquidity` est donc **désactivé**, pas fixé à une valeur arbitraire. Il
sera calibré comme les autres dès que la liquidité sera réellement alimentée. Un
déclencheur qui ne peut pas se déclencher doit le dire, pas faire semblant.

### 9.5 Recalibration

Script `scripts/calibrate_dedup_thresholds.py` : rejoue la requête de
distribution sur une fenêtre glissante et émet le jeu de seuils au quantile
choisi.

- **Cadence** : hebdomadaire au départ, manuel et versionné — un seuil qui change
  tout seul est un seuil qu'on ne peut pas corréler à un changement de
  comportement.
- **Fenêtre** : 14 jours glissants, plancher de 500 transitions par facteur
  faute de quoi le facteur conserve son seuil précédent.
- **Garde-fou** : une variation de plus de 50 % d'un seuil entre deux calibrations
  est signalée et non appliquée automatiquement — c'est le signe d'un changement
  de régime ou d'un collecteur en panne, pas d'une dérive normale.
- L'automatisation complète relève de la priorité 4 (calibration automatique), pas
  de ce chantier.

### 9.6 Configuration résultante

```
SONNET_DEDUP_DELTA_MOMENTUM=0.114     # p99 mesuré
SONNET_DEDUP_DELTA_VOLUME=0.049       # p99 mesuré
SONNET_DEDUP_DELTA_SENTIMENT=0.370    # p99 mesuré
SONNET_DEDUP_DELTA_LIQUIDITY=         # vide = désactivé (variance nulle)
SONNET_DEDUP_QUANTILE=0.99            # trace la provenance des valeurs ci-dessus
```

---

## 10. `MAX_AGE` — configurable et différencié

Le principe du garde-fou est retenu ; la valeur unique de 4 h ne l'est pas.

### 10.1 Différenciation par classe d'actif

`market_cap_rank` est déjà présent dans les features (mesuré : DEXE est au rang
150). Il sert d'axe naturel : une majeure dérive lentement et un état stable y est
informatif ; un petit actif peut se transformer en une heure.

| Classe | `market_cap_rank` | `MAX_AGE` par défaut | Raison |
|---|---|---|---|
| Majeures | ≤ 20 | **8 h** | régime lent, un état stable a du sens |
| Établies | 21 – 200 | **4 h** | cas médian, valeur d'origine |
| Petites | > 200 ou inconnu | **2 h** | volatiles, un état stable vieillit vite |

Les bornes et les durées sont exposées en configuration
(`SONNET_DEDUP_MAX_AGE_MAJOR` / `_MID` / `_SMALL`, `SONNET_DEDUP_RANK_MAJOR` /
`_MID`). Rang inconnu → classe la plus prudente.

**Ces trois durées sont des jugements, pas des mesures** — contrairement aux
`DELTA`, aucune donnée actuelle ne les étaye. La question « un état stable depuis
N heures mérite-t-il un nouvel avis ? » ne se tranche que par le rendement
constaté, donc par le journal contrefactuel. Elles sont configurables précisément
pour être révisées à ce moment-là.

### 10.2 Signal pour la révision future

Chaque appel déclenché par T7 est étiqueté comme tel. Le journal contrefactuel
pourra alors répondre : **les appels T7 produisent-ils des validations Sonnet, ou
seulement des rejets ?** S'ils ne produisent que des rejets, `MAX_AGE` est trop
court et coûte du budget pour rien.

---

## 11. T6 — report confirmé, extension conservée

Le report est validé. La conception reste au dossier et ne doit pas être perdue :

- T6 **reviendra** avec la file de priorité (§5), pas avant.
- La contrainte structurante est explicite : **un changement macro ne doit jamais
  provoquer un flot d'appels Sonnet.** Le mécanisme est déjà spécifié — le
  changement de régime *marque* les ancres comme périmées et les rend éligibles ;
  c'est la file de priorité qui décide de l'ordre et le budget qui décide du
  volume. À aucun moment une transition de marché ne doit court-circuiter le seau
  de jetons.
- Le code livré porte le champ `regime` dans l'ancre et le compare, mais le
  déclencheur est inerte tant que `SONNET_DEDUP_REGIME_ENABLED=false`. Le champ
  est écrit dès maintenant pour que l'historique soit exploitable le jour où T6
  s'active.

---

## 12. Mode ombre : mesurer avant de remplacer

**Le cooldown n'est pas retiré à la livraison.** Il reste l'autorité ; la
déduplication tourne à côté et journalise ce qu'elle *aurait* décidé. C'est ce qui
permet de démontrer le gain au lieu de l'affirmer.

### 12.1 Dispositif

À chaque évaluation d'escalade, les deux mécanismes sont interrogés. Seul le
cooldown décide. Une ligne est écrite dans `dedup_shadow` :

```
time, symbol, cooldown_verdict, dedup_verdict, dedup_trigger (T1..T7),
factors, anchor_factors, max_drift, score,
seconds_since_last_call, sonnet_outcome  -- validated | rejected | not_called
```

`sonnet_outcome` n'est renseigné que lorsque le cooldown a autorisé l'appel : il
est la vérité terrain disponible.

### 12.2 Les quatre cellules

|  | dédup : **appeler** | dédup : **ignorer** |
|---|---|---|
| **cooldown : appeler** | accord — aucun effet | **① Appels évités** |
| **cooldown : ignorer** | **② Détection plus précoce** | accord — aucun effet |

**① Appels évités** — le cooldown a payé, la déduplication non. Croisé avec
`sonnet_outcome` :
- verdict `rejected` → **économie légitime**, l'appel n'apportait rien ;
- verdict `validated` → **signal manqué**. C'est la métrique de sûreté critique.

**② Détection plus précoce** — la déduplication aurait appelé pendant que le
cooldown bloquait. On enregistre `seconds_since_last_call`, ce qui donne
directement le gain de latence. C'est le bénéfice principal attendu, et il est
invisible dans un compteur d'appels.

### 12.3 Critères de bascule

Le cooldown n'est retiré que si, sur **au moins 7 jours** :

| Critère | Seuil | Ce qu'il protège |
|---|---|---|
| Signaux manqués (① + `validated`) | **0** | non-régression — critère bloquant |
| Économie légitime (① + `rejected`) | > 30 % des appels | le gain en gaspillage est réel |
| Détections plus précoces (②) | > 0, gain médian mesuré | le gain de réactivité est réel |
| Déclenchements par cause | T1…T7 tous représentés ou expliqués | aucun déclencheur n'est mort |

**Un seul signal manqué bloque la bascule** et renvoie à la calibration des
seuils. Le coût d'un appel superflu est de quelques centimes ; celui d'une
inversion ratée est une position.

### 12.4 Machinerie partagée avec le journal contrefactuel

Ce dispositif est un cas particulier du journal contrefactuel : enregistrer ce
qu'un mécanisme *aurait* décidé, puis le confronter à l'issue réelle. Les deux
partagent le même schéma de base et le même principe.

Le journal contrefactuel doit donc être conçu en connaissant ce besoin, pour
éviter d'écrire deux fois la même chose. C'est le chantier suivant, et cette
section en est une contrainte d'entrée.

---

## 13. Questions ouvertes pour l'opérateur

1. **Quantile p99** (51 déclenchements/jour, 18 % du budget) — recommandé.
   p99,5 est plus conservateur (28/jour) si tu préfères démarrer serré ; p95
   (246/jour) dépasserait le budget quotidien et n'est pas retenu.
2. **`MAX_AGE` par classe (8 h / 4 h / 2 h)** — ce sont des jugements, pas des
   mesures. À réviser dès que le journal dira si les appels T7 produisent des
   validations ou seulement des rejets.
3. **Durée du mode ombre : 7 jours minimum.** Plus long donne une meilleure
   confiance sur le critère bloquant « zéro signal manqué », qui est le seul à ne
   tolérer aucune exception.
