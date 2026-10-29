"""Scoring, committed intermediate state, failure recovery and history."""
from itertools import product
from pathlib import Path
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.database.db import Base, DATABASE_URL
from src.database.models import LLMResult
from src.database.repository import save_llm_result, save_recall, score_llm_result
from src.database.schemas import LLMResultCreate
import src.database.repository as repository
import src.llm.analyze as analysis
from src.llm.schemas import QuestionnaireAnswers
from src.scoring.score import score_case


def answers_for(values):
    return QuestionnaireAnswers.model_validate({
        f"q{i}": {"value": value, "evidence": ["citation"] if value else []}
        for i, value in enumerate(values, 1)
    })


@pytest.mark.parametrize("values", list(product((False, True), repeat=7)))
def test_all_answer_combinations(values):
    result = score_case(answers_for(values))
    count = values[:5].count(False) + values[5:].count(True)
    assert result.toxicity_score == round(count * 100 / 7, 2)
    assert 0 <= result.toxicity_score <= 100
    assert result.score == values[:5].count(True) / 5
    assert (result.verdict == "non_toxique") == (count == 0)
    assert (result.transparency_class == "claire") == (count == 0)


BACKENDS = ["sqlite"] + (["postgresql"] if os.getenv("TEST_POSTGRES") == "1" else [])


@pytest.fixture(params=BACKENDS)
def database(request):
    if request.param == "sqlite":
        engine = create_engine("sqlite+pysqlite:///:memory:")
        schema = None
    else:
        # Use a unique schema; never change existing application tables.
        schema = "test_toxicity_" + uuid4().hex
        admin = create_engine(DATABASE_URL)
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(DATABASE_URL)

        @event.listens_for(engine, "connect")
        def set_schema(dbapi_connection, _):
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}"')
            dbapi_connection.commit()

    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        save_recall({
            "case_id": "test-case", "rappelconso": {}, "communication_entreprise": {},
        }, session_factory=factory)
        yield factory, engine
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            admin.dispose()


def raw_result(values=(True, True, True, True, True, False, False)):
    answers = answers_for(values)
    return LLMResultCreate(
        case_id="test-case",
        **{f"q{i}": getattr(answers, f"q{i}").value for i in range(1, 8)},
        evidence={f"q{i}": getattr(answers, f"q{i}").evidence for i in range(1, 8)},
        evidence_verified=True, model_name="fake", prompt_version="test-v1",
    )


def test_storage_then_scoring_and_history(database):
    factory, _ = database
    first = save_llm_result(raw_result(), factory)
    with factory() as session:
        saved = session.get(LLMResult, first.id)
        assert saved.toxicity_score is None
        assert saved.score is None
        assert saved.evidence == raw_result().evidence
    clear = score_llm_result(first.id, factory)
    assert clear.toxicity_score == 0
    assert clear.score == 1

    second = save_llm_result(raw_result((False,) * 5 + (True,) * 2), factory)
    problematic = score_llm_result(second.id, factory)
    assert problematic.toxicity_score == 100
    assert problematic.verdict == "toxique"
    assert second.id != first.id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(LLMResult)) == 2
        old = session.get(LLMResult, first.id)
        assert old.toxicity_score == 0
        assert old.evidence == raw_result().evidence


def test_pipeline_commits_before_calculation_and_recovers(database, monkeypatch):
    factory, _ = database
    monkeypatch.setattr(analysis, "case_folders", lambda *args: [("test-case", Path("unused"))])
    monkeypatch.setattr(analysis, "read_text", lambda path: "citation")

    class Client:
        model_name = "fake"

        def analyze(self, official, company):
            return answers_for((True,) * 5 + (False,) * 2)

    real_score_case = repository.score_case

    def failing_score(answers):
        # A separate session must already see the committed raw analysis.
        with factory() as session:
            row = session.scalar(select(LLMResult))
            assert row is not None
            assert row.toxicity_score is None
            assert row.q1 is True
            assert row.evidence_verified is True
        raise RuntimeError("simulated scoring failure")

    monkeypatch.setattr(repository, "score_case", failing_score)
    with pytest.raises(RuntimeError, match="simulated scoring failure"):
        analysis.analyze_case("test-case", Client(), factory)
    with factory() as session:
        pending_id = session.scalar(select(LLMResult.id))
    monkeypatch.setattr(repository, "score_case", real_score_case)
    assert score_llm_result(pending_id, factory).toxicity_score == 0
    # Re-running the analysis appends a new result instead of replacing history.
    result = analysis.analyze_case("test-case", Client(), factory)
    assert result.toxicity_score == 0
    assert result.id != pending_id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(LLMResult)) == 2


@pytest.mark.parametrize("invalid", [-0.01, 100.01])
def test_database_enforces_percentage_range(database, invalid):
    factory, _ = database
    saved = save_llm_result(raw_result(), factory)
    with factory() as session:
        session.get(LLMResult, saved.id).toxicity_score = invalid
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with factory() as session:
        assert session.get(LLMResult, saved.id).toxicity_score is None


def test_model_input_cannot_supply_toxicity_score():
    with pytest.raises(ValidationError):
        LLMResultCreate.model_validate({**raw_result().model_dump(), "toxicity_score": 99})


def test_unknown_analysis(database):
    factory, _ = database
    with pytest.raises(LookupError, match="Analyse introuvable"):
        score_llm_result(-1, factory)


def test_postgresql_migration_preserves_history_and_is_repeatable(database):
    factory, engine = database
    if engine.dialect.name != "postgresql":
        pytest.skip("Enable TEST_POSTGRES=1 to exercise the SQL migration")
    legacy = raw_result((True, False, True, True, True, False, False))
    # Historical classification is deliberately left unchanged by the migration.
    legacy = legacy.model_copy(update={
        "score": 0.8, "severity": 0, "transparency_class": "claire",
        "verdict": "non_toxique", "explanation": "historical explanation",
    })
    saved = save_llm_result(legacy, factory)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE llm_results DROP COLUMN toxicity_score")
        for column in ("score", "severity", "transparency_class", "verdict", "explanation"):
            connection.exec_driver_sql(f"ALTER TABLE llm_results ALTER COLUMN {column} SET NOT NULL")

    migration = (Path(__file__).resolve().parents[1] / "src/database/migrations/001_communication_toxicity.sql").read_text(encoding="utf-8")
    for _ in range(2):
        connection = engine.raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(migration)
        finally:
            connection.close()

    with factory() as session:
        row = session.get(LLMResult, saved.id)
        assert row.toxicity_score == 14.29
        assert row.score == 0.8
        assert row.verdict == "non_toxique"
        assert row.explanation == "historical explanation"
        assert row.evidence == legacy.evidence
        assert row.created_at == saved.created_at
    pending = save_llm_result(raw_result(), factory)
    assert pending.toxicity_score is None
    assert score_llm_result(pending.id, factory).toxicity_score == 0
