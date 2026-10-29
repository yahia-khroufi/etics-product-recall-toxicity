
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    organization_key: Mapped[str | None] = mapped_column(String(200))

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
    __table_args__ = (
        CheckConstraint(
            "toxicity_score >= 0 AND toxicity_score <= 100",
            name="ck_llm_results_toxicity_score_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recall_id: Mapped[int] = mapped_column(
        ForeignKey("recalls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"), unique=True)
    official_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_versions.id"))
    company_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_versions.id"))
    scoring_version: Mapped[str | None] = mapped_column(String(100))
    toxicity_score_origin: Mapped[str | None] = mapped_column(String(100))
    analysis_status: Mapped[str] = mapped_column(String(40), default="pending", server_default="legacy_unlinked")
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    q1: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q2: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q3: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q4: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q5: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q6: Mapped[bool] = mapped_column(Boolean, nullable=False)
    q7: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[dict[str, list[str]]] = mapped_column(JSON_TYPE, nullable=False)
    evidence_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # NULL means that the answers are saved but scoring has not completed yet.
    toxicity_score: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[int | None] = mapped_column(Integer)
    transparency_class: Mapped[str | None] = mapped_column(String(50))
    verdict: Mapped[str | None] = mapped_column(String(20))
    explanation: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    recall: Mapped[Recall] = relationship(back_populates="llm_results")


class RecallSnapshot(Base):
    """Observed descriptive data, never a reconstructed historical state."""
    __tablename__ = "recall_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    recall_id: Mapped[int] = mapped_column(ForeignKey("recalls.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON_TYPE)
    content_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("recall_id", "role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    recall_id: Mapped[int] = mapped_column(ForeignKey("recalls.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    text_sha256: Mapped[str] = mapped_column(String(64))
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[dict] = mapped_column(JSON_TYPE)
    raw_text: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    recall_snapshot_id: Mapped[int] = mapped_column(ForeignKey("recall_snapshots.id"))
    model_name: Mapped[str] = mapped_column(String(200))
    model_revision: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(100))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    scoring_version: Mapped[str] = mapped_column(String(100))
    parameters: Mapped[dict] = mapped_column(JSON_TYPE)
    git_commit: Mapped[str | None] = mapped_column(String(40))
    git_dirty: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(40), default="running")
    error_type: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[str] = mapped_column(String(100), unique=True)
    source_path: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("llm_results.id"))
    error_type: Mapped[str | None] = mapped_column(String(100))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineWorker(Base):
    __tablename__ = "pipeline_workers"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40))
    current_case_id: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
