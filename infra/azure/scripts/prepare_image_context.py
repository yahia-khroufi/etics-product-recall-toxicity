"""Prepare committed application sources only. Does not build or push an image."""
import argparse
from pathlib import Path, PurePosixPath
import subprocess


def prepare(repository, output):
    repository, output = Path(repository).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choisir un nouveau dossier de préparation, sans données existantes.")
    if output.is_relative_to(repository):
        raise ValueError("Placer le contexte de construction hors du dépôt.")
    files = subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", "HEAD"], cwd=repository).decode("utf-8").split("\0")
    output.mkdir(parents=True)
    count = 0
    for name in filter(None, files):
        parts = PurePosixPath(name).parts
        base = parts[-1]
        if parts[0] in {"infra", ".github", "data", "runtime", "backups", "work", "outputs", ".git", ".venv"}:
            continue
        if base.startswith(".env") and base != ".env.example":
            continue
        if any(suffix in base for suffix in (".tfstate", ".tfvars", ".tfplan")) or base in {"backend.hcl", "tfplan"}:
            continue
        if base.lower().endswith((".pem", ".pfx", ".key", ".dump", ".zip")):
            continue
        target = (output/name).resolve()
        if not target.is_relative_to(output):
            raise ValueError("Chemin source non autorisé.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output(["git", "show", "HEAD:"+name], cwd=repository))
        count += 1
    if not (output/"Dockerfile").is_file():
        raise ValueError("Dockerfile absent du commit sélectionné.")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    copied = prepare(root, args.output)
    print(f"Contexte préparé : {copied} fichiers du commit HEAD. Aucun envoi effectué.")
