import os
from contextlib import contextmanager

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.pipeline_io import PROJECT_ROOT


def make_engine():
    load_dotenv(PROJECT_ROOT / ".env")
    url = os.getenv("DASHBOARD_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL is required")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("The ETICS warehouse requires PostgreSQL")
    return engine


@contextmanager
def read_connection(engine):
    """Consistent snapshot; PostgreSQL itself rejects writes in this transaction."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        transaction = conn.begin()
        try:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = '8s'")
            yield conn
        finally:
            transaction.rollback()


def read_store(engine):
    with read_connection(engine) as conn:
        history = pd.read_sql_query(text("""
            SELECT h.*, e.source_run_id,
                   e.parameters->>'api_provider' AS api_provider
            FROM dwh.analysis_history h
            LEFT JOIN dwh.dim_execution e USING(execution_sk)
            ORDER BY h.analysis_created_at, h.source_result_id
        """), conn)
        counts = dict(conn.execute(text("""
            SELECT (SELECT count(*) FROM recalls) AS recalls,
                   (SELECT count(*) FROM llm_results) AS source_results,
                   (SELECT count(*) FROM dwh.fact_analysis) AS facts,
                   (SELECT count(*) FROM document_versions) AS document_versions,
                   (SELECT count(*) FROM analysis_runs) AS executions,
                   clock_timestamp() AS read_at
        """)).mappings().one())
        etl = pd.read_sql_query(text("SELECT * FROM dwh.etl_runs ORDER BY id DESC LIMIT 20"), conn)
    history["analysis_created_at"] = pd.to_datetime(history["analysis_created_at"], utc=True)
    return history, counts, etl


def read_detail(engine, result_id):
    """Retrieve only the documents actually linked to this historical result."""
    with read_connection(engine) as conn:
        result = conn.execute(text("""
            SELECT r.id, r.run_id, r.official_version_id, r.company_version_id,
                   a.model_revision, a.git_commit, a.git_dirty, a.prompt_hash,
                   a.scoring_version AS planned_scoring_version,
                   a.started_at, a.finished_at, a.status AS execution_status,
                   s.observed_at AS snapshot_observed_at, s.payload AS snapshot
            FROM llm_results r LEFT JOIN analysis_runs a ON a.id=r.run_id
            LEFT JOIN recall_snapshots s ON s.id=a.recall_snapshot_id
            WHERE r.id=:id
        """), {"id": int(result_id)}).mappings().one()
        docs = conn.execute(text("""
            SELECT v.*, d.role FROM document_versions v
            JOIN documents d ON d.id=v.document_id
            WHERE v.id IN (:official, :company) ORDER BY d.role
        """), {"official": result["official_version_id"],
               "company": result["company_version_id"]}).mappings().all()
    return dict(result), {doc["role"]: dict(doc) for doc in docs}


def read_observations(engine, recall_id):
    with read_connection(engine) as conn:
        return pd.read_sql_query(text("""
            SELECT d.role, v.id AS document_version_id, v.version_number,
                   v.observed_at, v.text_sha256, v.raw_sha256, v.source_url
            FROM document_versions v JOIN documents d ON d.id=v.document_id
            WHERE d.recall_id=:id ORDER BY v.observed_at, v.id
        """), conn, params={"id": int(recall_id)})


def read_dimensions(engine):
    with read_connection(engine) as conn:
        company = pd.read_sql_query(text("""
            SELECT company_key, company_name, organization_key,
                   valid_from, valid_to, is_current
            FROM dwh.dim_company ORDER BY company_key, valid_from
        """), conn)
        product = pd.read_sql_query(text("""
            SELECT product_key, product_name, brand, gtin,
                   valid_from, valid_to, is_current
            FROM dwh.dim_product ORDER BY product_key, valid_from
        """), conn)
    return company, product


def read_jobs(engine):
    with read_connection(engine) as conn:
        jobs = pd.read_sql_query(text("""
            SELECT case_id, status, attempts, result_id, error_type,
                   next_attempt_at, created_at, updated_at, finished_at
            FROM pipeline_jobs ORDER BY id DESC LIMIT 30
        """), conn)
        worker = conn.execute(text("SELECT * FROM pipeline_workers WHERE name='main'")).mappings().first()
    return jobs, dict(worker) if worker else None


def read_live_signature(engine):
    with read_connection(engine) as conn:
        return tuple(conn.execute(text("SELECT count(*),max(source_result_id),max(loaded_at) FROM dwh.fact_analysis")).one())
