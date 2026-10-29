"""Additive, checksummed PostgreSQL migrations; safe to repeat after interruption."""
import hashlib
from pathlib import Path

from src.database.db import engine

MIGRATIONS = Path(__file__).with_name("migrations")


def migrate(bind=engine):
    if bind.dialect.name != "postgresql":
        raise ValueError("SQL migrations require PostgreSQL")
    raw = bind.raw_connection()
    applied = []
    try:
        raw.autocommit = True
        with raw.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(19740321)")
            try:
                cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
                for path in sorted(MIGRATIONS.glob("*.sql")):
                    sql = path.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(sql.encode()).hexdigest()
                    cur.execute("SELECT sha256 FROM schema_migrations WHERE version=%s", (path.name,))
                    found = cur.fetchone()
                    if found:
                        if found[0] != checksum:
                            raise RuntimeError(f"Migration changed after application: {path.name}")
                        continue
                    cur.execute(sql)
                    cur.execute("INSERT INTO schema_migrations(version,sha256) VALUES (%s,%s)", (path.name, checksum))
                    applied.append(path.name)
            finally:
                cur.execute("ROLLBACK")
                cur.execute("SELECT pg_advisory_unlock(19740321)")
    finally:
        raw.close()
    return applied


def main():
    from src.database.db import Base
    from src.database import models  # noqa: F401
    # Creates missing tables only; existing tables are upgraded by SQL below.
    Base.metadata.create_all(engine)
    print("Applied:", migrate())


if __name__ == "__main__":
    main()
