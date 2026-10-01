import argparse
import json
import logging

from src.database.db import init_db
from src.database.repository import save_recall
from src.extraction.extract import extract_case
from src.llm.analyze import analyze_case
from src.pipeline_io import EXTRACTED_DIR, PROCESSED_DIR, RAW_DIR, case_folders
from src.preprocessing.preprocess import preprocess_case

LOGGER = logging.getLogger(__name__)


def run_base_path(case_id: str):
    path = PROCESSED_DIR / f"{case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Données traitées introuvables : {path}")
    return save_recall(json.loads(path.read_text(encoding="utf-8")))


def run_llm_path(case_id: str):
    """Chemin LLM : textes -> Q1-Q7 -> score/verdict -> PostgreSQL."""
    return analyze_case(case_id)


def run_pipeline(case_id: str, overwrite: bool = False, with_llm: bool = False) -> None:

    case_id, raw_folder = case_folders(RAW_DIR, [case_id])[0]
    if extract_case(case_id, raw_folder, overwrite):
        raise RuntimeError(f"{case_id} : extraction en erreur, suite du pipeline interrompue.")

    preprocess_case(case_id, EXTRACTED_DIR / case_id, overwrite)
    run_base_path(case_id)
    if with_llm:
        run_llm_path(case_id)


def run_from_processed(case_id: str, with_llm: bool = False) -> None:
    run_base_path(case_id)
    if with_llm:
        run_llm_path(case_id)


def processed_case_ids(selected: list[str] | None = None) -> list[str]:
    """Liste les case_id disponibles sous forme de JSON dans data/processed."""
    available = sorted(path.stem for path in PROCESSED_DIR.glob("*.json"))
    if not selected:
        return available
    missing = sorted(set(selected) - set(available))
    if missing:
        raise ValueError(f"JSON traités introuvables : {', '.join(missing)}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline ETICS : préparation, chemin base et chemin LLM."
    )
    parser.add_argument(
        "--case",
        action="append",
        help="Identifiant ou nom de dossier ; option répétable.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Régénérer les TXT et JSON existants.",
    )
    parser.add_argument(
        "--with-llm", action="store_true",
        help="Exécuter Q1-Q7 et stocker le score (appel API payant).",
    )
    parser.add_argument(
        "--from-processed", action="store_true",
        help="Partir des JSON existants sans relancer extraction/preprocessing.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s : %(message)s")

    try:
        case_ids = (
            processed_case_ids(args.case)
            if args.from_processed
            else [case_id for case_id, _ in case_folders(RAW_DIR, args.case)]
        )
        init_db()
    except Exception as error:
        LOGGER.error("Préparation du pipeline impossible : %s", error)
        return 1

    errors = 0
    for case_id in case_ids:
        try:
            if args.from_processed:
                run_from_processed(case_id, args.with_llm)
            else:
                run_pipeline(case_id, args.overwrite, args.with_llm)
        except Exception as error:
            errors += 1
            LOGGER.error("%s : %s", case_id, error)
    LOGGER.info("Pipeline terminé : %s dossier(s), %s erreur(s).", len(case_ids), errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
