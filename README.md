# Etics — Rappels toxicité

Projet d'ingestion, d'extraction, d'analyse et d'évaluation des rappels de
produits présentant un risque de toxicité.

## Structure

- `data/` : données brutes, extraites et traitées
- `docs/` : documentation
- `prompts/` : prompts versionnés
- `src/` : composants Python du pipeline
- `api/` : API FastAPI
- `dashboard/` : tableau de bord Streamlit
- `notebooks/` : explorations et analyses
- `tests/` : tests automatisés

## Architecture

```text
raw
 ↓
extraction
 ↓
preprocessing
 ↓
processed JSON
 ├── chemin base
 │     ↓
 │   Pydantic
 │     ↓
 │   SQLAlchemy
 │     ↓
 │   PostgreSQL
 │
 └── chemin LLM
       ↓
     Q1-Q7
       ↓
     score / verdict
       ↓
     PostgreSQL