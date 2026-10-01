import json
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.database.db import Base
from src.database.models import LLMResult, Recall
from src.database.repository import save_recall
from src.llm.analyze import analyze_case
from src.llm.schemas import QuestionAnswer, QuestionnaireAnswers

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeQuestionnaireClient:
    model_name = "fake-test-model"

    def analyze(
        self,
        rappelconso_text: str,
        communication_text: str,
    ) -> QuestionnaireAnswers:
        return QuestionnaireAnswers(
            q1=QuestionAnswer(
                value=True,
                evidence=["détection d'oxyde d'éthylène"],
            ),
            q2=QuestionAnswer(
                value=True,
                evidence=["dans une des matières premières"],
            ),
            q3=QuestionAnswer(
                value=True,
                evidence=["ne plus le consommer"],
            ),
            q4=QuestionAnswer(
                value=True,
                evidence=["7071848023851"],
            ),
            q5=QuestionAnswer(
                value=True,
                evidence=["échange ou remboursement"],
            ),
            q6=QuestionAnswer(value=False, evidence=[]),
            q7=QuestionAnswer(value=False, evidence=[]),
        )


def sqlite_session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    return session_factory, engine


def test_save_recall_validates_and_upserts():
    session_factory, engine = sqlite_session_factory()

    data = json.loads(
        (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "001.json"
        ).read_text(encoding="utf-8")
    )

    first = save_recall(
        data,
        session_factory=session_factory,
    )

    data["rappelconso"]["marque"] = "Sigdal test"

    second = save_recall(
        data,
        session_factory=session_factory,
    )

    assert first.id == second.id

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(Recall)
        ) == 1

        assert session.scalar(
            select(Recall.marque)
        ) == "Sigdal test"

    engine.dispose()


def test_analyze_case_scores_and_links_result():
    session_factory, engine = sqlite_session_factory()

    data = json.loads(
        (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "001.json"
        ).read_text(encoding="utf-8")
    )

    recall = save_recall(
        data,
        session_factory=session_factory,
    )

    result = analyze_case(
        "001",
        client=FakeQuestionnaireClient(),
        session_factory=session_factory,
    )

    assert result.recall_id == recall.id
    assert result.score == 1.0
    assert result.verdict == "non_toxique"
    assert result.transparency_class == "claire"
    assert result.evidence_verified is True

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(LLMResult)
        ) == 1

    engine.dispose()