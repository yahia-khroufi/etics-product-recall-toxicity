
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.db import SessionLocal
from src.database.models import LLMResult, Recall
from src.database.schemas import LLMResultCreate, RecallData

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
        recall = session.scalar(select(Recall).where(Recall.case_id == validated.case_id))
        if recall is None:
            recall = Recall(**values)
            session.add(recall)
        else:
            for field, value in values.items():
                setattr(recall, field, value)
        session.commit()
        session.refresh(recall)
        return recall
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
