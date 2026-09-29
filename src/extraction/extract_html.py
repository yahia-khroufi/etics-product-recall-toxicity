
from pathlib import Path

from bs4 import BeautifulSoup


def extract_html(path: Path) -> str:
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    for tag in soup.select('[hidden], [aria-hidden="true"]'):
        tag.decompose()
    content = soup.find("main") or soup.body or soup
    for tag in content.find_all("nav"):
        tag.decompose()
    for tag in content.find_all("br"):
        tag.replace_with("\n")
    for row in content.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if cells:
            row.replace_with("\n" + "\t".join(cell.get_text(" ", strip=True) for cell in cells) + "\n")
    for tag in content.find_all(["p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "dl", "dt", "dd"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = content.get_text(separator=" ", strip=False)
    if not text.strip():
        raise ValueError(f"Aucun texte dans {path.name} (page vide ou contenu chargé par JavaScript).")
    return text
