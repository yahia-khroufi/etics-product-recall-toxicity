# ETICS — Rappels toxicité

Projet d’analyse de rappels de produits présentant un risque de toxicité.

Le volet historique PostgreSQL est décrit dans [DWH.md](DWH.md) : migrations SQL,
versions documentaires observées, traçabilité des exécutions et entrepôt `dwh`.
La toxicité évaluée est informationnelle : elle concerne la communication du rappel.

```powershell
python -m src.database.migrate
python -m src.pipeline --from-processed
python -m src.dwh.etl
```

Ces commandes ne font pas d'appel LLM. L'ETL réutilise `llm_results` et ne reconstruit
aucune version ancienne des communications. Voir les limites de provenance dans `DWH.md`.

Pour une base existante, la reprise vérifiée avec sauvegarde et comparaison de
l'historique se lance sans actualiser les rappels :

```powershell
python -m src.database.upgrade --backup-dir backups
```

## Objectif

Le projet permet de :

- extraire les données depuis des PDF/HTML ;
- nettoyer et structurer les données en JSON ;
- stocker les rappels dans PostgreSQL ;
- analyser les communications avec un LLM ;
- répondre aux questions Q1 à Q7 ;
- calculer un score, une classe de transparence et un verdict ;
- stocker les résultats dans PostgreSQL.

## Pipeline

```text
data/raw
   ↓
Extraction
   ↓
data/extracted
   ↓
Preprocessing
   ↓
data/processed
   ↓
PostgreSQL
   +
Analyse LLM
   ↓
Q1-Q7
   ↓
Score / Verdict
   ↓
PostgreSQL
```

## Structure principale

```text
data/
├── raw/
├── extracted/
└── processed/

src/
├── extraction/
├── preprocessing/
├── database/
├── llm/
└── pipeline.py

api/
dashboard/
tests/
```

## Base de données

Deux tables principales :

- `recalls` : informations sur les rappels produits.
- `llm_results` : réponses Q1-Q7, score, verdict et informations du modèle.

Relation :

```text
Recall 1 ─────< plusieurs LLMResult
```

## Technologies

- Python
- Pydantic
- SQLAlchemy
- PostgreSQL
- Docker
- OpenRouter
- GPT-4o
- FastAPI
- Streamlit

## Lancer PostgreSQL

```powershell
docker compose up -d
```

## Consulter le dashboard

Depuis la racine du projet, avec PostgreSQL demarre et les migrations DWH appliquees :

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run dashboard/app.py
```

Ouvrir http://127.0.0.1:8501. Le script `Lancer_dashboard.ps1` permet aussi de le
lancer ; il demarre aussi le traitement automatique en arriere-plan.
L'interface Streamlit consulte PostgreSQL en lecture seule : synthese,
rappels, citations a revoir, historique LLM et tracabilite. Voir [DASHBOARD.md](DASHBOARD.md)
pour les filtres, limites et verifications. La consultation ne declenche pas d'appel LLM.
Le formulaire **Nouveau rappel** accepte les deux documents et confie leur traitement
au worker : extraction, analyse, verification des citations, scoring et ETL.
Voir [AUTOMATISATION.md](AUTOMATISATION.md) pour le suivi, les doublons et les reprises.

Le client LLM utilise une seule API : `OPENAI_API_KEY`, `OPENAI_MODEL` et
`OPENROUTER_BASE_URL`. Les anciennes variables de secours `LLM_API_2_*` et
`LLM_API_3_*` sont ignorées, même si elles sont encore présentes dans `.env`.

## Exécuter un cas

Pipeline complet :

```powershell
python -m src.pipeline --case 001
```

À partir du JSON déjà traité :

```powershell
python -m src.pipeline --case 001 --from-processed
```

Avec analyse LLM :

```powershell
python -m src.pipeline --case 001 --from-processed --with-llm
```

## Architecture base de données

```text
Application Python
      ↓
SQLAlchemy
      ↓
psycopg2
      ↓
PostgreSQL
```

## Architecture LLM

```text
Textes extraits
      ↓
Prompts
      ↓
OpenRouter
      ↓
GPT-4o
      ↓
Q1-Q7
      ↓
Score / Verdict
      ↓
PostgreSQL
```
