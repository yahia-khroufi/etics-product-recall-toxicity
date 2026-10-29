"""Container Apps adapter. Business modules and the local Compose stay unchanged."""
import os
import signal
import subprocess
import sys
import threading
import time


def schema_ready(engine):
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return bool(connection.exec_driver_sql("""
                SELECT to_regclass('dwh.analysis_history') IS NOT NULL
                   AND to_regclass('public.pipeline_jobs') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM public.schema_migrations
                               WHERE version='004_pipeline_jobs.sql')
            """).scalar_one())
    except Exception:
        # Database errors may carry connection details; never print their body.
        return False


def wait_for_schema(engine, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if schema_ready(engine):
            return
        time.sleep(3)
    raise RuntimeError("Initialisation PostgreSQL requise avant le démarrage.")


def supervise_worker(popen=None, stop=None):
    """A rolling revision may temporarily wait for the old worker's SQL lease."""
    popen = popen or subprocess.Popen
    stop = stop or threading.Event()
    child = None

    def terminate(_signum, _frame):
        stop.set()
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT)

    previous = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        while not stop.is_set():
            child = popen([sys.executable, "-m", "src.container", "worker"])
            result = child.wait()
            if stop.is_set():
                return 0
            if result != 0:
                return result
            # WorkerAlreadyRunning returns 0 in the existing application.
            stop.wait(10)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    role = os.getenv("ETICS_CLOUD_ROLE", "dashboard")
    if role not in {"dashboard", "worker"}:
        raise ValueError("Rôle Container Apps invalide.")
    from src.container import configure_database, main as container_main
    configure_database()
    from src.database.db import engine
    wait_for_schema(engine)
    engine.dispose()
    if role == "worker":
        return supervise_worker()
    sys.argv = ["src.container", "dashboard"]
    return container_main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Container Apps startup failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
