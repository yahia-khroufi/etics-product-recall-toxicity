# ETICS : historique opérationnel et Data Warehouse PostgreSQL

L'implémentation prolonge le pipeline Python/SQLAlchemy existant. Elle ne lance aucun LLM depuis l'ETL et ne recalcule pas les analyses historiques. Les scripts de migration ajoutent des structures ; ils ne suppriment aucune donnée métier.

## Trois historiques distincts

1. **Analyses** : chaque ligne de `llm_results` est une exécution d'analyse, même si les documents sont inchangés. L'entrepôt conserve une ligne de faits par `llm_results.id`.
2. **Documents observés** : `documents` identifie le rôle officiel/entreprise d'un rappel. `document_versions` conserve le texte exact envoyé au LLM, les empreintes, URL, métadonnées et la date de capture. Un changement de contenu ou de provenance ajoute une version. Deux nouvelles analyses d'un même contenu réutilisent la version. Un retour A → B → A crée une troisième observation.
3. **Données descriptives observées** : `recall_snapshots` conserve les changements des champs d'un rappel lors de `save_recall`. Ces snapshots alimentent les dimensions SCD Type 2. Une correction d'extraction ou de marque n'est pas présentée comme une modification de la communication.

`observed_at` est une date d'observation par le système. Ce n'est ni une date de modification publiée par l'entreprise, ni une date de validité juridique. Aucune observation n'est antidatée.

Les sources archivées avant cette implémentation ne permettent pas de reconstruire les anciennes versions des communications. Une analyse ancienne garde `run_id`, `official_version_id` et `company_version_id` NULL. Même si le fichier actuel existe, il n'est pas automatiquement rattaché à cette analyse.

## Corrections du socle

- `001_communication_toxicity.sql` ajoute le pourcentage et sa contrainte, rend les résultats dérivés NULLables et conserve les champs historiques. Pour une analyse déjà scorée sans pourcentage, il calcule uniquement l'indice dérivable des sept booléens, identifié par `toxicity_score_origin=derived_on_migration:seven-equal-weights-v1`. Il ne s'agit pas d'une ancienne mesure retrouvée.
- `002_provenance.sql` ajoute snapshots, documents et exécutions. Les snapshots de document et de rappel sont protégés contre UPDATE/DELETE par des triggers PostgreSQL.
- Les citations vides ou composées d'espaces sont refusées. Q1-Q6 sont vérifiées uniquement dans la communication. Une réponse Q7 positive nécessite des citations propres à chacune des deux sources ; les cas indécidables vont en revue.
- Une analyse dont les preuves échouent est stockée avec `analysis_status=needs_review` et reste sans score/verdict. Ce statut signifie vérification requise, pas communication non toxique.
- Le calcul reste déterministe et conserve la règle locale incluant Q2/Q5 : `etics-seven-criteria-v1`. Le score de complétude est entre 0 et 1 ; l'indice à sept critères est entre 0 et 100 ; la sévérité est une classe ordinale de 0 à 3. Le pourcentage n'est pas une probabilité de toxicité.
- Une analyse déjà scorée n'est pas recalculée par `score_llm_result`. Changer les règles nécessite une nouvelle version et de nouveaux résultats ; l'ancien verdict ne doit pas être réécrit.
- Le prompt réel est versionné dans `prompts/questionnaire_v4_evidence.md` et son empreinte est enregistrée. Les prompts antérieurs ne sont pas attribués artificiellement aux anciens runs.

## Traçabilité d'une nouvelle analyse

`analysis_runs` stocke le modèle demandé, l'identifiant de modèle rapporté par le fournisseur quand disponible, le prompt et son hash, la version de scoring, les paramètres, le commit Git, l'état dirty du dépôt, les dates et l'état d'exécution. L'identifiant rapporté peut rester un alias : il ne garantit pas une révision immuable des poids.

Avant l'appel LLM, les deux versions documentaires et le snapshot descriptif sont validés et enregistrés. Après l'appel, les réponses sont enregistrées avant le calcul. Un échec du calcul conserve les réponses pour reprise. Un échec de l'appel est journalisé sans inventer de réponses. La vérification de présence d'une citation ne garantit pas sa pertinence sémantique : l'évaluation humaine reste nécessaire.

Les textes stockés sont les textes nettoyés réellement fournis au modèle. L'empreinte du fichier brut est distincte de celle du texte. `provenance.extraction_link` indique que le lien fichier→texte n'est pas revérifié par une nouvelle extraction au moment de la capture ; l'ETL ne prétend pas avoir effectué cette vérification. Les métadonnées de collecte sont copiées comme provenance, pas comme une vérité historique reconstruite.

Le texte historique est conservé en base ; les PDF/HTML binaires ne sont pas recopiés dans PostgreSQL. Il faut conserver leur archivage externe pour pouvoir rouvrir un ancien binaire. Un changement du texte extrait peut aussi venir de l'extracteur ; un changement du hash brut peut venir des seules métadonnées du PDF. La requête documentaire distingue donc changement de texte extrait, changement du binaire et changement de provenance, sans attribuer automatiquement une révision sémantique à l'entreprise.

## Modèle en étoile : schéma `dwh`

| Table | Grain / rôle |
|---|---|
| `fact_analysis` | Une analyse source, clé unique `source_result_id` ; réponses, preuves, mesures et verdict d'origine |
| `dim_company` | Version descriptive d'une entreprise, SCD2 |
| `dim_product` | Version du produit concerné par un rappel, SCD2 |
| `dim_date` | Jour UTC de l'analyse ; deuxième rôle facultatif pour la date de publication |
| `dim_model` | Modèle demandé + identifiant rapporté lorsqu'il est connu |
| `dim_prompt` | Version + empreinte du prompt ; hash inconnu pour l'historique non tracé |
| `dim_scoring` | Version de règle ; `legacy_unknown` pour les anciennes lignes sans provenance |
| `dim_document_version` | Version observée, rôle, empreintes et source |
| `dim_execution` | Exécution source, paramètres et métadonnées Git |
| `snapshot_dimensions` | Correspondance stable entre un snapshot opérationnel et ses clés SCD2 |
| `etl_runs` | Journal des chargements, volumes, succès ou type d'échec |

La vue `dwh.analysis_history` joint les dimensions usuelles. Une entreprise et un produit peuvent avoir plusieurs clés substituts au cours du temps. Les faits conservent les clés de leur contexte d'origine ; ils ne sont pas rejoints systématiquement à la version courante.

### Identités et SCD Type 2

`RecallData.organization_key` permet de fournir une identité stable explicitement connue, par exemple un SIREN préfixé ou un identifiant interne vérifié. Sans cette clé, l'identité entreprise est basée sur le nom normalisé, et une entreprise sans nom reste isolée au niveau du rappel. Une fusion automatique des variantes de nom n'est pas effectuée. Sans identifiant stable, un changement de nom peut donc créer une autre identité plutôt qu'une version de la même entreprise.

Le produit est identifié par le `case_id` du rappel. Plusieurs GTIN peuvent appartenir au même rappel ; on ne fusionne pas automatiquement deux produits entre rappels distincts. `product_name`, `brand` et `gtin` constituent les attributs suivis.

Chaque dimension SCD2 possède `valid_from`, `valid_to`, `is_current` et un hash des attributs. Ces bornes décrivent les observations système, avec intervalles `[valid_from, valid_to)`. Un changement ferme la version précédente et en ouvre une autre. Une seule version courante est autorisée par identité. Les observations hors ordre sont rejetées plutôt que d'inventer une chronologie.

Pour les analyses nouvelles, `context_origin=observed_run_snapshot` indique le snapshot descriptif effectivement capturé pour l'exécution. Pour les anciennes analyses, `legacy_current_context_unverified` indique que l'entreprise et le produit sont attribués depuis le premier contexte descriptif disponible : leurs attributs à la date de l'ancienne analyse sont inconnus. Cette limite doit figurer dans les graphiques historiques. Les références documentaires historiques inconnues restent NULL.

## Commandes de reprise

Pour une base existante contenant `llm_results`, la commande recommandée orchestre
la sauvegarde PostgreSQL, les migrations, l'ETL et la comparaison des anciennes
valeurs, sans relancer le preprocessing et sans changer les rappels :

```powershell
.\.venv\Scripts\python.exe -m src.database.upgrade --backup-dir backups
```

`src/database/upgrade.py` utilise `pg_dump` (dans le PATH, dans une installation
PostgreSQL Windows, ou indiqué par `ETICS_PG_DUMP`). Le mot de passe reste dans
l'environnement du processus de sauvegarde, pas dans ses arguments affichés.
La commande s'arrête avant toute migration si la connexion ou la sauvegarde
échoue. Elle conserve un rapport JSON avec l'empreinte de la sauvegarde, les
volumes, les migrations appliquées et le résultat des contrôles.

La comparaison vérifie chaque colonne préexistante des anciens rappels et
analyses, y compris réponses, preuves, mesures, verdicts et dates. Elle autorise
uniquement l'ajout d'un pourcentage auparavant absent lorsque sa formule à sept
critères est exacte et sa provenance de migration explicite. Aucune restauration
automatique destructive n'est effectuée si un contrôle échoue.

Depuis la racine du dépôt, utiliser l'environnement existant et sa variable `DATABASE_URL`. Le DSN doit viser la base voulue ; `.env` n'est jamais inclus dans une image Docker. Avant une application sur une base existante, conserver une sauvegarde PostgreSQL avec `pg_dump` adaptée à son installation.

```powershell
# Initialiser les tables absentes puis exécuter les migrations SQL.
.\.venv\Scripts\python.exe -m src.database.migrate

# Charger/actualiser les rappels depuis les JSON existants, sans appel LLM.
# Capture aussi les textes actuellement disponibles, sans les attribuer aux anciennes analyses.
.\.venv\Scripts\python.exe -m src.pipeline --from-processed

# Capturer de nouveau les sources actuelles, sans analyse et sans duplication si inchangées.
.\.venv\Scripts\python.exe -m src.database.capture --case 001

# Alimenter l'entrepôt à partir de TOUS les résultats source, anciens comme nouveaux.
.\.venv\Scripts\python.exe -m src.dwh.etl

# Vérifier l'idempotence : inserted=0 et updated=0 si les sources n'ont pas changé.
.\.venv\Scripts\python.exe -m src.dwh.etl
```

Le pipeline normal appelle aussi les migrations PostgreSQL au démarrage. Les migrations sont numérotées, contrôlées par SHA-256 et journalisées dans `schema_migrations`. Une migration appliquée ne doit plus être éditée : ajouter un nouveau fichier SQL pour la suite. Les scripts SQL sont eux-mêmes réexécutables si une interruption survient entre leur commit et l'enregistrement dans le journal.

`DATABASE_URL` utilise le format SQLAlchemy `postgresql+psycopg2://...`. Ne pas transmettre ce format tel quel à `psql`/`pg_dump`, qui attendent un DSN PostgreSQL natif.

## ETL : garanties et reprise

Le chargement s'effectue dans une transaction PostgreSQL `REPEATABLE READ`. Un verrou consultatif de session sérialise les chargeurs **avant** l'ouverture du snapshot de lecture. Les dimensions, correspondances et faits sont validés ensemble ; en cas d'échec ils sont annulés ensemble. Le journal conserve le chargement échoué et son type d'erreur.

L'ETL parcourt tous les résultats source pour détecter également les analyses anciennement en attente qui viennent d'être scorées. `source_result_id` est unique. Une empreinte détecte les changements ; un deuxième chargement sans changement n'ajoute ni fait ni version SCD. Les journaux de chargement, eux, s'ajoutent normalement.

Une analyse en attente peut être complétée dans le fait existant, sans doubler le grain. Une analyse finalisée qui a été modifiée directement dans la source provoque un échec explicite : son ancienne valeur dans le warehouse n'est pas écrasée. Il faut résoudre cette anomalie ou définir un nouveau mécanisme de révision explicite.

L'ETL ne supprime aucun fait lorsque des lignes source disparaissent. Les instantanés opérationnels append-only et leurs clés étrangères limitent aussi les suppressions accidentelles. Les nouvelles organisations ou données descriptives connues aujourd'hui ne sont jamais présentées comme connues à la date d'un vieux résultat.

## Requêtes d'analyse

`src/dwh/queries.sql` fournit sept familles :

1. évolution mensuelle des scores/verdicts par entreprise, produit et versions modèle/prompt/scoring, en séparant état de validation et origine du contexte ;
2. détail de tout l'historique, y compris les lignes anciennes non liées ;
3. comparaison d'analyses de la **même version de communication** ;
4. observations de changements des documents, basées sur les versions et empreintes ;
5. historique SCD2 entreprise et produit ;
6. dernière analyse validée par version documentaire, pour ne pas compter chaque réanalyse comme un autre document ;
7. couverture de la traçabilité et des preuves.

Ne pas agréger ensemble des règles de scoring différentes sans les identifier. Un ancien `evidence_verified=true` n'est pas une validation rétroactive par le nouveau vérificateur. Une évolution de score entre deux modèles ne démontre pas que l'entreprise a changé son communiqué.

## Tests

```powershell
# Tests sans serveur : SQLite et vérification des preuves.
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider

# Tests réels PostgreSQL : serveur de TEST isolé uniquement.
# Le compte doit pouvoir créer/supprimer des bases jetables et des schémas de test.
$env:ETICS_TEST_ADMIN_URL='postgresql+psycopg2://USER:PASSWORD@127.0.0.1:PORT/postgres'
$env:DATABASE_URL=$env:ETICS_TEST_ADMIN_URL
$env:TEST_POSTGRES='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Les nouveaux tests PostgreSQL créent des bases `etics_test_<uuid>`, puis les suppriment. Le test PostgreSQL préexistant crée un schéma temporaire dans la base de test. Ne pas configurer ces commandes avec un serveur métier. Les valeurs anciennes synthétiques utilisées dans les tests servent uniquement à vérifier migrations et historique ; elles ne sont jamais injectées dans la base métier ni dans un entrepôt livré comme réel.

Les fixtures du cas 001 sont désormais versionnées sous `tests/fixtures` pour que les tests ne dépendent plus du corpus ignoré par Git. Les tests couvrent la conservation des anciens champs/dates, les migrations répétées, les citations vides, Q7, les échecs d'appel et de scoring, les réanalyses sans changement de document, les changements documentaires observés, SCD2, l'idempotence et la concurrence des chargeurs, le rollback ETL et l'exécution des requêtes SQL.

Le chatbot, le RAG et une nouvelle interface applicative ne font pas partie de ce lot.

## Validation réellement effectuée le 28 octobre 2026

- Serveur isolé **PostgreSQL 17.10**, distinct de la base métier ; Python local 3.14.
- Suite complète mise à jour : **165 tests réussis, 1 ignoré**, dont **15 tests PostgreSQL exécutés**. Le test ignoré est la variante SQLite d'un test de migration spécifique à PostgreSQL ; sa variante PostgreSQL a réussi.
- Le nouveau parcours sauvegarde → migration → ETL → comparaison des données originales a été exécuté sur PostgreSQL, puis répété sans duplication. Les tests unitaires détectent notamment les modifications de verdict, score, preuve, disparition d'une ligne et pourcentage dérivé incorrect.
- Migrations 001 à 003 exécutées ; migration d'une structure ancienne testée avec conservation des réponses, preuves, score original, verdict, explication et date.
- Chargement réel des 65 JSON du projet : 65 rappels, 65 snapshots descriptifs, 130 documents et 130 versions initiales observées ; dimensions alimentées avec 48 identités entreprise basées sur les noms et 65 produits de rappel.
- Deux ETL successifs sans changement : aucun fait ni dimension dupliqué. L'idempotence avec des analyses non vides et la concurrence de deux chargeurs ont été vérifiées dans les tests PostgreSQL.
- Requêtes SQL exécutées avec succès. Sauvegarde PostgreSQL de la base de validation restaurée dans une autre base jetable ; comptes vérifiés après restauration.
- La base de validation réelle contient **0 analyse LLM et 0 fait d'analyse**. Elle n'inclut pas les fixtures synthétiques des tests. Aucun appel LLM payant n'a été lancé.
- Lors de la première validation, la base métier était inaccessible. Ce blocage a ensuite été résolu ; la mise en service réelle est décrite ci-dessous.

## Mise en service sur la base métier réelle

Le conteneur existant `etics-product-recall-toxicity-db-1` a été redémarré avec
son volume conservé. La base `etics_db`, PostgreSQL **16.15**, contenait deux
rappels (002 et 006) et **deux analyses LLM du 7 octobre 2026**.

Une sauvegarde a précédé les migrations 001 à 003. La commande de reprise a
importé les deux analyses dans `dwh.fact_analysis` et comparé toutes les colonnes
préexistantes des lignes originales : aucune différence. Aucun score ni verdict
historique n'a été recalculé ; aucun pourcentage n'a dû être dérivé sur ces deux lignes.

Les 63 rappels manquants ont ensuite été insérés depuis les JSON réels du corpus,
en laissant inchangés les deux rappels déjà présents. Les textes actuellement
disponibles ont été capturés pour les 65 cas. État constaté :

| Objet métier réel | Nombre |
|---|---:|
| Rappels et snapshots descriptifs | 65 chacun |
| Documents et versions observées | 130 chacun |
| Identités entreprise du DWH | 48 |
| Produits de rappel du DWH | 65 |
| Analyses historiques et faits | 2 chacun |
| Nouveaux appels LLM effectués | 0 |

Les anciennes analyses restent `legacy_unlinked`, sans lien documentaire
rétroactif, avec `scoring_version=legacy_unknown`. Leurs dates du 7 octobre
ne sont pas utilisées pour dater les documents capturés le 28 octobre.
Une ancienne analyse avait `evidence_verified=false` ; ce signal est conservé.
La présence d'un verdict ancien ne vaut donc pas validation par le nouveau vérificateur.

Plusieurs ETL successifs n'ont ajouté ni fait ni dimension en double. Les
requêtes analytiques ont été exécutées sur ces données réelles. La suite complète
a également été exécutée sur PostgreSQL 16.15, dans des bases et schémas jetables :
**165 tests réussis, 1 ignoré, dont 15 tests PostgreSQL exécutés**.

La sauvegarde de livraison de la base métier a été produite avec `pg_dump 16`
du conteneur, puis restaurée avec `pg_restore 16` dans une base jetable :
65 rappels, 130 versions documentaires et les deux analyses/faits vérifiés.
Pour une restauration, utiliser des versions de clients adaptées à l'archive ;
les sauvegardes de la commande native locale peuvent employer le client 17 installé.

Le dashboard reste à développer. Le chatbot et le RAG restent hors de ce lot.

## API de secours

Le client peut utiliser jusqu'à trois API compatibles avec le SDK OpenAI, dans
l'ordre configuré. La première conserve `OPENAI_API_KEY`, `OPENAI_MODEL` et
`OPENROUTER_BASE_URL`. Les suivantes utilisent les trois champs obligatoires
`LLM_API_2_KEY`, `LLM_API_2_BASE_URL`, `LLM_API_2_MODEL`, puis les équivalents
`LLM_API_3_*`. Les valeurs restent dans `.env`, ignoré par Git ; l'exemple versionné
ne contient aucune clé. Une API optionnelle non configurée n'est pas essayée.

Les erreurs 401/402/403/404 font passer à l'API suivante et désactivent l'API en
échec pour le lot. Les erreurs réseau, 408, 429 et 5xx déclenchent aussi la bascule,
avec un délai avant réutilisation de l'API : `Retry-After` lorsqu'il est valide,
60 secondes par défaut. Le client n'attend pas ce délai si une autre API peut
répondre ; il ne rappelle pas l'API limitée avant son échéance. Les retries internes
du SDK sont désactivés pour éviter de multiplier les tentatives.

Une réponse structurée absente ou un refus exprimé dans la réponse du modèle
arrête l'analyse ; ces cas ne déclenchent pas une autre génération. Une erreur
400 de requête est également remontée pour correction.

Le pipeline réutilise un seul client pendant le lot. Si toutes les API sont
indisponibles, il s'arrête : les cas restants ne sont pas envoyés. Les analyses
déjà enregistrées restent conservées. Le modèle effectivement utilisé, son
identifiant rapporté et la liste des tentatives sans clés sont enregistrés dans
`analysis_runs`; l'entrepôt utilise cette identité réelle. Les fournisseurs sont
identifiés par leur hôte API dans `parameters.api_provider`.

Il faut disposer de clés et de quotas utilisables pour chaque API configurée.
Cette configuration ne crée pas de comptes et n'ajoute pas de crédits.
Référence SDK : [OpenAI Docs — erreurs et retries](https://developers.openai.com/api/reference/python).

### Sorties structurées et budget de génération

`LLM_MAX_OUTPUT_TOKENS` permet de régler le budget de réponse sans modifier le code.
La valeur par défaut reste 1500 ; le lot gratuit utilise 4096. Pour un modèle qui
l'autorise, `LLM_API_2_DISABLE_REASONING=1` (ou `LLM_API_3_DISABLE_REASONING=1`)
envoie `reasoning.enabled=false`. Ces paramètres sont enregistrés avec l'exécution.
Un modèle à raisonnement obligatoire ne doit pas recevoir cette option.

Le modèle gratuit sélectionné pour le lot est
`nvidia/nemotron-3.5-super-vl:free`, annoncé compatible avec les sorties structurées
par le catalogue OpenRouter au moment de l'exécution. Les clés sont uniquement
dans la configuration locale. Le modèle réel et l'API ayant répondu restent
consultables dans les exécutions ; aucun résultat simulé n'est chargé en base métier.

Une réponse tronquée n'est pas présentée comme une analyse valide. Si les citations
ne sont pas vérifiées, les réponses brutes restent `needs_review` sans score ni
verdict. Cela permet une revue humaine ultérieure sans remplacer les anciennes analyses.

Documentation du budget de raisonnement :
[OpenRouter — Reasoning Tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Lot LLM réel du 28 octobre 2026

Les **65 cas** ont maintenant reçu une nouvelle analyse avec le modèle gratuit
`nvidia/nemotron-3.5-super-vl:free` et le prompt `questionnaire_v4_evidence`.
Les réponses ont été enregistrées : **48 sont scorées**, **17 restent à revoir**
parce que leurs preuves ne passent pas la vérification. Ces 17 analyses restent
sans score final ni verdict ; elles ne sont pas supprimées.

Les API de secours ont effectivement répondu : 52 analyses via le slot `api_2`,
13 via `api_3`. Les clés ne sont enregistrées ni dans les exécutions ni dans cette
documentation. Le timeout local de ce lot est 120 secondes. Après une interruption
réseau au cas 033, le traitement a repris sans renvoyer les 32 cas déjà enregistrés.

Les deux analyses anciennes sont inchangées. La base et l'entrepôt comptent donc
**67 analyses et 67 faits**, pour 65 rappels et 130 versions documentaires observées.
Les 65 nouveaux résultats ont des liens explicites vers leurs deux documents
d'entrée ; les deux anciennes analyses ne reçoivent toujours aucun lien rétroactif.
Les nouvelles analyses ne correspondent pas à des modifications des documents.

L'ETL a été répété sans insertion ni mise à jour supplémentaire. La sauvegarde
PostgreSQL 16 après le lot a été restaurée dans une base jetable et vérifiée avec
67 analyses/faits, dont 17 résultats en revue sans score final.

Les contrôles des citations sont une validation technique, pas une mesure de
précision scientifique du modèle : le corpus annoté et l'évaluation humaine restent
à construire. Chaque nouvelle exécution LLM ajoute une analyse ; l'ETL conserve
son propre caractère idempotent.
