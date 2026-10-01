
from pathlib import Path
import os
import re
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
EXTRACTED_DIR = PROJECT_ROOT / "data" / "extracted"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def case_id_from_folder(folder: Path) -> str:
    match = re.match(r"^(\d+)(?:[_-]|$)", folder.name)
    return match.group(1) if match else folder.name


def case_folders(root: Path, selected: list[str] | None = None) -> list[tuple[str, Path]]:
    if not root.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {root}")
    found: dict[str, Path] = {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        case_id = case_id_from_folder(folder)
        if selected and case_id not in selected and folder.name not in selected:
            continue
        if case_id in found:
            raise ValueError(f"Identifiant {case_id} partagé par {found[case_id]} et {folder}")
        found[case_id] = folder
    if selected:
        missing = set(selected) - set(found) - {p.name for p in found.values()}
        if missing:
            raise ValueError(f"Dossiers demandés introuvables : {', '.join(sorted(missing))}")
    return list(found.items())


def write_output(path: Path, content: str, root: Path, overwrite: bool = False) -> bool:
    if root not in (EXTRACTED_DIR, PROCESSED_DIR):
        raise ValueError("Les sorties doivent rester dans data/extracted ou data/processed.")
    expected_suffix = ".txt" if root == EXTRACTED_DIR else ".json"
    if path.suffix != expected_suffix or not path.resolve().is_relative_to(root):
        raise ValueError(f"Destination interdite : {path}")
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return True

