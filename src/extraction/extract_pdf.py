
import logging
from pathlib import Path

import pymupdf

LOGGER = logging.getLogger(__name__)


def extract_pdf(path: Path) -> str:
    pages = []
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError(f"PDF protégé par mot de passe : {path}")
        for number, page in enumerate(document, start=1):
            text = page.get_text("text", sort=True)
            if not text.strip():
                LOGGER.warning("%s : page %s sans texte extractible (OCR éventuel).", path.name, number)
            pages.append(text)
    if not any(page.strip() for page in pages):
        raise ValueError(f"Aucun texte extractible dans {path.name} ; un OCR est nécessaire.")
    return "\f".join(pages)
