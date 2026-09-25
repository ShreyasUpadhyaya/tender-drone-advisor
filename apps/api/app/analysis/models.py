from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    extraction_run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    catalog_version_id: Mapped[str] = mapped_column(ForeignKey("catalog_versions.id"), index=True)
    solver_version: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    status: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[str | None] = mapped_column(String(32))
    queue_job_id: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(40), default="analysis-input-v1")
    payload: Mapped[dict] = mapped_column(JSON)


class GeneratedConfiguration(Base):
    __tablename__ = "analysis_configurations"
    __table_args__ = (UniqueConstraint("analysis_id", "rank", name="uq_analysis_rank"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    platform_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"))
    rank: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)


class AnalysisBOM(Base):
    __tablename__ = "analysis_bom"
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_configurations.id"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)
    price_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_prices.id"))
    payload: Mapped[dict] = mapped_column(JSON)


class RequirementEvaluation(Base):
    __tablename__ = "analysis_evaluations"
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_configurations.id"), primary_key=True
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("extracted_requirements.id"), primary_key=True
    )
    result: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)


class AnalysisCost(Base):
    __tablename__ = "analysis_costs"
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_configurations.id"), primary_key=True
    )
    policy_version: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)


class AnalysisIssue(Base):
    __tablename__ = "analysis_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    configuration_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_configurations.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
