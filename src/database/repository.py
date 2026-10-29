
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.db import SessionLocal
from src.database.models import LLMResult, Recall, AnalysisRun, RecallSnapshot, DocumentVersion, Document
from src.database.schemas import LLMResultCreate, RecallData
from src.llm.schemas import QuestionnaireAnswers
from src.scoring.score import score_case
from src.scoring.score import SCORING_VERSION, TOXICITY_METRIC_VERSION
from src.database.provenance import capture_recall, utcnow

SessionFactory = Callable[[], Session]


def _recall_values(data: RecallData) -> dict[str, Any]:
    official = data.rappelconso
    company = data.communication_entreprise
    return {
        "case_id": data.case_id,
        **official.model_dump(),
        "communication_produit": company.produit,
        "communication_marque": company.marque,
        "communication_gtin": company.gtin,
        "communication_lots": company.lots,
        "communication_motif": company.motif,
        "communication_risque": company.risque,
        "communication_recommandations": company.recommandations,
    }


def save_recall(data: dict[str, Any], session_factory: SessionFactory = SessionLocal) -> Recall:
    session = session_factory()
    try:
        validated = RecallData.model_validate(data)
        values = _recall_values(validated)
        recall = session.scalar(select(Recall).where(Recall.case_id == validated.case_id).with_for_update())
        if "organization_key" in validated.model_fields_set:
            values["organization_key"] = validated.organization_key
        if recall is None:
            recall = Recall(**values)
            session.add(recall)
        else:
            for field, value in values.items():
                setattr(recall, field, value)
        session.flush()
        capture_recall(session, recall)
        session.commit()
        session.refresh(recall)
        return recall
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def score_llm_result(
    result_id: int, session_factory: SessionFactory = SessionLocal,
) -> LLMResult:
    """Calculate from committed answers and update only this analysis.

    This separate transaction leaves the raw answers available for retry if
    calculation or persistence fails. Older analyses are never overwritten.
    """
    session = session_factory()
    try:
        result = session.scalar(select(LLMResult).where(LLMResult.id == result_id).with_for_update())
        if result is None:
            raise LookupError(f"Analyse introuvable : {result_id}")
        # Completed historical assessments are immutable through this API.
        if result.score is not None:
            return result
        if not result.evidence_verified:
            result.analysis_status = "needs_review"
            session.commit()
            return result
        answers = QuestionnaireAnswers.model_validate({
            f"q{number}": {
                "value": getattr(result, f"q{number}"),
                "evidence": result.evidence.get(f"q{number}", []),
            }
            for number in range(1, 8)
        })
        scoring = score_case(answers)
        for field in (
            "toxicity_score", "score", "severity", "transparency_class",
            "verdict", "explanation",
        ):
            setattr(result, field, getattr(scoring, field))
        result.scoring_version = SCORING_VERSION
        result.toxicity_score_origin = TOXICITY_METRIC_VERSION
        result.analysis_status = "scored"
        result.scored_at = utcnow()
        if result.run_id:
            run = session.get(AnalysisRun, result.run_id)
            run.status, run.finished_at, run.error_type = "scored", utcnow(), None
        session.commit()
        session.refresh(result)
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_recall_by_case_id(
    case_id: str, session_factory: SessionFactory = SessionLocal
) -> Recall | None:
    session = session_factory()
    try:
        return session.scalar(select(Recall).where(Recall.case_id == case_id))
    finally:
        session.close()


def save_llm_result(
    data: dict[str, Any] | LLMResultCreate,
    session_factory: SessionFactory = SessionLocal,
) -> LLMResult:
    """Valide et stocke un résultat LLM lié à un rappel existant."""
    session = session_factory()
    try:
        validated = (
            data
            if isinstance(data, LLMResultCreate)
            else LLMResultCreate.model_validate(data)
        )
        recall = session.scalar(select(Recall).where(Recall.case_id == validated.case_id))
        if recall is None:
            raise LookupError(
                f"Aucun rappel pour case_id={validated.case_id}. Exécuter d'abord le chemin base."
            )
        values = validated.model_dump(exclude={"case_id"})
        references = (validated.run_id, validated.official_version_id, validated.company_version_id)
        if any(ref is not None for ref in references):
            if any(ref is None for ref in references):
                raise ValueError("An analysis needs a run and both document versions")
            run = session.get(AnalysisRun, validated.run_id)
            if run is None or session.get(RecallSnapshot, run.recall_snapshot_id).recall_id != recall.id:
                raise ValueError("Run does not belong to this recall")
            for role, version_id in (("official",validated.official_version_id),("company",validated.company_version_id)):
                version = session.get(DocumentVersion, version_id)
                document = session.get(Document, version.document_id) if version else None
                if document is None or document.recall_id != recall.id or document.role != role:
                    raise ValueError("Document version does not belong to this recall/role")
        result = LLMResult(recall_id=recall.id, **values)
        session.add(result)
        session.commit()
        session.refresh(result)
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
