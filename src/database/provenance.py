"""Capture only states observed now. Never reconstruct past document revisions."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import subprocess

from sqlalchemy import select

from src.database.models import Recall, RecallSnapshot, Document, DocumentVersion, AnalysisRun
from src.pipeline_io import PROJECT_ROOT, RAW_DIR, case_folders


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)


def capture_recall(session, recall):
    payload = {c.name: getattr(recall, c.name) for c in Recall.__table__.columns
               if c.name not in {"id", "created_at", "updated_at"}}
    digest = fingerprint(payload)
    last = session.scalar(select(RecallSnapshot).where(RecallSnapshot.recall_id == recall.id)
                          .order_by(RecallSnapshot.id.desc()).limit(1))
    if last and last.content_hash == digest:
        return last
    observed = utcnow()
    if last:
        previous = last.observed_at.replace(tzinfo=timezone.utc) if last.observed_at.tzinfo is None else last.observed_at
        observed = max(observed, previous + timedelta(microseconds=1))
    row = RecallSnapshot(recall_id=recall.id, payload=payload,
                         content_hash=digest, observed_at=observed)
    session.add(row)
    session.flush()
    return row


def source_metadata(case_id, role):
    """Current provenance only; text content remains the exact LLM input."""
    try:
        _, folder = case_folders(RAW_DIR, [case_id])[0]
    except (ValueError, FileNotFoundError, IndexError):
        return {"capture_kind": "text_only"}
    metadata_file = folder / "metadata.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8-sig")) if metadata_file.is_file() else {}
    key = "rappelconso" if role == "official" else "communication"
    entry = metadata.get(key, {})
    stem = "rappelconso" if role == "official" else "communication_entreprise"
    files = [p for p in folder.iterdir() if p.stem.lower() == stem and p.suffix.lower() in {".pdf", ".html", ".htm"}]
    path = files[0] if len(files) == 1 else None
    return {
        "capture_kind": "current_source_and_text" if path else "text_only",
        "source_url": entry.get("url_fiche") if role == "official" else entry.get("url"),
        "source_path": str(path.relative_to(PROJECT_ROOT)) if path else None,
        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path else None,
        "metadata": entry,
        "collected_at_reported": metadata.get("collecte_le"),
        "extraction_link": "not_independently_verified_at_capture",
    }


def capture_document(session, recall_id, role, raw_text, metadata):
    document = session.scalar(select(Document).where(Document.recall_id == recall_id, Document.role == role))
    if document is None:
        document = Document(recall_id=recall_id, role=role)
        session.add(document)
        session.flush()
    text_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    digest = fingerprint({"text": text_hash, "source": metadata})
    last = session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id)
                          .order_by(DocumentVersion.version_number.desc()).limit(1))
    if last and last.fingerprint == digest:
        return last
    version = DocumentVersion(
        document_id=document.id, version_number=last.version_number + 1 if last else 1,
        fingerprint=digest, text_sha256=text_hash, raw_text=raw_text,
        raw_sha256=metadata.get("raw_sha256"), source_url=metadata.get("source_url"),
        source_path=metadata.get("source_path"), provenance=metadata, observed_at=utcnow(),
    )
    session.add(version)
    session.flush()
    return version


def start_run(case_id, texts, model_name, prompt_version, prompt_hash, scoring_version, factory, parameters=None):
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, timeout=3, stderr=subprocess.DEVNULL).decode().strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, timeout=3))
    except (OSError, subprocess.SubprocessError):
        commit, dirty = None, None
    with factory() as session:
        recall = session.scalar(select(Recall).where(Recall.case_id == case_id).with_for_update())
        if recall is None:
            raise LookupError(f"Aucun rappel pour case_id={case_id}")
        snapshot = capture_recall(session, recall)
        versions = [capture_document(session, recall.id, role, content, source_metadata(case_id, role))
                    for role, content in zip(("official", "company"), texts)]
        run = AnalysisRun(recall_snapshot_id=snapshot.id, model_name=model_name,
                          prompt_version=prompt_version, prompt_hash=prompt_hash,
                          scoring_version=scoring_version, parameters=parameters or {},
                          git_commit=commit, git_dirty=dirty, status="running", started_at=utcnow())
        session.add(run)
        session.commit()
        return run.id, versions[0].id, versions[1].id


def finish_run(run_id, status, factory, error_type=None, model_revision=None):
    with factory() as session:
        run = session.get(AnalysisRun, run_id)
        run.status, run.finished_at = status, utcnow()
        run.error_type, run.model_revision = error_type, model_revision
        session.commit()
