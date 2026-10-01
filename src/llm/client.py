
import os
from typing import Protocol

from dotenv import load_dotenv

from src.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from src.llm.schemas import QuestionnaireAnswers
from src.pipeline_io import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


class QuestionnaireClient(Protocol):
    model_name: str

    def analyze(
        self, rappelconso_text: str, communication_text: str
    ) -> QuestionnaireAnswers: ...


class OpenAIQuestionnaireClient:

    def __init__(self, model_name: str | None = None) -> None:
        from openai import OpenAI

        self.model_name = model_name or os.getenv("OPENAI_MODEL", "gpt-4o")
        self._client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENROUTER_BASE_URL"),
        )
    def analyze(self, rappelconso_text: str, communication_text: str) -> QuestionnaireAnswers:
        completion = self._client.chat.completions.parse(
            model=self.model_name,
            temperature=0,
            max_tokens=1500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(rappelconso_text, communication_text),
                },
            ],
            response_format=QuestionnaireAnswers,
        )
        message = completion.choices[0].message
        if message.refusal:
            raise RuntimeError(f"Le modèle a refusé l'analyse : {message.refusal}")
        if message.parsed is None:
            raise RuntimeError("Le modèle n'a retourné aucune sortie structurée.")
        return message.parsed
