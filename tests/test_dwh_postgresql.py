"""Real PostgreSQL only. Each test creates a disposable database, never business tables."""
import os
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from src.database.db import Base
from src.database.models import LLMResult, RecallSnapshot, DocumentVersion
from src.database.migrate import migrate
from src.database.repository import save_recall, save_llm_result, score_llm_result
from src.database.schemas import LLMResultCreate
from src.dwh.etl import load_warehouse
import src.dwh.etl as etl


@pytest.fixture
def pg_engine():
    url = os.getenv("ETICS_TEST_ADMIN_URL")
    if not url:
        pytest.skip("Set ETICS_TEST_ADMIN_URL to an isolated PostgreSQL test server")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "etics_test_" + uuid4().hex
    from sqlalchemy.engine import make_url
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(make_url(url).set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def warehouse(pg_engine):
    Base.metadata.create_all(pg_engine)
    assert migrate(pg_engine) == ["001_communication_toxicity.sql","002_provenance.sql","003_dwh.sql","004_pipeline_jobs.sql"]
    assert migrate(pg_engine) == []
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    yield pg_engine, factory


def recall(factory, name="Product A", company="Company A"):
    return save_recall({"case_id":"integration-case", "organization_key":"integration-company",
                       "rappelconso":{"produit":name,"marque":"Brand", "entreprise":company,
                                      "date_publication":"01/10/2026", "gtin":["1234567890123"]},
                       "communication_entreprise":{}},factory)


def raw(factory, **extra):
    data = dict(case_id="integration-case",model_name="test-model-alias",prompt_version="test-v1",
                evidence_verified=True,evidence={f"q{i}":["fixture"] if i<=5 else [] for i in range(1,8)},
                **{f"q{i}":i<=5 for i in range(1,8)})
    data.update(extra)
    return save_llm_result(LLMResultCreate(**data),factory)


def scalar(engine, sql):
    with engine.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_dashboard_readonly_queries_preserve_history(warehouse):
    from dashboard.data import read_connection, read_store, read_detail, read_observations, read_dimensions
    engine, factory = warehouse
    recall(factory)
    result = raw(factory, score=.8, severity=0, verdict="non_toxique")
    load_warehouse(engine)
    before = scalar(engine, "SELECT md5(string_agg(row_to_json(r)::text, ',' ORDER BY id)) FROM llm_results r")
    history, counts, etl = read_store(engine)
    assert counts["source_results"] == counts["facts"] == len(history) == 1
    assert not etl.empty
    metadata, documents = read_detail(engine, result.id)
    assert metadata["official_version_id"] is None and documents == {}
    assert read_observations(engine, result.recall_id).empty
    companies, products = read_dimensions(engine)
    assert len(companies) == len(products) == 1
    with read_connection(engine) as connection:
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        with pytest.raises(Exception, match="read-only"):
            connection.execute(text("UPDATE llm_results SET score=0"))
    after = scalar(engine, "SELECT md5(string_agg(row_to_json(r)::text, ',' ORDER BY id)) FROM llm_results r")
    assert before == after


def test_history_idempotence_and_pending_completion(warehouse):
    engine,factory = warehouse
    recall(factory)
    first = raw(factory, score=.8, severity=0, verdict="non_toxique",transparency_class="claire",explanation="original historical explanation")
    second = raw(factory)
    with factory() as s:
        row=s.get(LLMResult,first.id)
        row.created_at=datetime(2025,1,2,tzinfo=timezone.utc)  # Synthetic test fixture only.
        row.analysis_status="legacy_unlinked"
        s.commit()
    assert load_warehouse(engine)["inserted"] == 2
    assert load_warehouse(engine)["inserted"] == 0
    assert scalar(engine,"SELECT count(*) FROM dwh.fact_analysis") == 2
    assert scalar(engine,"SELECT count(*) FROM dwh.fact_analysis WHERE company_version_sk IS NULL") == 2
    assert scalar(engine,"SELECT count(*) FROM dwh.fact_analysis WHERE context_origin='legacy_current_context_unverified'") == 2
    score_llm_result(second.id,factory)
    assert load_warehouse(engine)["updated"] == 1
    assert load_warehouse(engine)["updated"] == 0
    with factory() as s:
        historical=s.get(LLMResult,first.id)
        assert historical.score == .8 and historical.explanation == "original historical explanation"
        assert historical.created_at.year == 2025
        assert historical.run_id is None and historical.scoring_version is None
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_scoring WHERE scoring_version='legacy_unknown'") == 1


def test_scd2_and_facts_keep_original_dimension_keys(warehouse):
    engine,factory=warehouse
    recall(factory)
    first=raw(factory)
    score_llm_result(first.id,factory)
    load_warehouse(engine)
    original=scalar(engine,"SELECT product_sk FROM dwh.fact_analysis")
    recall(factory,name="Product B",company="Company B")
    load_warehouse(engine)
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_product") == 2
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_company") == 2
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_product WHERE is_current") == 1
    assert scalar(engine,"SELECT product_sk FROM dwh.fact_analysis") == original
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_company WHERE NOT is_current AND valid_to>valid_from") == 1
    assert load_warehouse(engine)["inserted"] == 0
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_company") == 2
    with engine.connect() as c:
        previous=c.execute(text("SELECT valid_to FROM dwh.dim_product WHERE NOT is_current")).scalar_one()
        current=c.execute(text("SELECT valid_from FROM dwh.dim_product WHERE is_current")).scalar_one()
        assert previous == current


def test_etl_failure_rolls_back_every_dimension_and_fact(warehouse,monkeypatch):
    engine,factory=warehouse
    recall(factory)
    raw(factory)
    real=etl.insert
    def fail(connection,table,values,returning=None):
        if table=="fact_analysis":
            raise RuntimeError("synthetic ETL failure")
        return real(connection,table,values,returning)
    monkeypatch.setattr(etl,"insert",fail)
    with pytest.raises(RuntimeError): load_warehouse(engine)
    assert scalar(engine,"SELECT count(*) FROM dwh.fact_analysis") == 0
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_company") == 0
    assert scalar(engine,"SELECT status FROM dwh.etl_runs") == "failed"
    monkeypatch.setattr(etl,"insert",real)
    assert load_warehouse(engine)["inserted"] == 1


def test_snapshots_are_immutable_and_migrations_repeatable(warehouse):
    engine,factory=warehouse
    recall(factory)
    with engine.begin() as c:
        with pytest.raises(Exception,match="append-only"):
            c.execute(text("UPDATE recall_snapshots SET content_hash='changed'"))
    assert migrate(engine) == []
    assert scalar(engine,"SELECT count(*) FROM recall_snapshots") == 1


def test_etl_rejects_overwriting_finalized_history(warehouse):
    engine,factory=warehouse
    recall(factory)
    row=raw(factory)
    score_llm_result(row.id,factory)
    load_warehouse(engine)
    with factory() as s:
        s.get(LLMResult,row.id).verdict="toxique"
        s.commit()
    with pytest.raises(ValueError,match="not overwritten"):
        load_warehouse(engine)
    assert scalar(engine,"SELECT verdict FROM dwh.fact_analysis") == "non_toxique"


def test_concurrent_loaders_are_idempotent(warehouse):
    engine,factory=warehouse
    recall(factory)
    raw(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:load_warehouse(engine),range(2)))
    assert sum(r['inserted'] for r in results) == 1
    assert scalar(engine,"SELECT count(*) FROM dwh.fact_analysis") == 1
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_product") == 1


def test_traced_runs_document_versions_and_sql_queries(warehouse,monkeypatch):
    import src.llm.analyze as analysis
    from src.llm.schemas import QuestionnaireAnswers
    engine,factory=warehouse
    recall(factory)
    monkeypatch.setattr(analysis,"case_folders",lambda *a:[("integration-case",Path("synthetic-input"))])
    company=['company evidence']
    monkeypatch.setattr(analysis,"read_text",lambda p:company[0] if p.name.startswith('communication') else 'official risk')
    class Client:
        model_name='initial-primary-alias'
        model_revision='test-revision-A'
        def analyze(self,*a):
            self.model_name='actual-backup-alias'
            self.parameters={'api_provider':'backup.invalid','api_slot':'api_2'}
            return QuestionnaireAnswers.model_validate({f'q{i}':{'value':i<=5,'evidence':['company evidence'] if i<=5 else []} for i in range(1,8)})
    client=Client()
    first=analysis.analyze_case('integration-case',client,factory)
    second=analysis.analyze_case('integration-case',client,factory)
    assert load_warehouse(engine)['inserted'] == 2
    recall(factory,name='Product B')
    company[0]='company evidence revised'
    client.model_revision='test-revision-B'
    third=analysis.analyze_case('integration-case',client,factory)
    assert load_warehouse(engine)['inserted'] == 1
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_document_version WHERE role='company'") == 2
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_model") == 2
    assert scalar(engine,"SELECT count(*) FROM dwh.dim_model WHERE model_name='actual-backup-alias'") == 2
    assert scalar(engine,"SELECT count(*) FROM analysis_runs WHERE model_name='actual-backup-alias'") == 3
    assert scalar(engine,"SELECT count(*) FROM dwh.fact_analysis WHERE context_origin='observed_run_snapshot'") == 3
    assert first.company_version_id == second.company_version_id != third.company_version_id
    with engine.connect() as c:
        rows=c.execute(text("SELECT product_sk,company_version_sk FROM dwh.fact_analysis ORDER BY source_result_id")).all()
        assert rows[0]==rows[1] and rows[0]!=rows[2]
        sql=(Path(__file__).resolve().parents[1]/'src/dwh/queries.sql').read_text(encoding='utf-8')
        c.exec_driver_sql(sql)


def test_upgrade_legacy_columns_without_inventing_document_history(pg_engine):
    Base.metadata.create_all(pg_engine)
    factory=sessionmaker(pg_engine,expire_on_commit=False)
    recall(factory)
    row=raw(factory,score=.8,severity=0,verdict='non_toxique',transparency_class='claire',explanation='legacy value',q2=False)
    with pg_engine.begin() as c:
        # Mimic the original llm_results schema, before the user's local additions.
        for name in ['toxicity_score','toxicity_score_origin','run_id','official_version_id','company_version_id','scoring_version','analysis_status','scored_at']:
            c.exec_driver_sql(f'ALTER TABLE llm_results DROP COLUMN {name}')
        for name in ['score','severity','verdict','transparency_class','explanation']:
            c.exec_driver_sql(f'ALTER TABLE llm_results ALTER COLUMN {name} SET NOT NULL')
        before=c.execute(text('SELECT id,q1,q2,evidence,score,verdict,explanation,created_at FROM llm_results')).one()
    migrate(pg_engine)
    assert migrate(pg_engine)==[]
    with pg_engine.connect() as c:
        after=c.execute(text('SELECT id,q1,q2,evidence,score,verdict,explanation,created_at FROM llm_results')).one()
        assert before==after
        extra=c.execute(text('SELECT toxicity_score,toxicity_score_origin,scoring_version,company_version_id,run_id FROM llm_results')).one()
        assert extra[0]==14.29 and extra[1].startswith('derived_on_migration:')
        assert extra[2:]==(None,None,None)
        assert c.execute(text('SELECT count(*) FROM document_versions')).scalar()==0
    assert load_warehouse(pg_engine)['inserted']==1
    assert scalar(pg_engine,"SELECT completeness_score FROM dwh.fact_analysis")==.8


def test_backup_upgrade_and_history_check(pg_engine,tmp_path):
    from src.database.upgrade import upgrade_database,pg_binary
    try:
        pg_binary('pg_dump')
    except FileNotFoundError:
        pytest.skip('pg_dump is required for the end-to-end backup/upgrade test')
    Base.metadata.create_all(pg_engine)
    factory=sessionmaker(pg_engine,expire_on_commit=False)
    recall(factory)
    raw(factory,score=.8,severity=0,verdict='non_toxique',transparency_class='claire',explanation='original')
    unicode_backup_dir = tmp_path / "sauvegarde\u202fUnicode"
    report=upgrade_database(pg_engine,unicode_backup_dir)
    assert report['status']=='verified'
    assert report['source_before_counts']['llm_results']==1
    assert report['source_after_counts']['llm_results']==1
    assert report['warehouse_fact_count']==1
    assert Path(report['backup']).stat().st_size>0
    repeated=upgrade_database(pg_engine,unicode_backup_dir)
    assert repeated['applied_migrations']==[]
    assert repeated['etl']['inserted']==repeated['etl']['updated']==0
