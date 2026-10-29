"""Transactional, idempotent warehouse loader. No LLM calls and no rescoring."""
import argparse
from datetime import date, datetime, timezone
import json
import unicodedata

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.database.db import engine
from src.database.models import Recall, RecallSnapshot, LLMResult, AnalysisRun, DocumentVersion, Document
from src.database.provenance import capture_recall, fingerprint


def execute(connection, sql, **values):
    return connection.execute(text(sql), values)


def insert(connection, table, values, returning=None):
    # table/column names are internal constants, never user-supplied SQL.
    json_fields = {"gtin", "parameters", "provenance", "evidence"}
    params = {k: json.dumps(v, ensure_ascii=False) if k in json_fields else v for k, v in values.items()}
    placeholders = [f"CAST(:{k} AS jsonb)" if k in json_fields else f":{k}" for k in values]
    sql = f"INSERT INTO dwh.{table} ({','.join(values)}) VALUES ({','.join(placeholders)})"
    if returning:
        sql += f" RETURNING {returning}"
    return execute(connection, sql, **params)


def dimension(connection, table, key_column, key, values, sk):
    found = execute(connection, f"SELECT {sk} FROM dwh.{table} WHERE {key_column}=:key", key=key).scalar()
    if found is not None:
        return found
    return insert(connection, table, {key_column: key, **values}, sk).scalar_one()


def scd2(connection, kind, key, attrs, observed_at):
    sk = kind + "_sk"
    table = "dim_" + kind
    current = execute(connection, f"SELECT * FROM dwh.{table} WHERE {kind}_key=:key AND is_current", key=key).mappings().first()
    digest = fingerprint(attrs)
    if current and current["attributes_hash"] == digest:
        return current[sk]
    if current:
        if observed_at <= current["valid_from"]:
            raise ValueError("Out-of-order SCD observation; source snapshots must remain append-only")
        execute(connection, f"UPDATE dwh.{table} SET valid_to=:at,is_current=false WHERE {sk}=:sk",
                at=observed_at, sk=current[sk])
    return insert(connection, table, {kind + "_key": key, "attributes_hash": digest,
                  **attrs, "valid_from": observed_at, "valid_to": None, "is_current": True}, sk).scalar_one()


def date_key(connection, value):
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).date()
    elif isinstance(value, str):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                value = datetime.strptime(value, fmt).date()
                break
            except ValueError:
                pass
        if not isinstance(value, date):
            return None
    key = value.year * 10000 + value.month * 100 + value.day
    execute(connection, """INSERT INTO dwh.dim_date VALUES(:key,:dt,:yr,:quarter,:month,:day)
                            ON CONFLICT (date_sk) DO NOTHING""", key=key, dt=value, yr=value.year,
            quarter=(value.month-1)//3+1, month=value.month, day=value.day)
    return key


def _load(connection, run_id):
    session = Session(bind=connection, expire_on_commit=False)
    try:
        # Baselines are observed now, never backdated to the old analyses.
        for recall in session.scalars(select(Recall).order_by(Recall.id).with_for_update()):
            capture_recall(session, recall)
        session.flush()
        snapshots = list(session.scalars(select(RecallSnapshot).order_by(RecallSnapshot.observed_at, RecallSnapshot.id)))
        snapshot_map = {}
        first_snapshot = {}
        for snapshot in snapshots:
            first_snapshot.setdefault(snapshot.recall_id, snapshot.id)
            existing = execute(connection, "SELECT company_sk,product_sk FROM dwh.snapshot_dimensions WHERE source_snapshot_id=:id", id=snapshot.id).first()
            if existing:
                snapshot_map[snapshot.id] = tuple(existing)
                continue
            p = snapshot.payload
            normalized = unicodedata.normalize("NFKC", p.get("entreprise") or "").strip().casefold()
            company_key = p.get("organization_key") or ("name:" + fingerprint(normalized) if normalized else "unknown:" + str(snapshot.recall_id))
            company_sk = scd2(connection, "company", company_key,
                              {"company_name": p.get("entreprise"), "organization_key": p.get("organization_key")}, snapshot.observed_at)
            product_sk = scd2(connection, "product", p["case_id"],
                              {"product_name": p.get("produit"), "brand": p.get("marque"), "gtin": p.get("gtin", [])}, snapshot.observed_at)
            insert(connection, "snapshot_dimensions", {"source_snapshot_id": snapshot.id, "company_sk": company_sk, "product_sk": product_sk})
            snapshot_map[snapshot.id] = company_sk, product_sk

        document_map = {}
        for version, role in session.execute(select(DocumentVersion, Document.role).join(Document, Document.id == DocumentVersion.document_id)):
            document_map[version.id] = dimension(connection, "dim_document_version", "source_version_id", version.id,
                {"source_document_id": version.document_id, "role": role, "version_number": version.version_number,
                 "observed_at": version.observed_at, "source_url": version.source_url, "source_path": version.source_path,
                 "raw_sha256": version.raw_sha256, "text_sha256": version.text_sha256, "provenance": version.provenance}, "document_version_sk")
        executions = {}
        runs = {r.id: r for r in session.scalars(select(AnalysisRun))}
        for run in runs.values():
            sk = dimension(connection, "dim_execution", "source_run_id", run.id,
                {"git_commit": run.git_commit, "git_dirty": run.git_dirty, "parameters": run.parameters,
                 "started_at": run.started_at, "finished_at": run.finished_at, "status": run.status}, "execution_sk")
            execute(connection, "UPDATE dwh.dim_execution SET finished_at=:finished,status=:status WHERE execution_sk=:sk",
                    finished=run.finished_at, status=run.status, sk=sk)
            executions[run.id] = sk

        inserted = updated = source_count = 0
        for result, recall in session.execute(select(LLMResult, Recall).join(Recall, Recall.id == LLMResult.recall_id).order_by(LLMResult.id)):
            source_count += 1
            source_values = {c.name: getattr(result, c.name) for c in LLMResult.__table__.columns}
            digest = fingerprint({k: v.isoformat() if isinstance(v, datetime) else v for k,v in source_values.items()})
            old = execute(connection, "SELECT * FROM dwh.fact_analysis WHERE source_result_id=:id", id=result.id).mappings().first()
            if old and old["source_hash"] == digest:
                continue
            if old and old["completeness_score"] is not None:
                raise ValueError(f"Finalized source analysis {result.id} changed; warehouse history not overwritten")
            run = runs.get(result.run_id)
            snapshot_id = run.recall_snapshot_id if run else first_snapshot[recall.id]
            company_sk, product_sk = snapshot_map[snapshot_id]
            snapshot = session.get(RecallSnapshot, snapshot_id)
            model_name = run.model_name if run else result.model_name
            model_revision = run.model_revision if run else None
            provider = run.parameters.get("api_provider") if run else None
            model_identity = [model_name, model_revision, provider] if provider else [model_name, model_revision]
            model_sk = dimension(connection, "dim_model", "model_key", fingerprint(model_identity),
                {"model_name": model_name, "provider_reported_revision": model_revision}, "model_sk")
            prompt_hash = run.prompt_hash if run else None
            prompt_sk = dimension(connection, "dim_prompt", "prompt_key", fingerprint([result.prompt_version, prompt_hash]),
                {"prompt_version": result.prompt_version, "prompt_hash": prompt_hash}, "prompt_sk")
            scoring_sk = dimension(connection, "dim_scoring", "scoring_version", result.scoring_version or "legacy_unknown", {}, "scoring_sk")
            values = {"source_result_id": result.id, "source_recall_id": result.recall_id, "case_id": recall.case_id,
                "company_sk": old["company_sk"] if old else company_sk, "product_sk": old["product_sk"] if old else product_sk,
                "analysis_date_sk": date_key(connection, result.created_at),
                "publication_date_sk": date_key(connection, snapshot.payload.get("date_publication")),
                "model_sk": model_sk, "prompt_sk": prompt_sk, "scoring_sk": scoring_sk,
                "execution_sk": executions.get(result.run_id),
                "official_version_sk": document_map.get(result.official_version_id),
                "company_version_sk": document_map.get(result.company_version_id),
                "analysis_created_at": result.created_at, "scored_at": result.scored_at,
                "context_origin": "observed_run_snapshot" if run else "legacy_current_context_unverified",
                "analysis_status": result.analysis_status, "evidence_verified": result.evidence_verified,
                **{f"q{i}": getattr(result, f"q{i}") for i in range(1,8)}, "evidence": result.evidence,
                "completeness_score": result.score, "toxicity_score": result.toxicity_score,
                "toxicity_score_origin": result.toxicity_score_origin, "severity": result.severity,
                "transparency_class": result.transparency_class, "verdict": result.verdict,
                "explanation": result.explanation, "source_hash": digest, "etl_run_id": run_id}
            if old:
                params = {k: json.dumps(v) if k == "evidence" else v for k,v in values.items()}
                assignments = [f"{k}=" + (f"CAST(:{k} AS jsonb)" if k=="evidence" else f":{k}") for k in values]
                execute(connection, "UPDATE dwh.fact_analysis SET " + ",".join(assignments) + ",loaded_at=clock_timestamp() WHERE source_result_id=:source_result_id", **params)
                updated += 1
            else:
                insert(connection, "fact_analysis", values)
                inserted += 1
        return {"inserted": inserted, "updated": updated, "source_count": source_count}
    finally:
        session.close()


def load_warehouse(bind=engine):
    if bind.dialect.name != "postgresql":
        raise ValueError("The ETICS warehouse requires PostgreSQL")
    with bind.begin() as connection:
        run_id = insert(connection, "etl_runs", {"status": "running"}, "id").scalar_one()
    try:
        with bind.connect() as connection:
            # Acquire the SESSION lock before opening the repeatable-read snapshot.
            # A waiting transaction must not keep a snapshot predating the previous load.
            execute(connection, "SELECT pg_advisory_lock(19740322)")
            connection.commit()
            try:
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
                with connection.begin():
                    stats = _load(connection, run_id)
                    execute(connection, """UPDATE dwh.etl_runs SET status='completed',finished_at=clock_timestamp(),
                          inserted=:inserted,updated=:updated,source_count=:source_count WHERE id=:id""", id=run_id, **stats)
            finally:
                connection.rollback()
                execute(connection, "SELECT pg_advisory_unlock(19740322)")
                connection.commit()
        return {"run_id": run_id, **stats}
    except Exception as error:
        with bind.begin() as connection:
            execute(connection, "UPDATE dwh.etl_runs SET status='failed',finished_at=clock_timestamp(),error_type=:error WHERE id=:id",
                    error=type(error).__name__, id=run_id)
        raise


def main():
    argparse.ArgumentParser(description="Load dwh without LLM calls or rescoring; run migrations first.").parse_args()
    print(json.dumps(load_warehouse(), ensure_ascii=False))


if __name__ == "__main__":
    main()
