import importlib.util
from pathlib import Path
import signal
import sys
import threading
from unittest.mock import MagicMock

import pytest

SPEC = importlib.util.spec_from_file_location("etics_cloud_start", Path(__file__).parents[1]/"adapters"/"cloud_start.py")
cloud = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud)


def test_schema_guard_is_read_only():
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.exec_driver_sql.return_value.scalar_one.return_value = True
    assert cloud.schema_ready(engine)
    sql = [c.args[0] for c in connection.exec_driver_sql.call_args_list]
    assert sql[0] == "SET TRANSACTION READ ONLY"
    assert "004_pipeline_jobs.sql" in sql[1]
    connection.commit.assert_not_called()


def test_missing_schema_does_not_expose_connection_error(capsys):
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("private connection detail")
    assert not cloud.schema_ready(engine)
    assert "private" not in capsys.readouterr().err


def test_startup_waits_for_successful_initialization(monkeypatch):
    probe = MagicMock(side_effect=[False, True])
    pause = MagicMock()
    monkeypatch.setattr(cloud, "schema_ready", probe)
    monkeypatch.setattr(cloud.time, "sleep", pause)
    cloud.wait_for_schema(object())
    assert probe.call_count == 2
    pause.assert_called_once_with(3)


def test_startup_timeout(monkeypatch):
    monkeypatch.setattr(cloud.time, "monotonic", MagicMock(side_effect=[0, 2]))
    with pytest.raises(RuntimeError, match="Initialisation"):
        cloud.wait_for_schema(object(), timeout=1)


def test_worker_waits_for_old_revision_lease_then_propagates_failure():
    first, second = MagicMock(), MagicMock()
    first.wait.return_value = 0
    second.wait.return_value = 9
    popen = MagicMock(side_effect=[first, second])
    stop = MagicMock()
    stop.is_set.return_value = False
    assert cloud.supervise_worker(popen, stop) == 9
    assert popen.call_count == 2
    stop.wait.assert_called_once_with(10)


def test_worker_translates_platform_stop_to_sigint(monkeypatch):
    handlers = {}
    def install(sig, handler):
        previous = handlers.get(sig, signal.SIG_DFL)
        handlers[sig] = handler
        return previous
    monkeypatch.setattr(cloud.signal, "signal", install)
    child = MagicMock()
    child.poll.return_value = None
    def stop_during_wait():
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return 0
    child.wait.side_effect = stop_during_wait
    assert cloud.supervise_worker(lambda _: child, threading.Event()) == 0
    child.send_signal.assert_called_once_with(signal.SIGINT)
    assert handlers[signal.SIGTERM] == signal.SIG_DFL


def test_reject_unknown_cloud_role(monkeypatch):
    monkeypatch.setenv("ETICS_CLOUD_ROLE", "unexpected")
    with pytest.raises(ValueError):
        cloud.main()


def test_dashboard_starts_only_after_schema_check(monkeypatch):
    import src.container
    import src.database.db
    calls = []
    monkeypatch.setenv("ETICS_CLOUD_ROLE", "dashboard")
    monkeypatch.setattr(src.container, "configure_database", lambda: calls.append("configure"))
    monkeypatch.setattr(src.container, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(cloud, "wait_for_schema", lambda _: calls.append("schema"))
    monkeypatch.setattr(sys, "argv", ["test-cloud"])
    assert cloud.main() == 0
    assert calls == ["configure", "schema", "dashboard"]


def test_terraform_defines_no_vm_or_ssh_access():
    source = "\n".join(p.read_text(encoding="utf-8") for p in Path(__file__).parents[1].glob("*.tf"))
    assert 'resource "azurerm_linux_virtual_machine"' not in source
    assert "ssh_source_cidr" not in source


def test_real_postgresql_schema_guard_requires_migrations(pg_engine):
    from src.database.db import Base
    from src.database.migrate import migrate
    assert not cloud.schema_ready(pg_engine)
    Base.metadata.create_all(pg_engine)
    migrate(pg_engine)
    assert cloud.schema_ready(pg_engine)
    with pg_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM llm_results").scalar_one() == 0


# Fixture creates/drops only a unique etics_test_<uuid> database.
from tests.test_dwh_postgresql import pg_engine
