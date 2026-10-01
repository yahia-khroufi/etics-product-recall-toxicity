
import re
import unicodedata

from src.database.db import SessionLocal
from src.database.repository import SessionFactory, save_llm_result
from src.database.schemas import LLMResultCreate
from src.llm.client import OpenAIQuestionnaireClient, QuestionnaireClient
from src.llm.prompts import PROMPT_VERSION
from src.llm.schemas import QuestionnaireAnswers
from src.pipeline_io import EXTRACTED_DIR, case_folders
from src.preprocessing.clean import clean_text
from src.preprocessing.preprocess import read_text
from src.scoring.score import score_case


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def verify_evidence(
    answers: QuestionnaireAnswers,
    rappelconso_text: str,
    communication_text: str,
) -> bool:
    """Vérifie Q1-Q6 dans la communication et Q7 dans l'ensemble des deux textes."""
    communication = _normalized(communication_text)
    both = _normalized(rappelconso_text + "\n" + communication_text)
    for number in range(1, 8):
        answer = getattr(answers, f"q{number}")
        if answer.value and not answer.evidence:
            return False
        source = both if number == 7 else communication
        if any(_normalized(quote) not in source for quote in answer.evidence):
            return False
    return True


def analyze_case(
    case_id: str,
    client: QuestionnaireClient | None = None,
    session_factory: SessionFactory = SessionLocal,
):
    """Analyse un cas et persiste le résultat via le repository, jamais via le LLM."""
    resolved_case_id, folder = case_folders(EXTRACTED_DIR, [case_id])[0]
    rappelconso_text = clean_text(read_text(folder / "rappelconso.txt"))
    communication_text = clean_text(read_text(folder / "communication_entreprise.txt"))
    if not rappelconso_text or not communication_text:
        raise ValueError(f"{resolved_case_id} : les deux textes sont requis pour Q1-Q7.")

    llm_client = client or OpenAIQuestionnaireClient()
    answers = llm_client.analyze(rappelconso_text, communication_text)
    scoring = score_case(answers)
    evidence = {
        f"q{number}": getattr(answers, f"q{number}").evidence
        for number in range(1, 8)
    }
    result = LLMResultCreate(
        case_id=resolved_case_id,
        **{f"q{number}": getattr(answers, f"q{number}").value for number in range(1, 8)},
        evidence=evidence,
        evidence_verified=verify_evidence(
            answers, rappelconso_text, communication_text
        ),
        score=scoring.score,
        severity=scoring.severity,
        transparency_class=scoring.transparency_class,
        verdict=scoring.verdict,
        explanation=scoring.explanation,
        model_name=llm_client.model_name,
        prompt_version=PROMPT_VERSION,
    )
    return save_llm_result(result, session_factory=session_factory)
