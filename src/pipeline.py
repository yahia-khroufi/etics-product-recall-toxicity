"""Extraction, preprocessing et point d'appel du scoring pour chaque cas."""

import argparse
import logging

from src.extraction.extract import extract_case
from src.pipeline_io import RAW_DIR, EXTRACTED_DIR, case_folders
from src.preprocessing.preprocess import preprocess_case
from src.scoring.score import score_case

LOGGER = logging.getLogger(__name__)


def run_pipeline(case_id: str, overwrite: bool = False) -> None:
    """Enchaîne les étapes d'un cas ; une erreur interrompt les étapes suivantes."""
    case_id, raw_folder = case_folders(RAW_DIR, [case_id])[0]

    if extract_case(case_id, raw_folder, overwrite):
        raise RuntimeError(f"{case_id} : extraction en erreur, suite du pipeline interrompue.")

    preprocess_case(case_id, EXTRACTED_DIR / case_id, overwrite)
    score_case(case_id)


def main() -> int:
    """Lit les options et lance le pipeline pour tous les cas ou une sélection."""
    parser = argparse.ArgumentParser(description="Pipeline ETICS : extraction, preprocessing, scoring à venir.")
    parser.add_argument("--case", action="append", help="Identifiant ou nom de dossier ; option répétable.")
    parser.add_argument("--overwrite", action="store_true", help="Régénérer les TXT et JSON existants.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s : %(message)s")
    try:
        cases = case_folders(RAW_DIR, args.case)
    except (OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1
    errors = 0
    for case_id, _ in cases:
        try:
            run_pipeline(case_id, args.overwrite)
        except (OSError, ValueError, RuntimeError) as error:
            errors += 1
            LOGGER.error("%s : %s", case_id, error)
    LOGGER.info("Pipeline terminé : %s dossier(s), %s erreur(s). Scoring non implémenté.", len(cases), errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
