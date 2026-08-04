# Ouvrir la vanne de décision — design

**Date :** 2026-08-04
**Statut :** validé, prêt pour le plan d'implémentation

## Objectif

Poser `DECISION_THRESHOLD` et `RISK_MIN_SCORE` sur des valeurs mesurées, pour que le
pipeline recommence à produire des décisions. Le seuil est fermé depuis le
2026-08-01 (`DECISION_THRESHOLD=101`, inatteignable sur une échelle 0-100) en
attendant une calibration sur les features v2 réelles.

Cet objectif n'est pas atteignable en l'état, et le constat qui suit est le point de
départ du spec.

## Le constat qui commande l'ordre du travail

Mesuré le 2026-08-04 sur la base de production (`<VPS_USER>@<VPS_HOST>`,
`docker exec bottrading-postgres-1 psql -U cmi -d cmi`), sur les **1 281 511** lignes
de `decision_journal` des 7 derniers jours :

| axe | poids | lignes où la feature est présente |
|---|---|---|
| `positioning` (`funding_rate_8h`) | 0,1380 | **0** |
| `developer_activity` (`commit_ratio_4w`) | 0,0800 | **0** |
| `fundamentals` (`tvl_change_pct_7d`) | 0,0920 | 46 205 (3,6 %) |
| `liquidity_score` (`volume_24h_usd`) | 0,1035 | 1 281 511 (100 %) |

**L'axe `positioning` n'a jamais produit une seule lecture en production.**
`collector-binance-futures` échoue à **100 % de ses cycles** depuis son déploiement —
24 échecs en 2 h, un par cycle — tout en se déclarant `healthy` pendant 28 heures.

```
periodic task 'binance-futures-poll' tick failed
  cmi_common/db/universe.py:39  priced_symbols()
  asyncpg.DataError: invalid input for query argument $1
  [SQL: SELECT DISTINCT prices.symbol FROM prices WHERE prices.time >= $1::TIMESTAMP WITHOUT TIME ZONE]
```

`developer_activity` est à zéro pour une autre raison, bénigne : l'image déployée
(`eb3e808`) est antérieure au merge de `collector-github`.

**Conséquence sur l'objectif.** Le modèle exclut un axe absent au lieu de le pénaliser.
Calibrer aujourd'hui produirait un seuil valable pour un modèle à 5 axes sur 8, et ce
seuil deviendrait faux dès que les collectors se remettent à parler — sans erreur,
sans log, sans test rouge. C'est la forme de défaut que ce projet a déjà payée dix-huit
fois. Le spec se déroule donc en deux temps : **A** répare et rend visible, **B**
calibre sur des données complètes.

**Deuxième conséquence, sur la méthode.** « Préserver le taux de passage actuel »,
que proposait `2026-08-01-derivatives-fundamentals-RESUME.md`, n'est pas une cible
atteignable. Ce taux vaut zéro par construction depuis le 1er août, et il valait déjà
1 décision pour 7 735 analyses en juillet. C'est le deadlock, pas une référence.

## Périmètre

**Dans le périmètre :** le correctif du défaut tz et son test de régression, la
visibilité des échecs de tâches périodiques, le portage de `market_sentiment` dans les
features, l'extraction de `features_from` en fonction pure partagée, et
`scripts/pick_threshold.py`.

**Hors périmètre, explicitement :**

- **La revue des tâches T7–T14 de scoring v2.** Jamais passées par la revue en deux
  passes qui avait trouvé quatorze défauts sur T1–T6. Risque connu, tracé dans le
  RESUME, traité séparément.
- **La sémantique de `RISK_MIN_SCORE` face aux deux populations qu'il filtre.**
  Mesurée et signalée ici (§ B4), pas résolue : c'est une décision de stratégie.
- **Le branchement de `promote_list_entries` sur `coin_repo_map`.** Autre limite
  assumée, conditionnée à la mesure de couverture CoinGecko.

---

# Partie A — réparer et rendre visible

## A1 — Les quatre déclarations temporelles

Quatre modèles déclarent `Mapped[datetime]` nu là où les quinze autres colonnes
temporelles du fichier utilisent `DateTime(timezone=True)` :

| modèle | ligne | colonne en production |
|---|---|---|
| `Price.time` | `models.py:54` | `timestamp with time zone` |
| `Signal.time` | `models.py:70` | `timestamp with time zone` |
| `PipelineRejection.time` | `models.py:93` | `timestamp with time zone` |
| `DecisionJournal.time` | `models.py:111` | `timestamp with time zone` |

SQLAlchemy caste donc le paramètre en `TIMESTAMP WITHOUT TIME ZONE` alors que la
colonne est `timestamptz`. Les écritures passent, les lectures avec un datetime
*aware* explosent à l'encodage, avant même d'atteindre la base.

**Aucune migration.** Les colonnes sont déjà correctes en production, vérifiées table
par table. On corrige la croyance de l'ORM, pas le schéma.

**Corollaire : `_naive_utc` disparaît.** `api-gateway/app/persister.py:50` contient
déjà un contournement de ce même bug, côté écriture, sous une docstring qui affirme
l'inverse de la réalité (« the tz-naive TIMESTAMP columns »). Il dépouille le fuseau
avant insertion. Il n'a pas corrompu de données parce que `TimeZone = UTC` côté serveur
— vérifié — mais cette innocuité repose sur un réglage de session que personne n'a
déclaré. La fonction et ses trois appels partent avec le correctif.

**`DecisionJournal.time` est dans le lot**, et c'est ce que la partie B interroge. Le
script de calibration aurait heurté le même mur.

**Le test qui manquait.** Un test par modèle qui interroge avec un datetime *aware*.
Il échoue aujourd'hui sur les quatre et passe après. C'est la seule garantie que la
déclaration et la colonne ne redivergent pas : rien d'autre dans la suite ne les
confronte, et c'est précisément pour ça que la divergence a vécu.

## A2 — L'échec périodique cesse d'être muet

`run_periodic` (`libs/cmi_common/cmi_common/runner.py:31`) avale toute exception. Le
comportement est correct — un tick raté ne doit pas tuer la boucle — mais il ne
s'accompagne d'aucune trace exploitable : pas de compteur, pas de métrique, aucun
effet sur `/health`.

- **Un compteur partagé** dans `cmi_common.observability`, là où vivent déjà les
  constantes de noms de métriques — précisément parce qu'un nom divergent
  (`events_consumed_total` contre `cmi_events_consumed_total`) a fait servir « 0 »
  pour « non mesuré » pendant des mois. Labels `service` / `task` / `status`.
- **Un registre en mémoire** dans `runner.py` : par tâche, échecs consécutifs, dernier
  succès, dernière erreur.
- **`/health` consulte ce registre.** `create_app` sert le même `/health` pour tous les
  services. Au-delà de **3 échecs consécutifs**, il répond **503** avec la liste des
  tâches en cause.

**Pourquoi 3, et pourquoi 503.** Sur un cycle de 5 min, trois échecs consécutifs valent
15 minutes de panne intégrale avant l'alerte : un rate-limit transitoire ne fait pas
clignoter, et on ne reproduit pas les 28 heures. Le `HEALTHCHECK` des trois Dockerfile
utilise `curl -fsS`, qui échoue sur tout code ≥ 400 : un 503 bascule donc le conteneur
en `unhealthy`. Et comme `restart: unless-stopped` ne redémarre pas sur ce motif, et
qu'aucun `depends_on: service_healthy` ne porte sur les services applicatifs, la panne
devient visible sans provoquer de boucle de redémarrage ni bloquer un déploiement.

**Ce que ça ne couvre pas.** La santé des tâches, pas leur rendement. Un collector qui
tourne sans erreur et ne publie rien reste invisible ici — c'est exactement
`fundamentals` à 3,6 %. Ce trou se traite par la présence par axe (§ B3), pas par la
santé des tâches, et les deux sont nécessaires.

---

# Partie B — fidélité du rejeu, puis calibration

## B1 — `market_sentiment` voyage avec les features

Le decision-engine tient la lecture de régime en mémoire (`self._market`, TTL 3600 s)
depuis le topic `sentiment`. Rien ne l'écrit, donc rien ne la rejoue. Elle n'est pas
décorative : pour les lignes sans sentiment propre — **34,0 %**, mesuré sur les 276 966
lignes des dernières 24 h — elle décide
si l'axe `news_score` (13,8 % du poids) est **présent** ou **exclu**. Elle déplace donc
le score *et* le poids présent, ligne par ligne.

`ai-worker-haiku` consomme déjà ce topic et range même la valeur : `features:MARKET`
existe en production et est vivante. Elle n'est jamais lue, parce que `_ready()` refuse
de scorer un symbole sans prix — ce qui est correct pour MARKET.

**Le design :** au flush, haiku lit la lecture de régime et l'estampille dans le dict
`features` qu'il publie. Elle atterrit dans `decision_journal.features` **sans
migration** (colonne JSONB), et le decision-engine la lit dans `raw` au lieu de la
tenir.

Deux conséquences.

**Le moteur perd son unique état.** `score()` étant déjà pur, la décision devient une
fonction pure de la ligne de journal : le recompute cesse d'être une approximation pour
devenir un rejeu exact. La propriété tient pour tous les recalibrages futurs, pas
seulement celui-ci. La souscription au topic `sentiment` devient morte et part avec.

**Le TTL ne doit pas être celui du FeatureStore.** `FEATURE_TTL` vaut 900 s. Les mises
à jour de l'agrégat MARKET arrivent, mesuré sur 12 h, **toutes les 10 à 30 minutes**
(2 à 6 par heure). Un TTL plus court que la cadence d'alimentation rendrait la clé
absente une bonne partie du temps et ferait disparaître `news_score` pour des lignes
qui le gardent aujourd'hui — une régression silencieuse, de la forme exacte que le
CLAUDE.md décrit. La lecture de régime prend donc **sa propre clé à 3600 s**, qui
reproduit la sémantique actuelle du moteur au lieu d'en hériter une par accident.

## B2 — `features_from` cesse d'exister en double

Le mapping `raw → Features` (`decision-engine/app/engine.py:138-160`) devient une
fonction pure exportée, appelée par le moteur **et** par le script. Le RESUME notait
« keep them in one place if this script outlives the deploy » : elle y est d'emblée,
sinon le script mesure un modèle que la production n'exécute pas.

**Un point de rejeu à corriger au passage.** `_unlock_days` calcule les jours restants
contre `datetime.now()`. Rejoué une semaine plus tard, chaque `next_unlock_at` est dans
le passé, la fonction retourne `None` et le terme disparaît. La fonction prend donc un
instant de référence explicite : `now()` en production, `row.time` en rejeu.

## B3 — `scripts/pick_threshold.py`

Lecture seule, exécuté sur le VPS dans le conteneur decision-engine, qui porte déjà
`app.scoring` et l'accès base. Sortie, dans cet ordre :

1. **Le taux de présence par axe**, et la part de lignes rejetées par
   `_MIN_PRESENT_WEIGHT`. En premier, pas en annexe : c'est le rapport qui aurait fait
   tomber le défaut d'aujourd'hui en une ligne.
2. La distribution des scores et des confiances recomputés.
3. Pour le **débit cible en décisions par jour** que choisit l'opérateur, le
   `DECISION_THRESHOLD` correspondant — **et** le nombre de symboles distincts par jour
   à ce seuil. Les deux, parce qu'un même symbole peut franchir le seuil huit fois par
   heure : 200 décisions/jour peut vouloir dire 200 opportunités ou 12.
4. L'effet de `RISK_MIN_SCORE` sur cette sous-population.

**Le seuil est un choix d'opérateur, pas une sortie de formule.** Le volume impose le
cadrage : ~11 500 analyses/heure, soit ~276 000 par jour, contre `MAX_ORDERS_PER_HOUR=10`
en aval. Même le 99,9ᵉ percentile laisserait passer ~276 décisions/jour. Ce n'est pas
un réglage de finesse, c'est une vanne de débit, et le débit se décide.

**Le script refuse de sortir un seuil si un axe est à 0 % de présence.** Sans ce
garde-fou il aurait produit aujourd'hui un nombre parfaitement plausible et faux — ce
qui est le seul mode d'échec que ce spec existe pour empêcher.

## B4 — Ce que ce spec mesure sans le trancher

`RISK_MIN_SCORE` filtre **deux populations aux sémantiques différentes** : les décisions
du decision-engine, sur l'échelle v2 à huit axes, et celles d'`ai-worker-sonnet`, dont
le score sort d'un LLM. Un seul nombre pour les deux. Le calibrer sur la première le
rend arbitraire pour la seconde — et c'est déjà ce qui bloquait en juillet, quand
l'unique décision validée par Sonnet (score 52, confiance 0,45) était refusée
simultanément sur le score et sur la confiance.

Le script mesure l'effet du plancher sur chacune des deux populations et le rapporte.
Le résoudre demande de décider si une décision déjà validée par un analyste doit
repasser le même plancher qu'une décision déterministe. C'est une décision de stratégie,
et elle ne se prend pas depuis un script de calibration.

Reste également ouvert, hérité et non traité ici : `risk-engine/app/rules.py:98` dimensionne
les positions par `min(1.0, confidence)`, sans variable d'environnement. Les symboles
qui n'ont que les cinq axes historiques plafonnent à **0,69** de confiance — la somme
des poids de ces cinq axes après les deux rescalages — et sont donc dimensionnés
**31 % plus petit** qu'avant v2, où les mêmes axes sommaient à 1,0. Le RESUME du
1er août chiffrait 0,75 et ~25 % : c'était vrai à sept axes, et l'ajout du huitième
l'a périmé sans que rien ne le signale. Signalé, pas corrigé.

---

## Vérification

**Tests unitaires** sur les parties pures : `features_from` avec instant de référence
explicite, la clé de régime et son TTL de 3600 s, le registre de santé et son seuil de
trois échecs.

**Tests de régression tz** : un par modèle, interrogeant avec un datetime *aware*.
Ils doivent échouer sur le code actuel — un test de régression qu'on n'a pas vu rougir
n'a rien démontré.

**Harnais live**, dans la lignée de `verify_read_live.py` et `verify_github_activity.py`,
après déploiement :

- la présence par axe est non nulle sur `positioning` et `developer_activity` ;
- `/health` répond 503 sur un service dont on force l'échec de la tâche périodique ;
- `market_sentiment` apparaît dans `decision_journal.features` ;
- le rejeu d'une ligne fraîche par `features_from` + `score()` reproduit le score que
  la production a émis pour cette ligne. C'est le test qui vaut pour tous les autres :
  si le rejeu est exact, le seuil calculé porte sur le modèle réellement exécuté.

## Séquence de déploiement

L'ordre compte, et l'étape 4 est la seule qui change le comportement de trading.

1. Déployer A et B1–B2 ensemble. Le correctif tz débloque `collector-binance-futures`
   et `collector-kraken` ; `market_sentiment` commence à être journalisé.
2. Vérifier sous 30 min que `positioning` se peuple, et que `collector-github` — absent
   de l'image déployée — arrive avec ce déploiement.
3. Laisser tourner **~24 h**, le temps que la fenêtre de calibration contienne des
   lignes à huit axes.
4. Lancer `pick_threshold.py`, choisir le débit, poser `DECISION_THRESHOLD` et
   `RISK_MIN_SCORE` **dans le même changement**. Ce sont des variables d'environnement :
   un redémarrage suffit, pas un redéploiement.
5. Observer le taux de décision sur 24 h. Retoucher le seuil reste un changement
   d'environnement.
