# Dashboard ETICS — consultation PostgreSQL

Interface locale Python / Streamlit / SQLAlchemy sur le schéma `dwh` existant.
Elle consulte les résultats enregistrés et les documents réellement liés aux analyses.
Les vues de consultation ne lancent ni LLM ni écriture de revue humaine. Le formulaire
**Nouveau rappel** dépose les deux sources et confie le pipeline au worker indépendant ;
voir **AUTOMATISATION.md**. Aucune migration n'est lancée depuis l'interface.

## Démarrage sous Windows

Depuis la racine du projet :

```powershell
docker compose up -d db
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run dashboard/app.py
```

Ouvrir **http://127.0.0.1:8501**. Arrêter le serveur avec Ctrl+C dans son terminal.
Si l'environnement n'existe pas : `python -m venv .venv`, puis installer les dépendances.
Après installation, `./Lancer_dashboard.ps1` lance aussi le serveur ; `-Port 8502`
permet de choisir un autre port. Le lancement Python direct fonctionne sans changer
la politique d'exécution PowerShell.

La configuration `.streamlit/config.toml` garde le serveur sur `127.0.0.1` et désactive
les statistiques d'utilisation Streamlit. Le dashboard est destiné à la consultation
locale. Aucune publication Internet ni authentification multi-utilisateur n'est implémentée.

Le fichier `.env` du projet doit contenir `DATABASE_URL`. L'option
`DASHBOARD_DATABASE_URL` permet d'utiliser un compte PostgreSQL dédié en lecture seule.
Aucune clé LLM n'est nécessaire pour consulter l'interface.

Si le schéma `dwh` n'existe pas encore, suivre d'abord **DWH.md** et son parcours
de sauvegarde/migration. Après de nouvelles analyses, alimenter l'entrepôt depuis
le terminal avec `python -m src.dwh.etl`, puis cliquer **Actualiser les données**.
L'interface ne déclenche pas cet ETL.

## Vues

| Vue | Contenu |
|---|---|
| Vue d'ensemble | Compteurs, états, distribution de l'indice, résultats et export CSV |
| Nouveau rappel | Dépôt de deux documents, refus des doublons, analyse automatique en arrière-plan et suivi |
| Rappels | Choix d'analyse, Q1–Q7, score enregistré, citations, textes sources liés, versions et empreintes |
| À revoir | Analyses `needs_review`, motifs du contrôle lexical, absence de score et de verdict |
| Historique | Toutes les analyses filtrées d'un rappel, modèle/prompt/règles, points de score, versions documentaires observées |
| Traçabilité | Nombre de sources/faits, exécutions, 20 derniers ETL, dimensions entreprises et produits SCD Type 2 |

La recherche porte littéralement sur rappel, entreprise, produit et marque.
Les filtres entreprise, modèle, prompt, règles de scoring, statut et période portent
sur les analyses. La période et les dates du tableau sont en **Africa/Casablanca**.
Le graphique chronologique indique explicitement UTC ; ses infobulles donnent aussi
la date locale. Les observations documentaires portent leur fuseau enregistré.

Par défaut, seule la dernière analyse **par rappel dans la sélection filtrée** est retenue
(date puis identifiant pour départager). Le bouton **Toutes les analyses** inclut aussi
les anciennes. Filtrer d'abord un ancien modèle peut donc retrouver une ancienne analyse.
La vue Historique affiche toujours toutes les analyses du rappel qui passent les filtres.
La vue Traçabilité affiche des compteurs et dimensions globaux, explicitement annoncés.

Les données sont mises en cache pendant 30 secondes. Les vues de résultats surveillent
les modifications du DWH toutes les 10 secondes et se rechargent si nécessaire.
Le suivi sous le formulaire se met à jour séparément, sans effacer les champs.
L'actualisation vide le cache
de lecture ; elle n'interroge aucun LLM. Les CSV utilisent `;` et UTF-8 avec BOM ; les
cellules textuelles commençant par un indicateur de formule sont neutralisées.

## Interprétation et historique

- `toxicity_score` est l'indice de problèmes de **communication**, sur 100 ; ce n'est
  ni la toxicité d'un produit ni une probabilité de danger.
- La complétude mesure Q1–Q5 sur 100 %. Q6 et Q7 ont une interprétation différente
  des cinq critères de présence ; la matrice l'indique.
- Seules les analyses `scored`, avec `evidence_verified=true` et un indice non NULL,
  participent aux indicateurs de scores. Les valeurs manquantes restent manquantes.
- La moyenne globale est masquée lorsque plusieurs protocoles (modèle, révision
  déclarée, prompt, règles) sont présents. Un tableau présente alors leurs moyennes séparées.
- Le contrôle des citations réutilise le validateur de l'ingestion, sur les textes
  immuables **liés au résultat sélectionné**. La correspondance lexicale ne remplace
  pas une vérification humaine de la conclusion du LLM.
- Les anciens résultats sans sources liées restent consultables. Aucune archive
  actuelle ne leur est attribuée rétroactivement. Leurs scores enregistrés sont
  visibles au détail et au tableau, mais exclus des indicateurs comparables.
- Pour `needs_review`, les règles appliquées peuvent apparaître `legacy_unknown`
  dans la valeur brute DWH parce que le scoring n'a pas eu lieu. Le détail indique
  « non appliquées ou inconnues » et distingue les règles **prévues par l'exécution**.
- Une nouvelle analyse LLM ne constitue pas une modification de la communication.
  L'historique documentaire affiche les captures et empreintes séparément. Une seule
  version observée ne démontre aucune évolution antérieure. Aucun état passé n'est inventé.
- Les clés entreprises fondées sur le nom, en l'absence d'identifiant vérifié,
  ne garantissent pas une identité juridique stable.

## Accès aux données

`dashboard/data.py` utilise des paramètres SQL liés et des transactions PostgreSQL
`REPEATABLE READ` avec `SET TRANSACTION READ ONLY`, délai de requête de 8 secondes,
et rollback à la fermeture. Aucun `init_db`, `create_all` ou ETL n'est appelé.
PostgreSQL rejette les écritures dans ces transactions, même si le compte de connexion
a des droits supplémentaires. Un compte dédié via `DASHBOARD_DATABASE_URL` permet
en plus de limiter les droits au niveau du rôle.

Droits de lecture requis : schéma `dwh` et ses tables/vues ; tables `recalls`,
`llm_results`, `analysis_runs`, `recall_snapshots`, `documents`, `document_versions`
dans le schéma applicatif `public`.
Les tables `pipeline_jobs` et `pipeline_workers` sont aussi requises pour le dépôt
et le suivi des nouveaux rappels. Le formulaire écrit uniquement les nouveaux fichiers
bruts ; les états SQL et résultats sont écrits par le worker.
Le dashboard n'affiche pas la chaîne de connexion
ou les erreurs brutes de connexion. Les textes des documents sont affichés comme texte,
jamais exécutés ni injectés dans du HTML.

## Fichiers ajoutés ou complétés pour cette étape

- `dashboard/app.py` : interface et graphiques Altair.
- `dashboard/data.py` : accès SQLAlchemy exclusivement en lecture.
- `dashboard/logic.py` : sélection, cohorte de scores, exports et diagnostic lexical.
- `dashboard/__init__.py`, `.streamlit/config.toml`, `Lancer_dashboard.ps1` : lancement et configuration.
- `requirements.txt`, `.env.example`, `README.md`, `DASHBOARD.md` : dépendances et documentation.
- `tests/test_dashboard.py` : logique et interactions Streamlit.
- `tests/test_dwh_postgresql.py` : requêtes réelles et refus d'écriture PostgreSQL.

Toutes les modifications locales précédentes sont conservées. Aucune migration
supplémentaire ni changement de donnée métier n'est nécessaire au dashboard.

## Vérification

Tests du dashboard :

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -q
```

Pour les tests PostgreSQL : utiliser un serveur de test avec droit de créer des bases,
définir `ETICS_TEST_ADMIN_URL` sur sa base d'administration. Les tests DWH créent et
suppriment uniquement des bases `etics_test_<uuid>` ; ne pas utiliser de base métier
comme cible de tests. `TEST_POSTGRES=1` active aussi les variantes PostgreSQL des tests
de scoring ; leur `DATABASE_URL` doit viser la base de test, jamais celle du corpus.

Le test `test_dashboard_readonly_queries_preserve_history` vérifie sur PostgreSQL
le chargement des tables, l'absence d'association fictive des sources anciennes,
`transaction_read_only=on`, le rejet d'un UPDATE et la conservation de l'empreinte
des résultats. Les données synthétiques des tests restent dans leurs fixtures isolées.

Les tests d'interaction utilisent [AppTest, outil officiel Streamlit](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).
Le rapport de livraison accompagne les résultats réellement exécutés, la vérification
des cinq vues sur le corpus réel et les empreintes avant/après des tables PostgreSQL.
