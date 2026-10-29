import argparse
import json
import logging

from src.database.db import init_db, SessionLocal
from src.database.repository import save_recall
from src.database.capture import capture_case
from src.extraction.extract import extract_case
from src.llm.analyze import analyze_case
from src.llm.client import APIUnavailableError, APIRequestError, OpenAIQuestionnaireClient
from src.pipeline_io import EXTRACTED_DIR, PROCESSED_DIR, RAW_DIR, case_folders
from src.preprocessing.preprocess import preprocess_case

LOGGER = logging.getLogger(__name__)


def run_base_path(case_id: str, session_factory=SessionLocal):
    path = PROCESSED_DIR / f"{case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Données traitées introuvables : {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("case_id") != case_id:
        raise ValueError("L'identifiant du JSON doit correspondre au dossier demandé.")
    recall = save_recall(data, session_factory)
    if (EXTRACTED_DIR / case_id).is_dir():
        capture_case(case_id, session_factory)
    return recall


def run_llm_path(case_id: str, client=None, session_factory=SessionLocal):
    """Textes -> Q1-Q7/evidence -> PostgreSQL -> score (%) -> mise à jour."""
    return analyze_case(case_id, client=client, session_factory=session_factory)


def run_pipeline(case_id: str, overwrite: bool = False, with_llm: bool = False, client=None,
                 session_factory=SessionLocal) -> None:

    case_id, raw_folder = case_folders(RAW_DIR, [case_id])[0]
    if extract_case(case_id, raw_folder, overwrite):
        raise RuntimeError(f"{case_id} : extraction en erreur, suite du pipeline interrompue.")

    preprocess_case(case_id, EXTRACTED_DIR / case_id, overwrite)
    run_base_path(case_id, session_factory)
    if with_llm:
        run_llm_path(case_id, client, session_factory)


def run_from_processed(case_id: str, with_llm: bool = False, client=None,
                       session_factory=SessionLocal) -> None:
    run_base_path(case_id, session_factory)
    if with_llm:
        run_llm_path(case_id, client, session_factory)


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
        llm_client = OpenAIQuestionnaireClient() if args.with_llm and case_ids else None
    except Exception as error:
        LOGGER.error("Préparation du pipeline impossible : %s", error)
        return 1

    errors = 0
    attempted = 0
    for case_id in case_ids:
        attempted += 1
        try:
            if args.from_processed:
                run_from_processed(case_id, args.with_llm, llm_client)
            else:
                run_pipeline(case_id, args.overwrite, args.with_llm, llm_client)
        except (APIUnavailableError, APIRequestError) as error:
            errors += 1
            LOGGER.error("%s : %s. Lot interrompu ; les autres cas restent en attente.", case_id, error)
            break
        except Exception as error:
            errors += 1
            LOGGER.error("%s : %s", case_id, error)
    LOGGER.info("Pipeline terminé : %s dossier(s) tenté(s) sur %s, %s erreur(s).", attempted, len(case_ids), errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
