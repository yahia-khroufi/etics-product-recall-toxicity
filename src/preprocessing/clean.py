
import re
import unicodedata


def clean_text(text: str) -> str:
    """Normalise les espaces en conservant accents, chiffres et limites des paragraphes."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    text = text.replace("\ufeff", "").replace("\x00", "").replace("\u00ad", "")
    # Les tabulations séparent les colonnes des tableaux HTML extraits.
    lines = [re.sub(r"[^\S\n\t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fold(text: str) -> str:
    """Crée une version sans accents et en minuscules pour reconnaître les libellés."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def unique(values: list[str]) -> list[str]:
    """Déduplique les valeurs sans changer leur ordre ni leur contenu."""
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
