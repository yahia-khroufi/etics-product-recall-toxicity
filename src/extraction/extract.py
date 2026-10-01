import argparse
import logging
from pathlib import Path
import re
import unicodedata

from src.extraction.extract_html import extract_html
from src.extraction.extract_pdf import extract_pdf
from src.pipeline_io import RAW_DIR, EXTRACTED_DIR, case_folders, write_output

LOGGER = logging.getLogger(__name__)


def clean_reading_text(text: str) -> str:
    """Corrige encodage Unicode et espaces, sans supprimer le contenu ni les pages."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "").replace("\ufeff", "").replace("\u00ad", "")
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    pages = []
    for page in text.split("\f"):
        lines = [re.sub(r"[ ]+", " ", line).strip() for line in page.split("\n")]
        pages.append(re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip())
    return "\n\f\n".join(pages).strip("\n ") + "\n"


def extract_case(case_id: str, folder: Path, overwrite: bool = False) -> int:
    sources = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".pdf", ".html", ".htm"})
    targets: dict[str, Path] = {}
    for source in sources:
        name = source.stem.lower() + ".txt"
        if name in targets:
            raise ValueError(f"{folder.name} : {targets[name].name} et {source.name} produiraient le même TXT.")
        targets[name] = source
    if not sources:
        LOGGER.warning("%s : aucun PDF/HTML.", case_id)
    errors = 0
    for name, source in targets.items():
        destination = EXTRACTED_DIR / case_id / name
        if destination.exists() and not overwrite:
            LOGGER.info("Déjà présent : %s", destination.relative_to(EXTRACTED_DIR))
            continue
        try:
            reader = extract_pdf if source.suffix.lower() == ".pdf" else extract_html
            text = clean_reading_text(reader(source))
            write_output(destination, text, EXTRACTED_DIR, overwrite)
            LOGGER.info("Créé : %s", destination.relative_to(EXTRACTED_DIR))
        except Exception as error:
            errors += 1
            LOGGER.error("%s / %s : %s", case_id, source.name, error)
    return errors


