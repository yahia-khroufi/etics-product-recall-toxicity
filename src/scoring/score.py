"""Règle déterministe de score et de verdict à partir de Q1 à Q7."""

from dataclasses import dataclass

from src.llm.schemas import QuestionnaireAnswers


@dataclass(frozen=True)
class ScoreResult:
    score: float
    severity: int
    transparency_class: str
    verdict: str
    explanation: str


def score_case(answers: QuestionnaireAnswers) -> ScoreResult:
    question_names = ("q1", "q2", "q3", "q4", "q5", "q6", "q7")
    q = {name: getattr(answers, name).value for name in question_names}
    completeness = sum(q[name] for name in ("q1", "q2", "q3", "q4", "q5")) / 5

    if q["q7"]:
        return ScoreResult(
            completeness, 3, "minimisation_potentielle", "toxique",
            "La communication paraît atténuer la gravité indiquée dans la fiche officielle.",
        )
    missing = [name.upper() for name in ("q1", "q3", "q4") if not q[name]]
    if missing:
        return ScoreResult(
            completeness, 2, "incomplete", "toxique",
            f"Information essentielle absente selon {', '.join(missing)}.",
        )
    if q["q6"]:
        return ScoreResult(
            completeness, 1, "ambigue", "toxique",
            "La communication contient une formulation vague ou euphémisante.",
        )
    return ScoreResult(
        completeness, 0, "claire", "non_toxique",
        "Le danger, l'action et l'identification sont présents sans minimisation détectée.",
    )
