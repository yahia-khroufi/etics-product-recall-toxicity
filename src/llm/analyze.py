
import re
import unicodedata
import hashlib

from src.database.db import SessionLocal
from src.database.repository import SessionFactory, save_llm_result, score_llm_result
from src.database.schemas import LLMResultCreate
from src.llm.client import OpenAIQuestionnaireClient, QuestionnaireClient
from src.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from src.scoring.score import SCORING_VERSION
from src.database.provenance import start_run, finish_run
from src.llm.schemas import QuestionnaireAnswers
from src.pipeline_io import EXTRACTED_DIR, case_folders
from src.preprocessing.clean import clean_text
from src.preprocessing.preprocess import read_text


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
        quotes = [_normalized(quote) for quote in answer.evidence]
        if any(not quote or quote not in source for quote in quotes):
            return False
        if number == 7 and answer.value:
            official = _normalized(rappelconso_text)
            # Distinct, source-specific spans are needed to attest a comparison.
            if not (any(q in official and q not in communication for q in quotes)
                    and any(q in communication and q not in official for q in quotes)):
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
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + "\n" + build_user_prompt("", "")).encode()).hexdigest()
    run_id, official_id, company_id = start_run(
        resolved_case_id, (rappelconso_text, communication_text), llm_client.model_name,
        PROMPT_VERSION, prompt_hash, SCORING_VERSION, session_factory,
        parameters=getattr(llm_client,"parameters",None),
    )
    try:
        return _execute_analysis(resolved_case_id, llm_client, rappelconso_text, communication_text,
                                 run_id, official_id, company_id, session_factory)
    except Exception as error:
        _update_client_metadata(run_id, llm_client, session_factory)
        finish_run(run_id, "failed", session_factory, type(error).__name__, getattr(llm_client, "model_revision", None))
        raise


def _execute_analysis(case_id, llm_client, rappelconso_text, communication_text,
                      run_id, official_id, company_id, session_factory):
    answers = llm_client.analyze(rappelconso_text, communication_text)
    # Persist provider metadata before the result becomes visible to the ETL.
    _update_client_metadata(run_id, llm_client, session_factory)
    evidence = {
        f"q{number}": getattr(answers, f"q{number}").evidence
        for number in range(1, 8)
    }
    result = LLMResultCreate(
        case_id=case_id,
        run_id=run_id, official_version_id=official_id, company_version_id=company_id,
        **{f"q{number}": getattr(answers, f"q{number}").value for number in range(1, 8)},
        evidence=evidence,
        evidence_verified=verify_evidence(
            answers, rappelconso_text, communication_text
        ),
        model_name=llm_client.model_name,
        prompt_version=PROMPT_VERSION,
    )
    saved = save_llm_result(result, session_factory=session_factory)
    result = score_llm_result(saved.id, session_factory=session_factory)
    finish_run(run_id, result.analysis_status, session_factory,
               model_revision=getattr(llm_client, "model_revision", None))
    return result


def _update_client_metadata(run_id, client, session_factory):
    from src.database.models import AnalysisRun
    with session_factory() as session:
        run = session.get(AnalysisRun, run_id)
        run.model_name = client.model_name
        run.model_revision = getattr(client, "model_revision", None)
        run.parameters = getattr(client, "parameters", {})
        session.commit()
