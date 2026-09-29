from pathlib import Path
import json
import requests


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SOURCES_FILE = Path("sources.json")
RAW_DIR = Path("data/raw")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


# --------------------------------------------------
# FONCTION DE TELECHARGEMENT
# --------------------------------------------------

def download_source(url: str, output_dir: Path, base_name: str):
    """
    Télécharge une URL et sauvegarde la réponse en PDF ou HTML.
    """

    print(f"  Téléchargement : {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    # Exemple :
    # application/pdf
    # text/html; charset=UTF-8
    content_type = response.headers.get("Content-Type", "").lower()

    if "pdf" in content_type:
        extension = ".pdf"

    elif "html" in content_type:
        extension = ".html"

    else:
        # petit fallback si le serveur ne donne pas un bon Content-Type
        if url.lower().endswith(".pdf"):
            extension = ".pdf"
        else:
            extension = ".html"

    output_path = output_dir / f"{base_name}{extension}"

    output_path.write_bytes(response.content)

    print(f"  -> sauvegardé : {output_path}")

    return output_path


# --------------------------------------------------
# COLLECTE D'UN PRODUIT
# --------------------------------------------------

def collect_product(source: dict):
    product_id = source["id"]

    product_dir = RAW_DIR / product_id
    product_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== {product_id} ===")

    # RappelConso
    rappelconso_url = source.get("rappelconso_url")

    if rappelconso_url:
        try:
            download_source(
                url=rappelconso_url,
                output_dir=product_dir,
                base_name="rappelconso"
            )

        except requests.RequestException as e:
            print(f"  Erreur RappelConso : {e}")

    # Communication entreprise
    entreprise_url = source.get("entreprise_url")

    if entreprise_url:
        try:
            download_source(
                url=entreprise_url,
                output_dir=product_dir,
                base_name="entreprise"
            )

        except requests.RequestException as e:
            print(f"  Erreur entreprise : {e}")


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        sources = json.load(f)

    print(f"{len(sources)} produits à collecter")

    for source in sources:
        collect_product(source)

    print("\nCollecte terminée.")


if __name__ == "__main__":
    main()
