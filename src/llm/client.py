"""One configured API, one SDK client and one request per analysis attempt."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import os
from typing import Protocol
from urllib.parse import urlsplit

from dotenv import load_dotenv

from src.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from src.llm.schemas import QuestionnaireAnswers
from src.pipeline_io import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


class QuestionnaireClient(Protocol):
    model_name: str

    def analyze(self, rappelconso_text: str, communication_text: str) -> QuestionnaireAnswers: ...


@dataclass(frozen=True)
class APIEndpoint:
    model: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    disable_reasoning: bool = False

    @property
    def provider(self):
        return urlsplit(self.base_url or "https://api.openai.com/v1").hostname


class APIUnavailableError(RuntimeError):
    """The sole API is unavailable; only a bounded retry on that API is allowed."""
    def __init__(self, message, *, retry_after_seconds=0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class APIRequestError(RuntimeError):
    """Non-retryable request failure, with no provider response body exposed."""


def configured_endpoint(model_name=None):
    # Legacy LLM_API_2_* / LLM_API_3_* variables are deliberately ignored.
    return APIEndpoint(
        model=model_name or os.getenv("OPENAI_MODEL", "gpt-4o"),
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        base_url=os.getenv("OPENROUTER_BASE_URL") or None,
        disable_reasoning=os.getenv("LLM_DISABLE_REASONING", "0").lower() in {"1", "true", "yes"},
    )


def retry_delay(error):
    value = getattr(getattr(error, "response", None), "headers", {}).get("retry-after")
    if not value:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


class OpenAIQuestionnaireClient:
    def __init__(self, model_name: str | None = None, *, endpoint: APIEndpoint | None = None):
        from openai import OpenAI

        self.endpoint = endpoint if endpoint is not None else configured_endpoint(model_name)
        if not self.endpoint.api_key or not self.endpoint.model.strip():
            raise ValueError("OPENAI_API_KEY et OPENAI_MODEL doivent être configurés pour l'API unique.")
        self.timeout = float(os.getenv("LLM_API_TIMEOUT_SECONDS", "60"))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("LLM_API_TIMEOUT_SECONDS must be positive")
        max_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500"))
        if max_tokens <= 0:
            raise ValueError("LLM_MAX_OUTPUT_TOKENS must be positive")
        self.model_name = self.endpoint.model
        self.model_revision = None
        self._generation = {"temperature": 0, "max_tokens": max_tokens}
        self.attempts = []
        self.parameters = {**self._generation, "api_provider": self.endpoint.provider,
            "api_slot": "primary", "timeout_seconds": self.timeout,
            "sdk_max_retries": 0, "api_attempts": self.attempts}
        if self.endpoint.disable_reasoning:
            self.parameters["reasoning"] = {"enabled": False}
        self._client = OpenAI(api_key=self.endpoint.api_key, base_url=self.endpoint.base_url,
                              max_retries=0, timeout=self.timeout)

    def analyze(self, rappelconso_text: str, communication_text: str) -> QuestionnaireAnswers:
        from openai import APIConnectionError, APIStatusError

        self.model_revision = None
        self.attempts = []
        self.parameters["api_attempts"] = self.attempts
        attempt = {"slot": "primary", "provider": self.endpoint.provider, "model": self.model_name}
        options = {"extra_body": {"reasoning": {"enabled": False}}} if self.endpoint.disable_reasoning else {}
        try:
            completion = self._client.chat.completions.parse(
                model=self.model_name, **self._generation, **options,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": build_user_prompt(rappelconso_text, communication_text)}],
                response_format=QuestionnaireAnswers,
            )
        except APIConnectionError as error:
            self.attempts.append({**attempt, "status": "connection_error", "error_type": type(error).__name__})
            raise APIUnavailableError("L'API unique est injoignable. Aucun service de secours n'est utilisé.") from None
        except APIStatusError as error:
            status = error.status_code
            self.attempts.append({**attempt, "status": "http_error", "http_status": status})
            if status in {401, 402, 403, 404, 408, 409, 429} or status >= 500:
                raise APIUnavailableError(f"L'API unique est indisponible (HTTP {status}).",
                                          retry_after_seconds=retry_delay(error)) from None
            raise APIRequestError(f"L'API unique a rejeté la requête (HTTP {status}).") from None
        except Exception as error:
            self.attempts.append({**attempt, "status": "invalid_response", "error_type": type(error).__name__})
            raise RuntimeError("L'API unique n'a pas fourni une réponse structurée valide.") from None
        self.model_revision = completion.model
        self.attempts.append({**attempt, "status": "responded"})
        message = completion.choices[0].message
        if message.refusal:
            raise RuntimeError("Le modèle a refusé l'analyse")
        if message.parsed is None:
            raise RuntimeError("Le modèle n'a retourné aucune sortie structurée")
        return message.parsed
