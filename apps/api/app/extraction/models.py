import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    schema_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(40))
    model_config: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default="queued")
    review_state: Mapped[str] = mapped_column(String(24), default="not_required")
    error_code: Mapped[str | None] = mapped_column(String(80))
    queue_job_id: Mapped[str | None] = mapped_column(String(64))
    # A retry is a new immutable run.  This link makes the recovery lineage
    # inspectable without ever overwriting a failed model attempt.
    retry_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequirementRecord(Base):
    __tablename__ = "extracted_requirements"
    __table_args__ = (UniqueConstraint("run_id", "ordinal", name="uq_requirement_ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    # Only schema-valid, normalized, citation-validated requirements enter this table.
    payload: Mapped[dict] = mapped_column(JSON)


class EvidenceLink(Base):
    __tablename__ = "requirement_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("extracted_requirements.id"))
    span_id: Mapped[str] = mapped_column(ForeignKey("source_spans.id"))
    anchor: Mapped[dict] = mapped_column(JSON)


class ReviewIssue(Base):
    __tablename__ = "extraction_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default="open")
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecisionEvent(Base):
    """Append-only human review record; never changes C03 extracted evidence."""

    __tablename__ = "extraction_review_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    issue_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_issues.id"), nullable=True, index=True
    )
    requirement_id: Mapped[str | None] = mapped_column(
        ForeignKey("extracted_requirements.id"), nullable=True, index=True
    )
    source_span_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_spans.id"), nullable=True, index=True
    )
    supersedes_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_review_events.id"), nullable=True, unique=True, index=True
    )
    action: Mapped[str] = mapped_column(String(40))
    reviewer: Mapped[str] = mapped_column(String(80))
    rationale: Mapped[str] = mapped_column(String(1000))
    before_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionNodeRun(Base):
    __tablename__ = "extraction_node_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"))
    node: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    branch: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
