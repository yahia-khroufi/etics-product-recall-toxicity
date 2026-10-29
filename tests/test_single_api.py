"""No live API calls: assert one endpoint, one SDK and one request per attempt."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import openai
import pytest

from src.llm.client import (APIEndpoint, APIUnavailableError, APIRequestError,
                            OpenAIQuestionnaireClient, configured_endpoint, retry_delay)
from src.llm.schemas import QuestionnaireAnswers


def status_error(status, headers=None):
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://unit.invalid/v1"))
    return openai.APIStatusError("unit-secret must never be logged", response=response, body={})


def completion(refusal=None, parsed=True):
    answers = QuestionnaireAnswers.model_validate({f"q{i}": {"value": i <= 5,
        "evidence": ["citation"] if i <= 5 else []} for i in range(1, 8)})
    return SimpleNamespace(model="unit-revision", choices=[SimpleNamespace(message=SimpleNamespace(
        refusal=refusal, parsed=answers if parsed else None))])


def make_client(monkeypatch, outcome):
    parse = Mock(side_effect=outcome) if isinstance(outcome, (BaseException, list)) else Mock(return_value=outcome)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(openai, "OpenAI", factory)
    client = OpenAIQuestionnaireClient(endpoint=APIEndpoint("unit-model", "unit-secret", "https://unit.invalid/v1"))
    return client, parse, factory


def test_configuration_ignores_all_old_backup_variables(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-primary-secret")
    monkeypatch.setenv("OPENAI_MODEL", "primary-model")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://primary.invalid/v1")
    for number in (2, 3):
        monkeypatch.setenv(f"LLM_API_{number}_KEY", f"unit-backup-secret-{number}")
        monkeypatch.setenv(f"LLM_API_{number}_BASE_URL", "https://unused.invalid/v1")
        monkeypatch.delenv(f"LLM_API_{number}_MODEL", raising=False)  # Even malformed old configs are ignored.
    sdk = Mock()
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(openai, "OpenAI", factory)
    client = OpenAIQuestionnaireClient()
    factory.assert_called_once()
    assert factory.call_args.kwargs["api_key"] == "unit-primary-secret"
    assert factory.call_args.kwargs["base_url"] == "https://primary.invalid/v1"
    assert client.model_name == "primary-model"
    assert "unit-primary-secret" not in repr(configured_endpoint())


def test_success_records_primary_metadata_and_disables_sdk_retries(monkeypatch):
    client, parse, factory = make_client(monkeypatch, completion())
    assert client.analyze("official", "company").q1.value
    factory.assert_called_once()
    parse.assert_called_once()
    assert factory.call_args.kwargs["max_retries"] == 0
    assert client.model_name == "unit-model" and client.model_revision == "unit-revision"
    assert client.parameters["api_slot"] == "primary"
    assert client.parameters["api_provider"] == "unit.invalid"
    assert client.attempts[0]["status"] == "responded"
    assert "unit-secret" not in json.dumps(client.parameters)


@pytest.mark.parametrize("status", [401, 402, 403, 404, 408, 409, 429, 500, 503])
def test_api_error_never_tries_another_key(monkeypatch, status):
    client, parse, factory = make_client(monkeypatch, status_error(status))
    with pytest.raises(APIUnavailableError) as caught:
        client.analyze("official", "company")
    factory.assert_called_once()
    parse.assert_called_once()
    assert len(client.attempts) == 1 and client.attempts[0]["http_status"] == status
    assert "unit-secret" not in str(caught.value)
    assert client.model_name == "unit-model"


def test_network_error_has_no_fallback(monkeypatch):
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://unit.invalid/v1"))
    client, parse, factory = make_client(monkeypatch, error)
    with pytest.raises(APIUnavailableError):
        client.analyze("official", "company")
    parse.assert_called_once()
    factory.assert_called_once()
    assert client.attempts[0]["status"] == "connection_error"


@pytest.mark.parametrize("status", [400, 422])
def test_bad_request_is_safe_and_not_retryable(monkeypatch, status):
    client, parse, _ = make_client(monkeypatch, status_error(status))
    with pytest.raises(APIRequestError) as caught:
        client.analyze("official", "company")
    assert "unit-secret" not in str(caught.value)
    parse.assert_called_once()


@pytest.mark.parametrize("result", [completion(refusal="refused"), completion(parsed=False)])
def test_content_refusal_or_empty_output_does_not_trigger_another_request(monkeypatch, result):
    client, parse, _ = make_client(monkeypatch, result)
    with pytest.raises(RuntimeError):
        client.analyze("official", "company")
    parse.assert_called_once()


def test_malformed_structured_response_is_recorded_without_provider_body(monkeypatch):
    client, parse, _ = make_client(monkeypatch, ValueError("unit-secret"))
    with pytest.raises(RuntimeError) as caught:
        client.analyze("official", "company")
    assert "unit-secret" not in str(caught.value)
    assert client.attempts[0]["status"] == "invalid_response"
    parse.assert_called_once()


def test_retry_after_is_exposed_without_retrying_the_request(monkeypatch):
    client, parse, _ = make_client(monkeypatch, status_error(429, {"retry-after": "1200"}))
    with pytest.raises(APIUnavailableError) as caught:
        client.analyze("official", "company")
    assert caught.value.retry_after_seconds == 1200
    parse.assert_called_once()


@pytest.mark.parametrize("value", ["invalid", "nan", "inf", "-42"])
def test_invalid_retry_after_values_are_safe(value):
    assert retry_delay(status_error(429, {"retry-after": value})) == 0


def test_retry_after_http_date():
    date = datetime.now(timezone.utc) + timedelta(seconds=1200)
    assert 1197 < retry_delay(status_error(429, {"retry-after": format_datetime(date)})) <= 1200


def test_explicit_later_attempt_uses_same_sdk_key_and_model(monkeypatch):
    client, parse, factory = make_client(monkeypatch, [status_error(503), completion()])
    with pytest.raises(APIUnavailableError):
        client.analyze("a", "b")
    client.analyze("a", "b")
    factory.assert_called_once()
    assert parse.call_count == 2
    assert all(call.kwargs["model"] == "unit-model" for call in parse.call_args_list)
    assert len(client.attempts) == 1 and client.attempts[0]["status"] == "responded"


def test_legacy_key_cannot_replace_missing_primary_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_API_2_KEY", "unused-backup-key")
    factory = Mock()
    monkeypatch.setattr(openai, "OpenAI", factory)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIQuestionnaireClient()
    factory.assert_not_called()


def test_single_reasoning_setting_and_output_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-key")
    monkeypatch.setenv("OPENAI_MODEL", "unit-model")
    monkeypatch.setenv("LLM_DISABLE_REASONING", "1")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
    parse = Mock(return_value=completion())
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(openai, "OpenAI", Mock(return_value=sdk))
    client = OpenAIQuestionnaireClient()
    client.analyze("official", "company")
    assert parse.call_args.kwargs["extra_body"] == {"reasoning": {"enabled": False}}
    assert parse.call_args.kwargs["max_tokens"] == 4096


@pytest.mark.parametrize("error", [APIUnavailableError("unavailable"), APIRequestError("invalid request")])
def test_pipeline_stops_batch_on_sole_api_failure(monkeypatch, error):
    import src.pipeline as pipeline
    monkeypatch.setattr("sys.argv", ["pipeline", "--from-processed", "--with-llm"])
    monkeypatch.setattr(pipeline, "processed_case_ids", lambda _: ["a", "b", "c"])
    monkeypatch.setattr(pipeline, "init_db", lambda: None)
    client = object()
    monkeypatch.setattr(pipeline, "OpenAIQuestionnaireClient", lambda: client)
    runner = Mock(side_effect=error)
    monkeypatch.setattr(pipeline, "run_from_processed", runner)
    assert pipeline.main() == 1
    runner.assert_called_once_with("a", True, client)


def test_pipeline_reuses_one_client_for_the_batch(monkeypatch):
    import src.pipeline as pipeline
    monkeypatch.setattr("sys.argv", ["pipeline", "--from-processed", "--with-llm"])
    monkeypatch.setattr(pipeline, "processed_case_ids", lambda _: ["a", "b"])
    monkeypatch.setattr(pipeline, "init_db", lambda: None)
    client = object()
    factory = Mock(return_value=client)
    monkeypatch.setattr(pipeline, "OpenAIQuestionnaireClient", factory)
    runner = Mock()
    monkeypatch.setattr(pipeline, "run_from_processed", runner)
    assert pipeline.main() == 0
    factory.assert_called_once()
    assert all(call.args[2] is client for call in runner.call_args_list)
