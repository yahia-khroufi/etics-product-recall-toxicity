
import re

from src.preprocessing.clean import clean_text, fold, unique

Value = str | list[str] | None

COMMON_LABELS = {
    "produit": ("produit", "nom du produit", "désignation", "désignation du produit", "noms des modèles ou références concernés"),
    "marque": ("marque", "marques", "nom de la marque"),
    "gtin": ("gtin", "ean", "ean13", "ean-13", "code-barres", "code barre", "code ean"),
    "lots": ("lot", "lots", "lot(s)", "numéro de lot", "numéros de lots", "lots concernés"),
}
RAPPELCONSO_LABELS = {
    **COMMON_LABELS,
    "entreprise": ("entreprise", "professionnel", "responsable du rappel"),
    "numero_fiche": ("numéro de fiche", "numéro de la fiche", "fiche de rappel", "n° de fiche"),
    "date_publication": ("date de publication", "publication du", "publié le"),
    "motif_rappel": ("motif du rappel", "motif de rappel"),
    "risques_encourus": ("risques encourus par le consommateur", "risques encourus", "risque"),
    "preconisations_sanitaires": ("préconisations sanitaires",),
    "conduites_a_tenir": ("conduite à tenir par le consommateur", "conduites à tenir", "conduite à tenir"),
    "distributeurs": ("distributeurs", "distributeur"),
    "zone_geographique": ("zone géographique de vente", "zone géographique"),
}
COMMUNICATION_LABELS = {
    **COMMON_LABELS,
    "motif": ("motif", "motif du rappel", "motif de rappel", "raison du rappel"),
    "risque": ("risque", "risques", "risques encourus", "risques encourus par le consommateur", "danger"),
    "recommandations": ("recommandations", "consignes", "conduite à tenir", "conduite à tenir par le consommateur", "que faire ?"),
}
STOP_LABELS = {
    "conditionnements", "date debut/fin de commercialisation", "temperature de conservation",
    "modalites de compensation", "date de fin de la procedure de rappel", "numero de contact",
    "informations complementaires publiques", "description complementaire du risque",
    "categorie de produit", "sous-categorie de produit", "dlc", "dluo", "ddm",
}
SECTION_HEADINGS = {
    "pourquoi ce produit est-il dangereux ?", "que faire ?", "contact",
    "recours dont disposent les consommateurs", "rappel produit", "rappel de produit",
}


def empty_fields(communication: bool = False) -> dict[str, Value]:

    keys = (
        ("produit", "marque", "gtin", "lots", "motif", "risque", "recommandations")
        if communication else
        ("produit", "marque", "entreprise", "numero_fiche", "date_publication", "gtin", "lots",
         "motif_rappel", "risques_encourus", "preconisations_sanitaires", "conduites_a_tenir",
         "distributeurs", "zone_geographique")
    )
    result: dict[str, Value] = dict.fromkeys(keys)
    for key in ("gtin", "lots", "conduites_a_tenir"):
        if key in result:
            result[key] = []
    return result


def single_or_many(values: list[str]) -> Value:
    values = unique(values)
    return None if not values else values[0] if len(values) == 1 else values


def labelled_values(text: str, labels: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:

    aliases = {fold(alias): field for field, names in labels.items() for alias in names}
    result: dict[str, list[str]] = {field: [] for field in labels}
    active = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal active, buffer
        if active and buffer:
            result[active].append(" ".join(buffer))
        active, buffer = None, []

    for raw_line in text.splitlines():
        line = re.sub(r"^[•●▪*\-]\s*", "", raw_line).strip()
        head, colon, tail = line.partition(":")
        field = aliases.get(fold(head.strip())) if colon else aliases.get(fold(line))
        if field:
            flush()
            active = field
            if colon and tail.strip():
                buffer.append(tail.strip())
        elif not line:
            if buffer:
                flush()
        elif (
            raw_line.startswith(("•", "●", "▪"))
            or fold(head.strip()) in STOP_LABELS
            or fold(line) in SECTION_HEADINGS
            or bool(re.match(r"^(?:GTIN|EAN)\b", line, re.I))
        ):
            flush()
        elif active:
            buffer.append(line)
    flush()
    return result


def table_identifiers(text: str) -> tuple[list[str], list[str]]:

    columns: dict[str, int] = {}
    codes, lots = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" not in line:
            columns = {}
            continue
        cells = [cell.strip() for cell in line.split("\t")]
        headers = {fold(cell.rstrip(":")): i for i, cell in enumerate(cells)}
        identified = {
            field: headers[fold(alias)] for field in ("gtin", "lots")
            for alias in COMMON_LABELS[field] if fold(alias) in headers
        }
        if identified:
            columns = identified
            continue
        for field, values in (("gtin", codes), ("lots", lots)):
            if field in columns and columns[field] < len(cells):
                values.append(cells[columns[field]])
    return codes, lots


def extract_gtins(text: str, labelled: list[str]) -> list[str]:
    results = []
    pattern = r"\b(?:GTIN|EAN(?:-?13)?|code[ -]?barres?|code EAN)\s*:?\s*([0-9][0-9 \t]{6,30}[0-9])(?![0-9])"
    candidates = re.findall(pattern, text, re.I) + table_identifiers(text)[0]
    for value in labelled:
        candidates.extend(re.split(r"[,;|]", value))
    for value in candidates:
        digits = re.sub(r"[ \t]", "", value.strip())
        if digits.isascii() and digits.isdigit() and len(digits) in (8, 12, 13, 14):
            results.append(digits)
    return unique(results)


def extract_lots(text: str, labelled: list[str]) -> list[str]:

    candidates = list(labelled) + table_identifiers(text)[1]
    for line in text.splitlines():
        line = re.sub(r"^[•●▪*\-]\s*", "", line).strip()
        match = re.match(
            r"^(?:(?:GTIN|EAN)\s*:?\s*\d{8,14}\s+)?Lot(?:s|\(s\))?\s+(?:concernés\s*)?:?\s*(.+)",
            line, re.I,
        )
        if match:
            candidates.append(match.group(1))
    results = []
    for value in candidates:
        value = re.split(r"\b(?:Date de|DLC|DLUO|DDM|GTIN|EAN)\b", value, maxsplit=1, flags=re.I)[0].strip()
        if fold(value).startswith(("voir ", "non concerne", "non precise", "non renseigne")):
            continue
        # Les numéros de lot peuvent contenir des lettres, tirets et barres obliques.
        results.extend(part.strip(" :") for part in re.split(r"[;|,]", value) if part.strip(" :"))
    return unique(results)


def matching_paragraphs(text: str, pattern: str) -> list[str]:
    found = []
    for block in re.split(r"\n\s*\n", text):
        paragraph = " ".join(block.splitlines()).strip()
        if paragraph and len(paragraph) <= 1500 and re.search(pattern, paragraph, re.I):
            found.append(paragraph)
    return unique(found)


def extract_rappelconso_fields(text: str) -> dict[str, Value]:
    text = clean_text(text)
    fields = empty_fields()
    labelled = labelled_values(text, RAPPELCONSO_LABELS)
    for key, values in labelled.items():
        if key not in {"gtin", "lots", "conduites_a_tenir"}:
            fields[key] = single_or_many(values)
    fields["gtin"] = extract_gtins(text, labelled["gtin"])
    fields["lots"] = extract_lots(text, labelled["lots"])
    fields["conduites_a_tenir"] = unique([
        part.strip().rstrip(".") for value in labelled["conduites_a_tenir"]
        for part in re.split(r"[,;|]", value)
    ])
    # Forme explicite de l'en-tête des affichettes RappelConso.
    title = re.search(r"^(.{2,180}?)\s+rappelle\s+(.+)$", text, re.M)
    if title:
        if fields["entreprise"] is None:
            fields["entreprise"] = title.group(1).strip()
        if fields["produit"] is None:
            fields["produit"] = title.group(2).strip()
    if fields["date_publication"] is None:
        publication = re.search(r"Publication du[^\d]{0,100}(\d{2}/\d{2}/\d{4})", text, re.I)
        if publication:
            fields["date_publication"] = publication.group(1)
    return fields


def extract_communication_fields(text: str) -> dict[str, Value]:

    text = clean_text(text)
    fields = empty_fields(communication=True)
    labelled = labelled_values(text, COMMUNICATION_LABELS)
    for key, values in labelled.items():
        if key not in {"gtin", "lots"}:
            fields[key] = single_or_many(values)
    fields["gtin"] = extract_gtins(text, labelled["gtin"])
    fields["lots"] = extract_lots(text, labelled["lots"])
    fallback_patterns = {
        "motif": r"\b(?:suite à|en raison de|présence d['’e]|détection d['’e]|défaut de|non.conformité|nous avons constaté que)",
        "risque": r"\b(?:risque(?:s)?|danger(?:s)?)\b",
        "recommandations": r"\b(?:ne\s+(?:plus|pas)\s+(?:le\s+|les\s+)?(?:consommer|utiliser)|n['’]utilise[rz]\s+pas|cessez\s+d['’]utiliser|il vous est demandé de|veuillez\s+(?:rapporter|cesser))\b",
    }
    for key, pattern in fallback_patterns.items():
        if fields[key] is None:
            fields[key] = single_or_many(matching_paragraphs(text, pattern))
    return fields
