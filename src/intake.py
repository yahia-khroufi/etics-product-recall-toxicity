"""Atomic new-case intake for the local dashboard. Existing cases are never replaced."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import text

from src.database.provenance import utcnow
from src.pipeline_io import RAW_DIR, EXTRACTED_DIR, PROCESSED_DIR, case_id_from_folder

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
READY_FILE = ".etics-ready.json"
INTAKE_LOCK = 19740324


class IntakeError(ValueError):
    pass


def validate_document(filename, content):
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".html", ".htm"}:
        raise IntakeError("Utilisez un document PDF ou HTML pour chaque source.")
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise IntakeError("Chaque document doit être non vide et ne pas dépasser 20 Mo.")
    if suffix == ".pdf" and not content.lstrip().startswith(b"%PDF-"):
        raise IntakeError("Le fichier fourni ne possède pas un en-tête PDF valide.")
    if suffix != ".pdf":
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise IntakeError("Les fichiers HTML doivent être encodés en UTF-8.") from None
        if not re.search(r"<(?:!doctype\s+html|html|body|head|article|main|p|div)\b", decoded, re.I):
            raise IntakeError("Le document HTML ne contient pas de balise de contenu reconnue.")
    return suffix


def validate_url(url):
    if url and (urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc
                or urlparse(url).username or urlparse(url).password):
        raise IntakeError("Les liens de provenance doivent être des URL HTTP(S) sans identifiants.")


def existing_case_ids(connection, raw_dir=RAW_DIR, extracted_dir=EXTRACTED_DIR, processed_dir=PROCESSED_DIR):
    result = set(connection.execute(text("SELECT case_id FROM recalls UNION SELECT case_id FROM pipeline_jobs")).scalars())
    for directory in (raw_dir, extracted_dir):
        if directory.exists():
            result.update(case_id_from_folder(p) for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))
    if processed_dir.exists():
        result.update(p.stem for p in processed_dir.glob("*.json"))
    return result


def duplicate_documents(connection, official_digest, company_digest, raw_dir):
    found = connection.execute(text("""
        SELECT r.case_id FROM recalls r JOIN documents d ON d.recall_id=r.id
        JOIN document_versions v ON v.document_id=d.id GROUP BY r.case_id
        HAVING bool_or(d.role='official' AND v.raw_sha256=:official)
           AND bool_or(d.role='company' AND v.raw_sha256=:company) LIMIT 1
    """), {"official": official_digest, "company": company_digest}).scalar()
    if found:
        return found
    if raw_dir.exists():
        for folder in raw_dir.iterdir():
            marker = folder / READY_FILE
            if not folder.is_dir() or folder.name.startswith(".") or not marker.is_file():
                continue
            try:
                manifest, _ = read_manifest(folder)
            except (IntakeError, ValueError, OSError):
                continue
            digests = {Path(name).stem: digest for name, digest in manifest["files"].items()}
            if digests.get("rappelconso") == official_digest and digests.get("communication_entreprise") == company_digest:
                return folder.name
    return None


def submit_case(connection, case_id, official, company, *, official_url="", company_url="",
                raw_dir=RAW_DIR, extracted_dir=EXTRACTED_DIR, processed_dir=PROCESSED_DIR):
    """Caller supplies a read-only PostgreSQL transaction; only new filesystem data is written.

    The transaction-scoped advisory lock serializes dashboard submissions. The hidden
    staging directory is published by an atomic rename after the complete manifest.
    A crash before publication leaves only a hidden directory ignored by the worker.
    """
    case_id = case_id.strip()
    if case_id and not re.fullmatch(r"\d{3,10}", case_id):
        raise IntakeError("L'identifiant doit contenir entre 3 et 10 chiffres, par exemple 066. Laissez vide pour l'attribution automatique.")
    official_url, company_url = official_url.strip(), company_url.strip()
    validate_url(official_url)
    validate_url(company_url)
    extensions = [validate_document(name, content) for name, content in (official, company)]
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INTAKE_LOCK})
    existing = existing_case_ids(connection, raw_dir, extracted_dir, processed_dir)
    duplicate = duplicate_documents(connection, hashlib.sha256(official[1]).hexdigest(),
                                    hashlib.sha256(company[1]).hexdigest(), raw_dir)
    if duplicate:
        raise IntakeError(f"Ces deux documents ont déjà été déposés pour le rappel {duplicate}. Aucun nouveau traitement n'a été lancé.")
    numeric_existing = {int(value) for value in existing if value.isdecimal()}
    if not case_id:
        case_id = str(max(numeric_existing, default=0) + 1).zfill(3)
    if case_id in existing or int(case_id) in numeric_existing:
        raise IntakeError(f"Le rappel {case_id} existe déjà. Aucun fichier ni résultat n'a été remplacé.")
    root = raw_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = root / (".intake-" + uuid4().hex)
    staging.mkdir()
    sources = {}
    for stem, document, suffix in zip(("rappelconso", "communication_entreprise"), (official, company), extensions):
        name = stem + suffix
        (staging / name).write_bytes(document[1])
        sources[name] = hashlib.sha256(document[1]).hexdigest()
    metadata = {"intake": "dashboard-v1", "collecte_le": utcnow().isoformat(),
                "rappelconso": {"url_fiche": official_url or None, "uploaded_name": Path(official[0]).name},
                "communication": {"url": company_url or None, "uploaded_name": Path(company[0]).name}}
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    (staging / "metadata.json").write_bytes(encoded)
    sources["metadata.json"] = hashlib.sha256(encoded).hexdigest()
    manifest = {"case_id": case_id, "files": sources, "submitted_at": utcnow().isoformat()}
    (staging / READY_FILE).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    # os.rename refuses an existing destination on Windows. A non-empty existing
    # case directory cannot be replaced on POSIX either. Never use os.replace here.
    target = root / case_id
    if target.exists():
        raise IntakeError(f"Le dossier {case_id} vient d'être créé par un autre dépôt.")
    os.rename(staging, target)
    return case_id


def read_manifest(folder):
    """Validate all bytes before queueing or running a submission."""
    if folder.is_symlink():
        raise IntakeError("Les dossiers de dépôt ne doivent pas être des liens symboliques.")
    path = folder / READY_FILE
    content = path.read_bytes()
    manifest = json.loads(content)
    if manifest.get("case_id") != folder.name or not re.fullmatch(r"\d{3,10}", folder.name):
        raise IntakeError("Identifiant de dépôt incohérent.")
    files = manifest.get("files", {})
    stems = {Path(name).stem for name in files}
    if len(files) != 3 or stems != {"rappelconso", "communication_entreprise", "metadata"}:
        raise IntakeError("Les deux documents et metadata.json sont requis.")
    for name, digest in files.items():
        source = folder / name
        if Path(name).name != name or source.is_symlink() or not source.resolve().is_relative_to(folder.resolve()):
            raise IntakeError("Chemin de source interdit.")
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise IntakeError("Un document a changé après son dépôt.")
    return manifest, hashlib.sha256(content).hexdigest()
