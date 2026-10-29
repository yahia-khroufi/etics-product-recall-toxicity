import importlib.util
from pathlib import Path
from unittest.mock import MagicMock
import pytest

SPEC = importlib.util.spec_from_file_location("etics_azure_initialization", Path(__file__).parents[1]/"scripts"/"initialization.py")
job = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(job)


def test_empty_execution_history_is_not_success():
    with pytest.raises(RuntimeError):
        job.latest_success([])


def test_latest_failure_does_not_reuse_older_success():
    with pytest.raises(RuntimeError):
        job.latest_success([{"startTime":"2026-01-01","status":"Succeeded"},
                            {"properties":{"startTime":"2026-01-02","status":"Failed"}}])


def test_start_waits_for_its_own_execution(monkeypatch):
    azure = MagicMock(side_effect=[[],{"name":"run-1"},{"properties":{"status":"Running"}},
                                   {"properties":{"status":"Succeeded"}}])
    monkeypatch.setattr(job,"azure",azure)
    monkeypatch.setattr(job.time,"sleep",MagicMock())
    job.manage("start","rg-test","init-test")
    assert azure.call_count == 4
    assert "run-1" in azure.call_args.args[0]


def test_start_refuses_overlapping_initialization(monkeypatch):
    azure = MagicMock(return_value=[{"properties":{"status":"Running"}}])
    monkeypatch.setattr(job,"azure",azure)
    with pytest.raises(RuntimeError):
        job.manage("start","rg-test","init-test")
    assert azure.call_count == 1


def test_cli_errors_do_not_expose_provider_body(monkeypatch):
    result = MagicMock(returncode=1,stderr="secret provider body")
    monkeypatch.setattr(job.subprocess,"run",MagicMock(return_value=result))
    with pytest.raises(RuntimeError) as caught:
        job.azure(["containerapp","job","list"])
    assert "secret provider body" not in str(caught.value)
