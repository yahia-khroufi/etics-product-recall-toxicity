
import json
import logging
from pathlib import Path

from src.pipeline_io import PROCESSED_DIR, write_output
from src.preprocessing.extract_fields import extract_communication_fields, extract_rappelconso_fields

LOGGER = logging.getLogger(__name__)


def read_text(path: Path) -> str:
    """Lit un TXT UTF-8 ; une source absente est signalée et donne un texte vide."""
    if not path.is_file():
        LOGGER.warning("Source absente : %s ; ses champs resteront vides.", path)
        return ""
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        LOGGER.warning("Source vide : %s", path)
    return text


def preprocess_case(case_id: str, folder: Path, overwrite: bool = False) -> bool:
    """Réunit deux extractions indépendantes dans data/processed/<case_id>.json."""
    destination = PROCESSED_DIR / f"{case_id}.json"
    if destination.exists() and not overwrite:
        LOGGER.info("Déjà présent : %s", destination.name)
        return False
    official = folder / "rappelconso.txt"
    company = folder / "communication_entreprise.txt"
    if not official.is_file() and not company.is_file():
        raise ValueError(f"{folder.name} : aucun des deux TXT attendus n'est présent.")
    payload = {
        "case_id": case_id,
        "rappelconso": extract_rappelconso_fields(read_text(official)),
        "communication_entreprise": extract_communication_fields(read_text(company)),
    }
    created = write_output(
        destination, json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        PROCESSED_DIR, overwrite,
    )
    LOGGER.info("Créé : %s", destination.name)
    return created


