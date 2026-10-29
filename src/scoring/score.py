from dataclasses import dataclass

from src.llm.schemas import QuestionnaireAnswers

# Preserve the rule present in the user's working tree, including Q2 and Q5.
SCORING_VERSION = "etics-seven-criteria-v1"
TOXICITY_METRIC_VERSION = "seven-equal-weights-v1"


@dataclass(frozen=True)
class ScoreResult:
    score: float
    severity: int
    transparency_class: str
    verdict: str
    explanation: str
    toxicity_score: float


def score_case(answers: QuestionnaireAnswers) -> ScoreResult:
    question_names = ("q1", "q2", "q3", "q4", "q5", "q6", "q7")
    q = {name: getattr(answers, name).value for name in question_names}
    completeness = sum(q[name] for name in ("q1", "q2", "q3", "q4", "q5")) / 5
    problems = sum(not q[name] for name in ("q1", "q2", "q3", "q4", "q5"))
    problems += int(q["q6"]) + int(q["q7"])
    toxicity_score = round(problems * 100 / 7, 2)

    if q["q7"]:
        return ScoreResult(
            completeness, 3, "minimisation_potentielle", "toxique",
            "La communication paraît atténuer la gravité indiquée dans la fiche officielle.",
            toxicity_score,
        )
    missing = [name.upper() for name in ("q1", "q2", "q3", "q4", "q5") if not q[name]]
    if missing:
        return ScoreResult(
            completeness, 2, "incomplete", "toxique",
            f"Information essentielle absente selon {', '.join(missing)}.",
            toxicity_score,
        )
    if q["q6"]:
        return ScoreResult(
            completeness, 1, "ambigue", "toxique",
            "La communication contient une formulation vague ou euphémisante.",
            toxicity_score,
        )
    return ScoreResult(
        completeness, 0, "claire", "non_toxique",
        "Le danger, l'action et l'identification sont présents sans minimisation détectée.",
        toxicity_score,
    )
