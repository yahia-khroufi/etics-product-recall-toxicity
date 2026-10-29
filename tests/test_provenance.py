"""Synthetic fixtures test behavior; they are never loaded into the user's DB."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from src.database.db import Base
from src.database.models import DocumentVersion, AnalysisRun, LLMResult, RecallSnapshot
from src.database.repository import save_recall, score_llm_result
from src.llm.schemas import QuestionnaireAnswers
import src.llm.analyze as analysis
from src.scoring.score import SCORING_VERSION


@pytest.fixture
def factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    save_recall({"case_id":"unit-case", "organization_key":"unit-org",
                 "rappelconso":{"entreprise":"Unit Co", "produit":"Unit product"},
                 "communication_entreprise":{}}, factory)
    yield factory
    engine.dispose()


def answers(quote="company detail"):
    return QuestionnaireAnswers.model_validate({f"q{i}":{"value":i<=5,
              "evidence":[quote] if i<=5 else []} for i in range(1,8)})


@pytest.mark.parametrize("quote", ["", "  \n\t", "invented quotation"])
def test_empty_or_invented_evidence_rejected(quote):
    assert not analysis.verify_evidence(answers(quote), "official danger", "company detail")


def test_q7_requires_evidence_from_both_sources():
    result = answers()
    result.q7.value = True
    result.q7.evidence = ["company detail"]
    assert not analysis.verify_evidence(result, "official danger", "company detail")
    result.q7.evidence.append("official danger")
    assert analysis.verify_evidence(result, "official danger", "company detail")


def setup_input(monkeypatch, company="company detail"):
    monkeypatch.setattr(analysis, "case_folders", lambda *a: [("unit-case",Path("unit-fixture"))])
    monkeypatch.setattr(analysis, "read_text", lambda p: company if p.name.startswith("communication") else "official danger")


class Client:
    model_name = "synthetic-unit-model"

    def analyze(self, *args):
        return answers()


def test_unverified_results_are_pending_review(factory, monkeypatch):
    setup_input(monkeypatch, "different text")
    result = analysis.analyze_case("unit-case", Client(), factory)
    assert result.evidence_verified is False
    assert result.analysis_status == "needs_review"
    assert result.score is None and result.verdict is None
    assert score_llm_result(result.id, factory).toxicity_score is None


def test_analyses_and_document_changes_are_distinct(factory, monkeypatch):
    setup_input(monkeypatch)
    first = analysis.analyze_case("unit-case", Client(), factory)
    second = analysis.analyze_case("unit-case", Client(), factory)
    assert first.id != second.id
    assert first.company_version_id == second.company_version_id
    assert first.scoring_version == SCORING_VERSION
    setup_input(monkeypatch, "company detail changed")
    third = analysis.analyze_case("unit-case", Client(), factory)
    assert third.company_version_id != first.company_version_id
    assert third.official_version_id == first.official_version_id
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(DocumentVersion)) == 3
        assert s.scalar(select(func.count()).select_from(AnalysisRun)) == 3
        assert s.scalar(select(func.count()).select_from(RecallSnapshot)) == 1
        old = s.get(LLMResult,first.id)
        assert old.company_version_id == first.company_version_id
        assert old.score == 1


def test_failed_calls_have_run_without_fabricated_analysis(factory, monkeypatch):
    setup_input(monkeypatch)
    class FailingClient(Client):
        def analyze(self,*args):
            raise RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError):
        analysis.analyze_case("unit-case", FailingClient(), factory)
    with factory() as s:
        run = s.scalar(select(AnalysisRun))
        assert run.status == "failed" and run.error_type == "RuntimeError"
        assert s.scalar(select(func.count()).select_from(LLMResult)) == 0


def test_finalized_analysis_is_not_rescored(factory, monkeypatch):
    setup_input(monkeypatch)
    result = analysis.analyze_case("unit-case", Client(), factory)
    import src.database.repository as repository
    monkeypatch.setattr(repository,"score_case",lambda a: pytest.fail("historical score overwritten"))
    assert score_llm_result(result.id,factory).scoring_version == SCORING_VERSION


def test_successful_backup_model_is_persisted(factory,monkeypatch):
    setup_input(monkeypatch)
    class BackupClient(Client):
        model_name='failed-primary-model'
        def analyze(self,*args):
            self.model_name='actual-backup-model'
            self.model_revision='backup-revision'
            self.parameters={'api_provider':'backup.invalid','api_slot':'api_2',
                'api_attempts':[{'slot':'primary','http_status':402},{'slot':'api_2','status':'responded'}]}
            return answers()
    result=analysis.analyze_case('unit-case',BackupClient(),factory)
    assert result.model_name=='actual-backup-model'
    with factory() as s:
        run=s.get(AnalysisRun,result.run_id)
        assert run.model_name==result.model_name
        assert run.model_revision=='backup-revision'
        assert run.parameters['api_slot']=='api_2'
