# ETICS — Rappels toxicité

Projet d’analyse de rappels de produits présentant un risque de toxicité.

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
