# Etics — Rappels toxicité

Projet d'ingestion, d'extraction, d'annotation et d'évaluation des rappels de
produits présentant un risque de toxicité.

## Structure

- `data/` : données brutes, extraites, traitées, annotations et exports
- `docs/` : documentation, guide d'annotation et schémas
- `prompts/` : prompts versionnés
- `src/` : composants Python du pipeline
- `api/` : API FastAPI
- `dashboard/` : tableau de bord Streamlit
- `notebooks/` : explorations et analyses
- `tests/` : tests automatisés

## Démarrage

L'extraction PDF/HTML et le preprocessing sont disponibles. Depuis la racine :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m src.pipeline --case 001
```

`run_pipeline(case_id)` enchaîne `extract_case`, `preprocess_case`, puis `score_case`.
Le scoring est un point d'appel réservé : aucun score n'est calculé pour l'instant.
Une erreur interrompt les étapes suivantes du cas concerné.

Sans `--case`, la commande parcourt tous les dossiers. Les sorties sont dans
`data/extracted/` puis `data/processed/`. Les sources et `metadata.json` restent
intacts. Utiliser `--overwrite` pour régénérer explicitement les résultats existants.

Voir le [guide extraction et preprocessing](docs/extraction-preprocessing.md)
pour la structure, le rôle des fonctions, les limites et les exemples réels.
