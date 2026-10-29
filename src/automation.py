"""Durable single-worker queue for complete dashboard submissions; no historical replay."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
import logging
import os
import hashlib
from pathlib import Path
import re
import time

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from src.database.db import engine
from src.database.models import PipelineJob, PipelineWorker, Recall, LLMResult, AnalysisRun
from src.database.provenance import utcnow, finish_run
from src.database.repository import score_llm_result
from src.dwh.etl import load_warehouse
from src.intake import READY_FILE, read_manifest
from src.llm.client import APIUnavailableError, OpenAIQuestionnaireClient
from src.pipeline import run_pipeline, run_llm_path
from src.pipeline_io import RAW_DIR

LOGGER = logging.getLogger(__name__)
WORKER_LOCK = 19740323
TERMINAL = {"completed", "needs_review", "skipped_existing", "failed", "interrupted"}


class WorkerAlreadyRunning(RuntimeError):
    pass


class SubmissionChanged(RuntimeError):
    pass


@contextmanager
def worker_lease(bind):
    """A session-scoped PostgreSQL advisory lock survives individual job commits."""
    with bind.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}).scalar_one()
        if not acquired:
            raise WorkerAlreadyRunning("Un processus ETICS surveille déjà la file.")
        try:
            yield connection
        finally:
            if not connection.invalidated:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})


class Worker:
    def __init__(self, bind=engine, *, raw_dir=RAW_DIR, client_factory=OpenAIQuestionnaireClient,
                 retry_seconds=900, max_attempts=3):
        self.engine = bind
        self.factory = sessionmaker(bind, expire_on_commit=False)
        self.raw_dir = Path(raw_dir).resolve()
        self.client_factory = client_factory
        self.client = None  # Do not instantiate an API client when the queue is empty.
        self.retry_seconds = retry_seconds
        self.max_attempts = max_attempts
        self.started_at = utcnow()
        self.pause_until = None

    def heartbeat(self, status, case_id=None):
        with self.factory() as session:
            row = session.get(PipelineWorker, "main")
            if row is None:
                row = PipelineWorker(name="main", pid=os.getpid(), status=status,
                                     started_at=self.started_at, heartbeat_at=utcnow())
                session.add(row)
            row.pid, row.status, row.current_case_id = os.getpid(), status, case_id
            row.started_at, row.heartbeat_at = self.started_at, utcnow()
            row.next_attempt_at = self.pause_until
            session.commit()

    def update(self, job_id, **values):
        with self.factory() as session:
            job = session.get(PipelineJob, job_id)
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = utcnow()
            session.commit()

    def existing_result(self, case_id):
        with self.factory() as session:
            return session.scalar(select(LLMResult).join(Recall).where(Recall.case_id == case_id)
                                  .order_by(LLMResult.id.desc()).limit(1))

    def discover(self):
        discovered = 0
        if not self.raw_dir.is_dir():
            return discovered
        for folder in sorted(self.raw_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith(".") or not (folder / READY_FILE).is_file():
                continue
            if not re.fullmatch(r"\d{3,10}", folder.name):
                continue
            with self.factory() as session:
                if session.scalar(select(PipelineJob.id).where(PipelineJob.case_id == folder.name)):
                    continue
            try:
                manifest, digest = read_manifest(folder)
            except Exception as error:
                LOGGER.warning("Dépôt %s non pris en charge (%s).", folder.name, type(error).__name__)
                with self.factory() as session:
                    session.add(PipelineJob(case_id=folder.name, source_path=folder.name,
                        input_hash=hashlib.sha256((folder / READY_FILE).read_bytes()).hexdigest(),
                        status="failed", attempts=0, error_type=type(error).__name__, finished_at=utcnow()))
                    session.commit()
                continue
            # This marker exists only on explicit complete form submissions. Never
            # queue the pre-existing corpus or fabricate jobs for historical cases.
            with self.factory() as session:
                session.add(PipelineJob(case_id=manifest["case_id"], source_path=folder.name,
                                        input_hash=digest, status="queued", attempts=0))
                session.commit()
                discovered += 1
        return discovered

    def finish_saved_result(self, job, result):
        """Resume committed answers/scoring/ETL without another inference request."""
        self.update(job.id, status="dwh_pending", result_id=result.id, error_type=None)
        if result.analysis_status == "pending":
            result = score_llm_result(result.id, self.factory)
        if result.run_id:
            with self.factory() as session:
                run = session.get(AnalysisRun, result.run_id)
                unfinished = run.status not in {"scored", "needs_review"}
                revision = run.model_revision
            if unfinished:
                finish_run(result.run_id, result.analysis_status, self.factory, model_revision=revision)
        load_warehouse(self.engine)
        final_status = "needs_review" if result.analysis_status == "needs_review" else "completed"
        self.update(job.id, status=final_status, next_attempt_at=None, finished_at=utcnow())
        LOGGER.info("Rappel %s : %s, analyse %s, DWH alimenté.", job.case_id, final_status, result.id)

    def process(self, job):
        self.heartbeat("processing", job.case_id)
        saved = self.existing_result(job.case_id)
        if saved is not None:
            # This also recovers a crash after response persistence or after ETL.
            try:
                self.finish_saved_result(job, saved)
            except Exception as error:
                self.update(job.id, status="dwh_pending", result_id=saved.id,
                            error_type=type(error).__name__, next_attempt_at=utcnow() + timedelta(seconds=30))
                LOGGER.warning("Rappel %s : reprise ETL en attente (%s).", job.case_id, type(error).__name__)
            return
        if job.status == "analyzing":
            # The provider might have answered before the process died. Without
            # a persisted response we cannot promise exactly-once billing.
            self.update(job.id, status="interrupted", error_type="AmbiguousLLMCompletion")
            return
        try:
            folder = self.raw_dir / job.source_path
            if folder.resolve().parent != self.raw_dir or folder.name != job.case_id:
                raise SubmissionChanged("Invalid submission path")
            _, digest = read_manifest(folder)
            if digest != job.input_hash:
                raise SubmissionChanged("Submission manifest changed")
            self.update(job.id, status="preparing", error_type=None, next_attempt_at=None)
            # Reuse existing extraction/preprocessing/persistence without overwrite.
            run_pipeline(job.case_id, session_factory=self.factory)
            if read_manifest(folder)[1] != job.input_hash:
                raise SubmissionChanged("Input changed during preparation")
            if self.client is None:
                self.client = self.client_factory()
            self.update(job.id, status="analyzing", attempts=job.attempts + 1)
            run_llm_path(job.case_id, self.client, self.factory)
            result = self.existing_result(job.case_id)
            if result is None:
                raise RuntimeError("Inference did not persist a result")
            self.finish_saved_result(job, result)
        except Exception as error:
            saved = self.existing_result(job.case_id)
            if saved is not None:
                self.update(job.id, status="dwh_pending", result_id=saved.id,
                            error_type=type(error).__name__, next_attempt_at=utcnow() + timedelta(seconds=30))
            elif isinstance(error, APIUnavailableError):
                attempts = job.attempts + 1
                retry_at = utcnow() + timedelta(seconds=max(self.retry_seconds, error.retry_after_seconds))
                self.pause_until = retry_at
                self.update(job.id, status="retry_wait" if attempts < self.max_attempts else "failed",
                            error_type=type(error).__name__, next_attempt_at=retry_at if attempts < self.max_attempts else None)
                self.client = None  # Reload the same single API configuration on a bounded retry.
            else:
                self.update(job.id, status="failed", error_type=type(error).__name__, next_attempt_at=None)
            # Raw provider errors may contain credentials; retain only their type.
            LOGGER.warning("Rappel %s en attente/erreur (%s).", job.case_id, type(error).__name__)

    def cycle(self):
        added = self.discover()
        with self.factory() as session:
            previous_worker = session.get(PipelineWorker, "main")
            self.pause_until = previous_worker.next_attempt_at if previous_worker else None
        if self.pause_until and self.pause_until > utcnow():
            self.heartbeat("waiting_api")
            return {"discovered": added, "processed": 0}
        self.pause_until = None
        with self.factory() as session:
            jobs = session.scalars(select(PipelineJob).where(PipelineJob.status.notin_(TERMINAL))
                                   .order_by(PipelineJob.id)).all()
        processed = 0
        for job in jobs:
            if job.next_attempt_at and job.next_attempt_at > utcnow():
                continue
            self.process(job)
            processed += 1
            if self.pause_until:
                break
        self.heartbeat("waiting_api" if self.pause_until else "idle")
        return {"discovered": added, "processed": processed}

    def retry(self, case_id):
        """Explicit operator retry; never removes or replaces an existing result."""
        with self.factory() as session:
            job = session.scalar(select(PipelineJob).where(PipelineJob.case_id == case_id).with_for_update())
            if job is None or job.status not in {"failed", "interrupted", "retry_wait"}:
                raise ValueError("Aucun traitement en erreur à reprendre pour cet identifiant.")
            job.status, job.error_type, job.next_attempt_at = "queued", None, None
            job.attempts, job.updated_at = 0, utcnow()
            worker = session.get(PipelineWorker, "main")
            if worker:
                worker.next_attempt_at = None
            session.commit()
        self.pause_until = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Traiter une seule passe, sans surveillance continue.")
    parser.add_argument("--interval", type=float, default=10, help="Intervalle de surveillance en secondes (>= 1).")
    parser.add_argument("--retry", metavar="CASE", help="Autoriser explicitement la reprise d'un cas en erreur/interrompu.")
    args = parser.parse_args()
    if not 1 <= args.interval <= 3600:
        parser.error("--interval doit être entre 1 et 3600 secondes")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker()
    try:
        if args.retry:
            worker.retry(args.retry)
        with worker_lease(worker.engine) as lease:
            LOGGER.info("Surveillance ETICS des dépôts du dashboard démarrée (PID %s).", os.getpid())
            try:
                while True:
                    # Loss of the lease connection must stop work, never reconnect
                    # silently while another process may acquire the worker lock.
                    lease.exec_driver_sql("SELECT 1")
                    stats = worker.cycle()
                    if stats["discovered"] or stats["processed"] or args.once:
                        LOGGER.info("Passe terminée : %s", stats)
                    if args.once:
                        break
                    time.sleep(args.interval)
            finally:
                worker.heartbeat("stopped")
    except WorkerAlreadyRunning:
        LOGGER.info("Un worker ETICS est déjà actif ; aucun second traitement lancé.")
        return 0
    except KeyboardInterrupt:
        LOGGER.info("Surveillance arrêtée ; les traitements restent conservés en base.")
        return 0
    except Exception as error:
        LOGGER.error("Surveillance interrompue (%s). Vérifiez PostgreSQL et les migrations.", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
