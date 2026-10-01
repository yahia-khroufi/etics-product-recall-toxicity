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

## Architecture

```text
raw -> extraction -> preprocessing
                         |-> chemin base : Pydantic -> SQLAlchemy -> PostgreSQL
                         `-> chemin LLM  : Q1-Q7 -> score/verdict -> PostgreSQL
```

Les deux chemins partagent `case_id`. Le client LLM produit seulement une sortie
Pydantic ; les écritures en base passent toujours par `src/database/repository.py`.

## Démarrage

Depuis la racine :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m src.pipeline --help
docker compose up -d db
.\.venv\Scripts\python.exe -m src.pipeline --case 001
```

La dernière commande exécute extraction, preprocessing et chemin base. Elle valide
`data/processed/001.json`, puis insère ou actualise le rappel dans PostgreSQL.
Comme les JSON existent déjà, le lancement direct recommandé est :

```powershell
.\.venv\Scripts\python.exe -m src.pipeline --from-processed --case 001
```

Cette variante ne relance ni extraction ni preprocessing.

Pour ajouter le chemin LLM, renseigner `OPENAI_API_KEY` dans `.env`, puis lancer :

```powershell
.\.venv\Scripts\python.exe -m src.pipeline --from-processed --case 001 --with-llm
```

Cette commande analyse séparément les deux textes, produit Q1-Q7 avec preuves,
calcule le score et le verdict, puis stocke un `llm_result` lié au rappel.

Sans `--case`, la commande parcourt tous les dossiers. `--with-llm` déclenche un
appel API pour chaque cas et n'est donc jamais implicite. Les sorties sont dans
`data/extracted/` puis `data/processed/`. Les sources et `metadata.json` restent
intacts. Utiliser `--overwrite` pour régénérer explicitement les résultats existants.

Pour exécuter les tests locaux, sans PostgreSQL ni appel LLM :

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_data_paths -v
```
