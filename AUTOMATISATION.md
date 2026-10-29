# Nouveaux rappels : dépôt et traitement automatiques

## Utilisation

1. Ouvrir le dashboard local **http://127.0.0.1:8501** et choisir **Nouveau rappel**.
2. Joindre la fiche officielle RappelConso et la communication de l'entreprise : PDF ou HTML UTF-8, 20 Mo au maximum par document.
3. Laisser l'identifiant vide pour une attribution automatique, ou saisir un nouvel identifiant de 3 à 10 chiffres (par exemple `066`). Les liens de provenance sont facultatifs et ne déclenchent aucun téléchargement.
4. Cliquer **Envoyer et analyser**. Le dépôt complet est publié ; le worker le détecte lors de sa prochaine passe (toutes les 10 secondes).
5. Suivre les étapes dans le tableau sous le formulaire. Les résultats deviennent visibles dans les autres vues après le chargement DWH ; les vues se rafraîchissent automatiquement lorsqu'un fait DWH est ajouté ou mis à jour.

Le worker utilise une seule API, définie par `OPENAI_API_KEY`, `OPENAI_MODEL` et
`OPENROUTER_BASE_URL` dans `.env`. Ses quotas et tarifs s'appliquent. Les anciennes
variables `LLM_API_2_*` et `LLM_API_3_*` sont ignorées : aucune bascule n'est effectuée.
`LLM_DISABLE_REASONING=1` permet de désactiver le raisonnement si le fournisseur le prend en charge.
Les anciens résultats gardent leur modèle et leurs métadonnées d'API d'origine ;
la configuration unique s'applique aux prochaines analyses.

Le SDK est configuré avec `max_retries=0`, conformément à la
[documentation officielle OpenAI Python](https://developers.openai.com/api/reference/python).
Aucune clé API n'est saisie dans le formulaire. La consultation des résultats ne déclenche pas d'inférence.

## Lancement

Sur l'installation existante mise à jour, le processus a été lancé en arrière-plan.
Après fermeture du programme ou redémarrage du PC, lancer depuis la racine du projet :

```powershell
.\Lancer_dashboard.ps1
```

Ce script démarre le worker en arrière-plan puis Streamlit. Il ne crée pas de tâche
planifiée Windows et ne lance pas automatiquement ETICS au démarrage du PC.
Le verrou PostgreSQL empêche un second worker de traiter la même file.

Lancement séparé, dans deux terminaux :

```powershell
.venv\Scripts\python.exe -m src.automation
.venv\Scripts\python.exe -m streamlit run dashboard/app.py
```

Le premier processus reste en cours de surveillance ; utiliser le second terminal
pour Streamlit. `Lancer_traitement_auto.ps1 -ArrierePlan` lance aussi uniquement le worker,
sans fenêtre visible. Ses journaux se trouvent dans `runtime/` (exclu de Git).
Une passe unique : `python -m src.automation --once`.

Pour installer cette étape dans une autre copie existante du projet, sauvegarder et
appliquer la migration additive `004_pipeline_jobs.sql` avec le parcours :

```powershell
.venv\Scripts\python.exe -m src.database.upgrade --backup-dir backups
```

Les trois migrations précédentes restent inchangées. La mise à jour vérifie la
conservation des lignes sources et charge le DWH de manière idempotente. Le parcours
de sauvegarde fonctionne aussi avec l'espace Unicode du chemin Windows du projet.

## Ce que fait le traitement

```text
Formulaire → dépôt atomique dans data/raw/<identifiant>/
           → file persistante pipeline_jobs
           → extraction PDF/HTML → textes → JSON traité
           → rappel + versions immuables des sources
           → analyse LLM → contrôle des citations → scoring si conforme
           → ETL PostgreSQL dwh → dashboard
```

L'orchestrateur réutilise les fonctions du pipeline existant et SQLAlchemy. Il n'écrase
pas les extractions ou les analyses précédentes et ne relance pas les 65 cas du corpus.
Seuls les dépôts complets issus du formulaire, munis d'un manifeste `.etics-ready.json`,
sont automatiquement placés en file.

Le dossier temporaire de dépôt porte un nom caché `.intake-...`. Les deux documents,
leur provenance et leurs empreintes sont écrits avant un renommage atomique. Un
dépôt interrompu avant publication est ignoré. Les fichiers déjà publiés sont
revérifiés avant toute analyse. Les dossiers temporaires ne sont pas effacés automatiquement.

Le formulaire utilise les transactions de lecture PostgreSQL existantes pour vérifier
les identifiants et sérialiser les dépôts avec un verrou transactionnel. Il écrit
uniquement les nouveaux fichiers ; le worker écrit les états et les données analytiques.
Un compte `DASHBOARD_DATABASE_URL` dédié doit aussi pouvoir lire les tables
`pipeline_jobs` et `pipeline_workers` (en plus des tables déjà utilisées par le dashboard).

## Doublons et reprises

- Un identifiant déjà présent en base, dans la file, dans les dossiers bruts/extraits
  ou dans les JSON est refusé. Les variantes numériques comme `066` et `00066` sont
  considérées comme le même identifiant pour le dépôt.
- Une paire de documents strictement identique est refusée même si les noms de fichiers
  ou le nouvel identifiant diffèrent. La comparaison utilise les SHA-256 des fichiers
  du dépôt et des versions documentaires existantes. Ce contrôle n'est pas une détection
  sémantique de tous les doublons de produit ou de fiche officielle.
- Un seul worker détient le verrou PostgreSQL. Une analyse déjà enregistrée est
  réutilisée à la reprise : aucune nouvelle inférence n'est nécessaire pour reprendre
  le scoring ou le chargement DWH.
- Si les citations ne passent pas le contrôle, l'état final est **Terminé · citations
  à revoir** ; les réponses sont conservées et le score/verdict restent NULL. Ce cas
  n'est pas automatiquement renvoyé au LLM.
- Si l'API unique est indisponible, le worker attend au moins 15 minutes avant une nouvelle
  tentative ; maximum 3 tentatives par traitement. La pause s'applique aussi aux autres
  dépôts, et persiste après redémarrage. Un `Retry-After` plus long est respecté.
  Chaque tentative utilise la même API ; aucune autre clé n'est essayée. Les reprises
  automatiques du SDK sont désactivées (`max_retries=0`).
- Si l'ETL échoue après l'enregistrement de la réponse, la reprise est tentée après
  30 secondes, sur cette même réponse.
- Une interruption pendant l'appel LLM, sans réponse persistée, est signalée
  **Analyse interrompue · reprise à autoriser**. Le worker ne peut pas déterminer si
  le fournisseur a déjà répondu ou facturé ; il ne répète pas automatiquement cet appel.
- Pour une reprise explicitement décidée par l'opérateur :
  `python -m src.automation --retry 066 --once`. Un worker déjà actif prendra la reprise
  en charge à sa prochaine passe. Les documents et analyses existants ne sont pas supprimés.

Les erreurs d'extraction, de fichier modifié, de configuration ou de réponse mal formée
sont visibles dans la file avec leur type. Aucun message brut d'API ni clé n'est affiché.
Le formulaire ne permet pas de remplacer les sources ou de réanalyser un ancien rappel :
la gestion explicite de nouvelles versions reste distincte de l'ajout d'un nouveau cas.

## États enregistrés

| État technique | Signification |
|---|---|
| queued | Dépôt complet détecté et enregistré en file |
| preparing | Extraction, préparation et conservation des sources |
| analyzing | Appel LLM commencé |
| dwh_pending | Réponse conservée ; scoring ou ETL à terminer |
| completed | Analyse et DWH terminés |
| needs_review | Réponse conservée, citations à revoir, DWH alimenté sans score |
| retry_wait | API indisponible, reprise différée |
| failed | Erreur ou nombre maximal de tentatives atteint |
| interrupted | Appel possiblement exécuté, reprise explicite nécessaire |

`pipeline_workers` conserve la dernière activité, le PID et la pause API du worker.
Pendant un appel long, la dernière activité peut rester celle du début du traitement.
Le formulaire consulte le suivi toutes les 10 secondes sans réinitialiser les champs.
Les vues de résultats surveillent les changements du DWH via un fragment Streamlit.

## Vérification

`tests/test_automation.py` utilise des dossiers temporaires et des bases PostgreSQL
jetables `etics_test_<uuid>`. Il vérifie la chaîne complète, les dépôts concurrents,
les doublons, les empreintes modifiées, le verrou worker, les reprises API/ETL,
l'interruption ambiguë et l'état à revoir. Les clients LLM de ces tests sont simulés :
aucune donnée fictive n'est injectée dans le corpus métier et aucun appel réel payant
n'est déclenché pour la validation de cette étape.

`tests/test_dashboard.py` vérifie aussi le formulaire et le refus d'un dépôt incomplet.
Les tests PostgreSQL de sauvegarde incluent un chemin Unicode ; le test du rapport CLI
vérifie la compatibilité avec une console Windows cp1252.

Le rapport de livraison indique le résultat réellement exécuté et la vérification
de conservation des 65 rappels et 67 analyses lors de la migration locale.
