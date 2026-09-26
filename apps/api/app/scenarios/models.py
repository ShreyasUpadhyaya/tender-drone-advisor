import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class ScenarioWorkspace(Base):
    __tablename__ = "scenario_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), index=True
    )
    catalog_version_id: Mapped[str] = mapped_column(ForeignKey("catalog_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (UniqueConstraint("identity", name="uq_scenario_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenario_workspaces.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    intent: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(String(1000))
    reviewer: Mapped[str] = mapped_column(String(80))
    component_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    inventory_version_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), index=True
    )
    scenario_extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), unique=True
    )
    analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScenarioAssumption(Base):
    __tablename__ = "scenario_assumptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_version_id: Mapped[str] = mapped_column(ForeignKey("scenario_versions.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("extracted_requirements.id"), unique=True
    )
    category: Mapped[str] = mapped_column(String(40))
    attribute: Mapped[str] = mapped_column(String(80))
    operator: Mapped[str] = mapped_column(String(24))
    original_value: Mapped[object] = mapped_column(JSON)
    original_unit: Mapped[str] = mapped_column(String(32))
    normalized_value: Mapped[object] = mapped_column(JSON)
    normalized_unit: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(String(1000))
    provenance: Mapped[str] = mapped_column(String(80), default="internal_assumption")
