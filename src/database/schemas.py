from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TextValue = str | list[str] | None


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RappelConsoData(StrictSchema):
    produit: str | None = None
    marque: str | None = None
    entreprise: str | None = None
    numero_fiche: str | None = None
    date_publication: str | None = None
    gtin: list[str] = Field(default_factory=list)
    lots: list[str] = Field(default_factory=list)
    motif_rappel: str | None = None
    risques_encourus: str | None = None
    preconisations_sanitaires: TextValue = None
    conduites_a_tenir: list[str] = Field(default_factory=list)
    distributeurs: TextValue = None
    zone_geographique: TextValue = None


class CommunicationEntrepriseData(StrictSchema):
    produit: TextValue = None
    marque: TextValue = None
    gtin: list[str] = Field(default_factory=list)
    lots: list[str] = Field(default_factory=list)
    motif: TextValue = None
    risque: TextValue = None
    recommandations: TextValue = None


class RecallData(StrictSchema):
    case_id: str = Field(min_length=1, max_length=100)
    organization_key: str | None = Field(default=None, max_length=200)
    rappelconso: RappelConsoData
    communication_entreprise: CommunicationEntrepriseData

    @field_validator("case_id")
    @classmethod
    def case_id_sans_espaces(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("case_id ne peut pas être vide")
        return value


class LLMResultCreate(StrictSchema):
    case_id: str = Field(min_length=1, max_length=100)
    q1: bool
    q2: bool
    q3: bool
    q4: bool
    q5: bool
    q6: bool
    q7: bool
    evidence: dict[str, list[str]]
    evidence_verified: bool
    run_id: int | None = None
    official_version_id: int | None = None
    company_version_id: int | None = None
    # Existing callers may still supply the legacy derived fields.
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    severity: int | None = Field(default=None, ge=0, le=3)
    transparency_class: Literal["claire", "ambigue", "incomplete", "minimisation_potentielle"] | None = None
    verdict: Literal["toxique", "non_toxique"] | None = None
    explanation: str | None = None
    model_name: str
    prompt_version: str = "questionnaire_v3"
