
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.db import Base

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class Recall(Base):
    __tablename__ = "recalls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )

    produit: Mapped[str | None] = mapped_column(Text)
    marque: Mapped[str | None] = mapped_column(Text)
    entreprise: Mapped[str | None] = mapped_column(Text)
    numero_fiche: Mapped[str | None] = mapped_column(String(100))
    date_publication: Mapped[str | None] = mapped_column(String(50))
    gtin: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    lots: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    motif_rappel: Mapped[str | None] = mapped_column(Text)
    risques_encourus: Mapped[str | None] = mapped_column(Text)
    preconisations_sanitaires: Mapped[Any | None] = mapped_column(JSON_TYPE)
    conduites_a_tenir: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    distributeurs: Mapped[Any | None] = mapped_column(JSON_TYPE)
    zone_geographique: Mapped[Any | None] = mapped_column(JSON_TYPE)

    communication_produit: Mapped[Any | None] = mapped_column(JSON_TYPE)
    communication_marque: Mapped[Any | None] = mapped_column(JSON_TYPE)
    communication_gtin: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    communication_lots: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    communication_motif: Mapped[Any | None] = mapped_column(JSON_TYPE)
    communication_risque: Mapped[Any | None] = mapped_column(JSON_TYPE)
    communication_recommandations: Mapped[Any | None] = mapped_column(JSON_TYPE)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    llm_results: Mapped[list["LLMResult"]] = relationship(
        back_populates="recall", cascade="all, delete-orphan"
    )


class LLMResult(Base):
    __tablename__ = "llm_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recall_id: Mapped[int] = mapped_column(
        ForeignKey("recalls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    q1: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q2: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q3: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q4: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q5: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q6: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q7: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[dict[str, list[str]]] = mapped_column(JSON_TYPE, nullable=False)
    evidence_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    transparency_class: Mapped[str] = mapped_column(String(50), nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    recall: Mapped[Recall] = relationship(back_populates="llm_results")
