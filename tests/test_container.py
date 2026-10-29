from sqlalchemy.engine import make_url

from src.container import configure_database


def test_container_uses_internal_postgresql_even_with_host_env():
    env = dict(ETICS_CONTAINER="1", POSTGRES_USER="user", POSTGRES_PASSWORD="p@ss:/?#%",
               POSTGRES_DB="test-db", DATABASE_URL="postgresql://localhost:5433/host",
               DASHBOARD_DATABASE_URL="postgresql://localhost:5433/readonly")
    configure_database(env)
    url = make_url(env["DATABASE_URL"])
    assert (url.host, url.port, url.database, url.username, url.password) == ("db", 5432, "test-db", "user", "p@ss:/?#%")
    assert env["DASHBOARD_DATABASE_URL"] == ""


def test_container_preserves_explicit_readonly_container_dsn():
    env = dict(ETICS_CONTAINER="1", DASHBOARD_DATABASE_URL_CONTAINER="postgresql://readonly@db/db")
    configure_database(env)
    assert env["DASHBOARD_DATABASE_URL"] == env["DASHBOARD_DATABASE_URL_CONTAINER"]


def test_host_configuration_is_preserved():
    env = dict(DATABASE_URL="postgresql://localhost:5433/host")
    before = env.copy()
    configure_database(env)
    assert env == before
