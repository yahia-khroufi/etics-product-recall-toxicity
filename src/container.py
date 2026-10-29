"""Docker entrypoint: internal DB routing, safe initialization and process health."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from sqlalchemy.engine import URL, make_url


def configure_database(environ=None):
    env = os.environ if environ is None else environ
    if env.get("ETICS_CONTAINER") != "1":
        return
    url = URL.create("postgresql+psycopg2", username=env.get("POSTGRES_USER", "etics"),
        password=env.get("POSTGRES_PASSWORD", "etics123"), host=env.get("POSTGRES_HOST", "db"),
        port=int(env.get("POSTGRES_PORT", "5432")), database=env.get("POSTGRES_DB", "etics_db"))
    env["DATABASE_URL"] = url.render_as_string(hide_password=False)
    # The host's localhost:5433 connection must not shadow the container route.
    env["DASHBOARD_DATABASE_URL"] = env.get("DASHBOARD_DATABASE_URL_CONTAINER", "")


def worker_health():
    from sqlalchemy import text
    from src.database.db import engine
    with engine.connect() as connection:
        worker = connection.execute(text("SELECT pid,status,heartbeat_at FROM pipeline_workers WHERE name='main'")).mappings().first()
    if not worker or worker["status"] == "stopped":
        return False
    # The PID must belong to this container, not a still-running Windows worker.
    command = Path(f"/proc/{worker['pid']}/cmdline")
    if not command.is_file() or b"src.automation" not in command.read_bytes():
        return False
    return (datetime.now(timezone.utc) - worker["heartbeat_at"]).total_seconds() < 900


def main():
    configure_database()
    role = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    if role == "initialize":
        from src.database.upgrade import upgrade_database
        try:
            report = upgrade_database(backup_dir=Path("/app/backups"))
            print(json.dumps({"status": report["status"], "migrations": report["applied_migrations"],
                              "source_counts": report["source_after_counts"], "etl": report["etl"]}))
            return 0
        except Exception as error:
            print(f"Initialization failed ({type(error).__name__}); dashboard and worker will not start.", file=sys.stderr)
            return 1
    if role == "dashboard":
        command = [sys.executable, "-m", "streamlit", "run", "dashboard/app.py",
                   "--server.address", "0.0.0.0", "--server.port", "8501", *sys.argv[2:]]
    elif role == "worker":
        command = [sys.executable, "-m", "src.automation", *sys.argv[2:]]
    elif role == "test":
        # Tests create disposable databases/schemas on the admin DB, never business tables.
        url = make_url(os.environ["DATABASE_URL"]).set(database="postgres").render_as_string(hide_password=False)
        os.environ.update(DATABASE_URL=url, ETICS_TEST_ADMIN_URL=url, TEST_POSTGRES="1")
        command = [sys.executable, "-m", "pytest", *sys.argv[2:]]
    elif role == "health-dashboard":
        from urllib.request import urlopen
        try:
            with urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5) as response:
                return 0 if response.status == 200 else 1
        except Exception:
            return 1
    elif role == "health-worker":
        try:
            return 0 if worker_health() else 1
        except Exception:
            return 1
    else:
        print("Unknown container role", file=sys.stderr)
        return 2
    os.execv(command[0], command)


if __name__ == "__main__":
    raise SystemExit(main())
