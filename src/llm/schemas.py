from pydantic import BaseModel, ConfigDict, Field


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool
    evidence: list[str] = Field(
        default_factory=list,
        description="Citations exactes des documents, vides si l'information est absente.",
    )


class QuestionnaireAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q1: QuestionAnswer
    q2: QuestionAnswer
    q3: QuestionAnswer
    q4: QuestionAnswer
    q5: QuestionAnswer
    q6: QuestionAnswer
    q7: QuestionAnswer


