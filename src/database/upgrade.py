"""Back up, migrate, load dwh and verify preservation of existing source rows."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from sqlalchemy import inspect, text

from src.database.db import Base, engine
from src.database.migrate import migrate
from src.dwh.etl import load_warehouse


def pg_binary(name):
    explicit = os.getenv("ETICS_" + name.upper())
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"ETICS_{name.upper()} executable is missing")
        return str(path)
    found = shutil.which(name)
    if found:
        return found
    parent = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "PostgreSQL"
    candidates = list(parent.glob(f"*/bin/{name}.exe"))
    if candidates:
        return str(max(candidates, key=lambda p: tuple(int(x) for x in p.parents[1].name.split(".") if x.isdigit())))
    raise FileNotFoundError(f"Install {name} or set ETICS_{name.upper()} to its executable")


def _json(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Unsupported source value: {type(value).__name__}")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   default=_json, separators=(",", ":")).encode()).hexdigest()


def source_snapshot(bind):
    """Read raw columns: ORM models may already contain columns not yet migrated."""
    result = {}
    with bind.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        inspector = inspect(connection)
        quote = bind.dialect.identifier_preparer.quote
        for table in ("recalls", "llm_results"):
            if inspector.has_table(table):
                rows = connection.execute(text(f"SELECT * FROM {quote(table)} ORDER BY id")).mappings()
                result[table] = {row["id"]: dict(row) for row in rows}
    return result


def verify_source_preservation(before, after):
    derived = 0
    for table, rows in before.items():
        current = after.get(table, {})
        for row_id, original in rows.items():
            if row_id not in current:
                raise RuntimeError(f"Missing historical {table} row {row_id}")
            for column, value in original.items():
                actual = current[row_id].get(column)
                if value == actual:
                    continue
                # Migration 001 may derive a previously absent metric. Its provenance
                # is mandatory; all existing non-NULL measures/verdicts stay unchanged.
                if table == "llm_results" and value is None and column in {"toxicity_score", "toxicity_score_origin"}:
                    origin = current[row_id].get("toxicity_score_origin") or ""
                    q = [original.get(f"q{i}") for i in range(1,8)]
                    expected = round((sum(not v for v in q[:5])+sum(bool(v) for v in q[5:]))*100/7,2)
                    if (origin == "derived_on_migration:seven-equal-weights-v1"
                        and original.get("score") is not None and original.get("toxicity_score") is None
                        and all(isinstance(v,bool) for v in q)
                        and current[row_id].get("toxicity_score") == expected):
                        derived += int(column == "toxicity_score")
                        continue
                raise RuntimeError(f"Historical {table} row {row_id}, column {column} changed")
    return derived


def backup_database(bind, destination):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Backup destination already exists")
    binary = pg_binary("pg_dump")
    url = bind.url
    command = [binary, "--format=custom", "--no-password"]
    for flag, value in (("host", url.host), ("port", url.port), ("username", url.username), ("dbname", url.database)):
        if value is not None:
            command.append(f"--{flag}={value}")
    environment = os.environ.copy()
    if url.password is not None:
        environment["PGPASSWORD"] = url.password
    environment["PGCONNECT_TIMEOUT"] = "5"
    for key in ("sslmode", "sslcert", "sslkey", "sslrootcert"):
        if key in url.query:
            environment["PG" + key.upper()] = url.query[key]
    # Python opens Unicode Windows paths correctly; some pg_dump builds interpret
    # --file via the active ANSI code page (the project's U+202F path then fails).
    # Stream the same custom-format dump to this already-open binary handle.
    with destination.open("xb") as stream:
        process = subprocess.run(command, env=environment, stdout=stream,
                                 stderr=subprocess.PIPE, timeout=300)
    if process.returncode:
        # Do not log connection strings or password-bearing process environments.
        raise RuntimeError("pg_dump failed; no migration executed. Inspect PostgreSQL access and pg_dump version")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("No usable PostgreSQL backup was produced")
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def upgrade_database(bind=engine, backup_dir=None):
    if bind.dialect.name != "postgresql":
        raise ValueError("ETICS upgrade requires PostgreSQL")
    # Fail before creating backup files or schema objects if the target is unavailable.
    with bind.connect() as c:
        database, server_version = c.execute(text("SELECT current_database(),current_setting('server_version')")).one()
    before = source_snapshot(bind)
    directory = Path(backup_dir or "backups").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stem = "etics_upgrade_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:8]
    backup = directory / (stem + ".dump")
    checksum = backup_database(bind, backup)
    report = {"database": database, "server_version": server_version, "backup": str(backup),
              "backup_sha256": checksum, "source_before_counts": {t:len(r) for t,r in before.items()},
              "source_before_hashes": {t:_digest(r) for t,r in before.items()}, "status": "backed_up"}
    report_path = directory / (stem + ".json")
    def write_report():
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    write_report()
    try:
        from src.database import models  # noqa: F401
        Base.metadata.create_all(bind)
        report["applied_migrations"] = migrate(bind)
        report["etl"] = load_warehouse(bind)
        after = source_snapshot(bind)
        report["newly_derived_metrics"] = verify_source_preservation(before, after)
        with bind.connect() as c:
            missing = c.execute(text("""SELECT count(*) FROM llm_results s
                LEFT JOIN dwh.fact_analysis f ON f.source_result_id=s.id
                WHERE f.source_result_id IS NULL""")).scalar_one()
            if missing:
                raise RuntimeError("Some source analyses are missing from dwh")
            report["warehouse_fact_count"] = c.execute(text("SELECT count(*) FROM dwh.fact_analysis")).scalar_one()
        report["source_after_counts"] = {t:len(r) for t,r in after.items()}
        report["status"] = "verified"
        write_report()
        return report
    except Exception as error:
        report["status"], report["error_type"] = "failed", type(error).__name__
        write_report()
        # No destructive auto-restore. A successfully written backup is retained.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    args = parser.parse_args()
    try:
        report = upgrade_database(backup_dir=args.backup_dir)
    except Exception as error:
        print(f"Upgrade failed ({type(error).__name__}); existing backups are preserved.")
        return 1
    # Keep the CLI report printable even in a Windows cp1252 terminal.
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
