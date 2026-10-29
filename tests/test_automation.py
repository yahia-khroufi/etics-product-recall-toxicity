"""Submission and real PostgreSQL worker tests. No external API is called."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import select, text

from tests.test_dwh_postgresql import pg_engine, warehouse  # Disposable PostgreSQL fixtures.
from dashboard.data import read_connection
from src.database.models import PipelineJob, PipelineWorker, LLMResult
from src.database.provenance import utcnow
from src.intake import submit_case, IntakeError, READY_FILE, read_manifest, validate_document
from src.automation import Worker, worker_lease, WorkerAlreadyRunning
from src.llm.client import APIUnavailableError
from src.llm.schemas import QuestionnaireAnswers


OFFICIAL = ("official.html", b"<html><body><p>Rappel officiel du produit test. Danger officiel.</p></body></html>")
COMPANY = ("company.html", b"<html><body><p>Produit test. Danger test. Ne plus consommer. Remboursement.</p></body></html>")


@pytest.fixture
def paths(tmp_path, monkeypatch):
    from src import pipeline_io
    from src import pipeline
    import src.extraction.extract as extraction
    import src.preprocessing.preprocess as preprocessing
    import src.database.capture as capture
    import src.database.provenance as provenance
    import src.llm.analyze as analysis
    raw, extracted, processed = (tmp_path / name for name in ("raw", "extracted", "processed"))
    for directory in (raw, extracted, processed):
        directory.mkdir()
    for module in (pipeline_io, pipeline, provenance):
        monkeypatch.setattr(module, "RAW_DIR", raw)
    for module in (pipeline_io, pipeline, extraction, capture, analysis):
        monkeypatch.setattr(module, "EXTRACTED_DIR", extracted)
    for module in (pipeline_io, pipeline, preprocessing):
        monkeypatch.setattr(module, "PROCESSED_DIR", processed)
    monkeypatch.setattr(provenance, "PROJECT_ROOT", tmp_path)
    return raw, extracted, processed


class FakeClient:
    model_name = "synthetic-test-model"
    model_revision = "synthetic-test-revision"
    calls = 0
    def analyze(self, *args):
        self.calls += 1
        return QuestionnaireAnswers.model_validate({f"q{i}": {"value": i <= 5,
            "evidence": ["Danger test"] if i <= 5 else []} for i in range(1, 8)})


def submit(engine, paths, case_id="066", official=OFFICIAL, company=COMPANY):
    with read_connection(engine) as connection:
        return submit_case(connection, case_id, official, company,
                           raw_dir=paths[0], extracted_dir=paths[1], processed_dir=paths[2])


def get_job(factory):
    with factory() as session:
        return session.scalar(select(PipelineJob))


@pytest.mark.parametrize("name,content", [("test.exe", b"x"), ("test.pdf", b"wrong"),
    ("test.pdf", b""), ("test.html", b"plain text"), ("test.html", b"<html>\xff")])
def test_document_validation_rejects_bad_uploads(name, content):
    with pytest.raises(IntakeError):
        validate_document(name, content)


def test_atomic_intake_duplicate_identity_and_document_pair(warehouse, paths):
    engine, factory = warehouse
    assert submit(engine, paths) == "066"
    manifest, digest = read_manifest(paths[0] / "066")
    assert set(manifest["files"]) == {"rappelconso.html", "communication_entreprise.html", "metadata.json"}
    original = (paths[0] / "066" / "rappelconso.html").read_bytes()
    with pytest.raises(IntakeError, match="déposés"):
        submit(engine, paths, "067")
    changed = ("changed.html", COMPANY[1].replace(b"Danger test", b"Danger different"))
    with pytest.raises(IntakeError, match="existe"):
        submit(engine, paths, "066", company=changed)
    with pytest.raises(IntakeError, match="existe"):
        submit(engine, paths, "00066", company=changed)
    assert submit(engine, paths, "", company=changed) == "067"
    assert (paths[0] / "066" / "rappelconso.html").read_bytes() == original
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM recalls")) == 0
    assert get_job(factory) is None  # The form does no SQL writes.


@pytest.mark.parametrize("bad", ["../066", "66", "066_name", "abc", "00000000000"])
def test_intake_rejects_unsafe_or_noncanonical_identifiers(warehouse, paths, bad):
    with pytest.raises(IntakeError):
        submit(warehouse[0], paths, bad)
    assert list(paths[0].iterdir()) == []


def test_concurrent_submissions_reserve_only_one_case(warehouse, paths):
    def attempt(_):
        try:
            return submit(warehouse[0], paths)
        except IntakeError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [1, 2])) == ["066", "rejected"]
    assert [p.name for p in paths[0].iterdir()] == ["066"]


def test_complete_worker_chain_once_and_restart_no_duplicate(warehouse, paths):
    engine, factory = warehouse
    submit(engine, paths)
    client = FakeClient()
    worker = Worker(engine, raw_dir=paths[0], client_factory=lambda: client)
    with worker_lease(engine):
        assert worker.cycle() == {"discovered": 1, "processed": 1}
        assert worker.cycle() == {"discovered": 0, "processed": 0}
    job = get_job(factory)
    assert job.status == "completed" and job.attempts == 1 and client.calls == 1
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM recalls")) == 1
        assert session.scalar(text("SELECT count(*) FROM document_versions")) == 2
        assert session.scalar(text("SELECT count(*) FROM llm_results")) == 1
        assert session.scalar(text("SELECT count(*) FROM dwh.fact_analysis")) == 1
        assert session.scalar(text("SELECT toxicity_score FROM dwh.fact_analysis")) == 0
    def no_client():
        raise AssertionError("A restart must not initialize the LLM client for completed jobs")
    restarted = Worker(engine, raw_dir=paths[0], client_factory=no_client)
    with worker_lease(engine):
        assert restarted.cycle()["processed"] == 0


def test_single_worker_lock_prevents_parallel_inference(warehouse):
    with worker_lease(warehouse[0]):
        with pytest.raises(WorkerAlreadyRunning):
            with worker_lease(warehouse[0]):
                pytest.fail("second worker entered")


def test_upload_tampering_fails_before_api_call(warehouse, paths):
    engine, factory = warehouse
    submit(engine, paths)
    (paths[0] / "066" / "communication_entreprise.html").write_bytes(b"changed")
    client = FakeClient()
    with worker_lease(engine):
        Worker(engine, raw_dir=paths[0], client_factory=lambda: client).cycle()
    assert get_job(factory).status == "failed" and client.calls == 0
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM llm_results")) == 0


def test_llm_outage_waits_and_caps_retries(warehouse, paths):
    engine, factory = warehouse
    submit(engine, paths)
    class Unavailable(FakeClient):
        def analyze(self, *args):
            self.calls += 1
            raise APIUnavailableError("synthetic outage")
    client = Unavailable()
    worker = Worker(engine, raw_dir=paths[0], client_factory=lambda: client, max_attempts=2)
    with worker_lease(engine):
        worker.cycle()
        assert get_job(factory).status == "retry_wait" and client.calls == 1
        worker.cycle()
        assert client.calls == 1
        with factory() as session:
            session.get(PipelineJob, get_job(factory).id).next_attempt_at = utcnow() - timedelta(seconds=1)
            session.get(PipelineWorker, "main").next_attempt_at = utcnow() - timedelta(seconds=1)
            session.commit()
        worker.cycle()
    assert get_job(factory).status == "failed" and get_job(factory).attempts == 2 and client.calls == 2


def test_etl_retry_reuses_saved_response_without_llm(warehouse, paths, monkeypatch):
    import src.automation as automation
    engine, factory = warehouse
    submit(engine, paths)
    client = FakeClient()
    worker = Worker(engine, raw_dir=paths[0], client_factory=lambda: client)
    real_etl = automation.load_warehouse
    def fail(*args):
        raise RuntimeError("synthetic ETL failure")
    monkeypatch.setattr(automation, "load_warehouse", fail)
    with worker_lease(engine):
        worker.cycle()
        job = get_job(factory)
        assert job.status == "dwh_pending" and job.result_id and client.calls == 1
        with factory() as session:
            original = session.get(LLMResult, job.result_id)
            before = (original.created_at, original.scored_at, original.evidence)
            session.get(PipelineJob, job.id).next_attempt_at = utcnow() - timedelta(seconds=1)
            session.commit()
        monkeypatch.setattr(automation, "load_warehouse", real_etl)
        worker.cycle()
    assert client.calls == 1 and get_job(factory).status == "completed"
    with factory() as session:
        result = session.get(LLMResult, job.result_id)
        assert (result.created_at, result.scored_at, result.evidence) == before
        assert session.scalar(text("SELECT count(*) FROM dwh.fact_analysis")) == 1


def test_interruption_without_response_requires_explicit_retry(warehouse, paths):
    engine, factory = warehouse
    submit(engine, paths)
    client = FakeClient()
    worker = Worker(engine, raw_dir=paths[0], client_factory=lambda: client)
    with worker_lease(engine):
        worker.discover()
        worker.update(get_job(factory).id, status="analyzing", attempts=1)
        worker.cycle()
        assert get_job(factory).status == "interrupted" and client.calls == 0
        worker.retry("066")
        worker.cycle()
    assert client.calls == 1 and get_job(factory).status == "completed"


def test_needs_review_is_terminal_and_retains_null_scores(warehouse, paths):
    engine, factory = warehouse
    submit(engine, paths)
    class BadQuotes(FakeClient):
        def analyze(self, *args):
            self.calls += 1
            return QuestionnaireAnswers.model_validate({f"q{i}": {"value": i <= 5,
                "evidence": ["invented quote only in a test"] if i <= 5 else []} for i in range(1, 8)})
    client = BadQuotes()
    worker = Worker(engine, raw_dir=paths[0], client_factory=lambda: client)
    with worker_lease(engine):
        worker.cycle()
        worker.cycle()
    assert get_job(factory).status == "needs_review" and client.calls == 1
    with factory() as session:
        row = session.execute(text("SELECT toxicity_score,verdict FROM dwh.fact_analysis")).one()
        assert row == (None, None)


def test_worker_respects_long_retry_after_on_the_single_api(warehouse, paths):
    engine, factory = warehouse
    submit(engine, paths)
    class Unavailable(FakeClient):
        def analyze(self, *args):
            self.calls += 1
            raise APIUnavailableError("synthetic throttle", retry_after_seconds=1800)
    client = Unavailable()
    worker = Worker(engine, raw_dir=paths[0], client_factory=lambda: client)
    before = utcnow()
    with worker_lease(engine):
        worker.cycle()
        worker.cycle()
    assert client.calls == 1
    assert get_job(factory).next_attempt_at >= before + timedelta(seconds=1800)
