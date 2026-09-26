import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.db import get_db
from app.extraction.contracts import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ReviewDecision,
    ValidatedRequirement,
    ValidationIssue,
)
from app.extraction.models import (
    EvidenceLink,
    ExtractionNodeRun,
    ExtractionRun,
    RequirementRecord,
    ReviewDecisionEvent,
    ReviewIssue,
)
from app.extraction.provider import model_snapshot
from app.models import DocumentVersion, IngestionJob, ProcessingState, SourceSpan
from app.routes.documents import get_queue
from app.security import Actor, Role, require_authenticated, require_roles
from app.settings import get_settings

router = APIRouter(prefix="/v1", tags=["extraction"], dependencies=[Depends(require_authenticated)])


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    node: str
    attempt: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    elapsed_ms: int | None
    branch: str | None
    error_code: str | None
    summary: dict


class RunResponse(BaseModel):
    trace_id: str
    document_version_id: str
    schema_version: str
    prompt_version: str
    model_configuration: dict
    state: str
    review_state: str
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
    nodes: list[NodeResponse] = Field(default_factory=list)


class RequirementResponse(BaseModel):
    id: str
    requirement: ValidatedRequirement


class IssueResponse(BaseModel):
    id: str
    issue: ValidationIssue
    state: str
    decision: ReviewDecision | None
    decided_at: datetime | None


class ReviewEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal[
        "accept_verified_extraction",
        "correct_transcription_or_normalization",
        "reject_unsupported_extraction",
        "mark_unresolved",
        "record_documented_assumption",
    ]
    # Kept as an optional compatibility field, but never trusted. The persisted
    # reviewer is always derived from the authenticated actor.
    reviewer: str | None = Field(default=None, min_length=1, max_length=80)
    rationale: str = Field(min_length=4, max_length=1000)
    issue_id: UUID | None = Field(default=None, strict=False)
    requirement_id: UUID | None = Field(default=None, strict=False)
    source_span_id: UUID | None = Field(default=None, strict=False)
    supersedes_event_id: UUID | None = Field(default=None, strict=False)
    before_value: dict | None = None
    after_value: dict | None = None


class ReviewEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    action: str
    reviewer: str
    rationale: str
    issue_id: str | None
    requirement_id: str | None
    source_span_id: str | None
    supersedes_event_id: str | None
    is_current: bool
    before_value: dict | None
    after_value: dict | None
    created_at: datetime


class ReviewCandidateResponse(BaseModel):
    ordinal: int | None
    state: Literal["rejected", "unresolved"]
    title: str
    reason: str
    source: dict | None = None


class ReviewWorkspaceResponse(BaseModel):
    run: RunResponse
    accepted_requirements: list[RequirementResponse]
    rejected_candidates: list[ReviewCandidateResponse]
    issues: list[IssueResponse]
    events: list[ReviewEventResponse]
    counts: dict[str, int]
    failed_stage: str | None
    next_action: str


def error(code, detail, status=404):
    return HTTPException(status_code=status, detail={"error": code, "detail": detail})


def review_event_response(
    event: ReviewDecisionEvent, superseded_ids: set[str]
) -> ReviewEventResponse:
    return ReviewEventResponse(
        id=event.id,
        action=event.action,
        reviewer=event.reviewer,
        rationale=event.rationale,
        issue_id=event.issue_id,
        requirement_id=event.requirement_id,
        source_span_id=event.source_span_id,
        supersedes_event_id=event.supersedes_event_id,
        is_current=event.id not in superseded_ids,
        before_value=event.before_value,
        after_value=event.after_value,
        created_at=event.created_at,
    )


def require_run(db, run_id):
    run = db.get(ExtractionRun, str(run_id))
    if run is None:
        raise error("not_found", "Extraction run was not found.")
    return run


def serialize_run(db, run):
    nodes = db.scalars(
        select(ExtractionNodeRun)
        .where(ExtractionNodeRun.run_id == run.id)
        .order_by(ExtractionNodeRun.started_at)
    ).all()
    return RunResponse(
        trace_id=run.id,
        document_version_id=run.document_version_id,
        schema_version=run.schema_version,
        prompt_version=run.prompt_version,
        model_configuration=run.model_config,
        state=run.state,
        review_state=run.review_state,
        error_code=run.error_code,
        created_at=run.created_at,
        finished_at=run.finished_at,
        nodes=[NodeResponse.model_validate(node) for node in nodes],
    )


@router.post(
    "/document-versions/{version_id}/extractions", response_model=RunResponse, status_code=202
)
def start_extraction(
    version_id: UUID,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    queue: Queue = Depends(get_queue),
    actor: Actor = Depends(require_roles(Role.ADMIN, Role.REVIEWER)),
):
    version = db.get(DocumentVersion, str(version_id))
    if version is None or (not actor.demo and version.document.workspace_id != actor.workspace_id):
        raise error("not_found", "Document version was not found.")
    job = db.scalar(
        select(IngestionJob)
        .where(IngestionJob.document_version_id == version.id)
        .order_by(IngestionJob.created_at.desc())
    )
    if job is None or job.state != ProcessingState.COMPLETED:
        raise error("ingestion_incomplete", "Complete document ingestion before extraction.", 409)
    snapshot = model_snapshot(get_settings())
    identity = json.dumps([version.id, SCHEMA_VERSION, PROMPT_VERSION, snapshot], sort_keys=True)
    key = hashlib.sha256(identity.encode()).hexdigest()
    run = db.scalar(select(ExtractionRun).where(ExtractionRun.idempotency_key == key))
    if run is None:
        run = ExtractionRun(
            document_version_id=version.id,
            idempotency_key=key,
            schema_version=SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config=snapshot,
        )
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            run = db.scalar(select(ExtractionRun).where(ExtractionRun.idempotency_key == key))
    else:
        response.status_code = 200
    # Lock dispatch to avoid concurrent duplicate queue jobs. Repeated workers also claim atomically.
    run = db.scalar(select(ExtractionRun).where(ExtractionRun.id == run.id).with_for_update())
    if run.state == "queued" and run.queue_job_id is None:
        try:
            queued = queue.enqueue(
                "app.tasks.extract_requirements_task",
                run.id,
                job_id=f"extraction-{run.id}",
                job_timeout=14400,
            )
            run.queue_job_id = queued.id
            run.error_code = None
            record_audit(
                db,
                request,
                actor,
                action="extraction.start",
                resource_type="extraction_run",
                resource_id=run.id,
                trace_id=run.id,
                details={"idempotent": False},
            )
            db.commit()
        except Exception:  # noqa: BLE001 -- boundary must not leak queue/DB credentials
            db.rollback()
            raise error(
                "queue_unavailable", "Extraction is stored. Retry this request to dispatch it.", 503
            ) from None
    return serialize_run(db, run)


@router.get("/extraction-runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    return serialize_run(db, require_run(db, run_id))


@router.get("/extraction-runs/{run_id}/requirements", response_model=list[RequirementResponse])
def get_requirements(run_id: UUID, db: Session = Depends(get_db)):
    run = require_run(db, run_id)
    records = db.scalars(
        select(RequirementRecord)
        .where(RequirementRecord.run_id == run.id)
        .order_by(RequirementRecord.ordinal)
    ).all()
    return [
        RequirementResponse(
            id=record.id, requirement=ValidatedRequirement.model_validate(record.payload)
        )
        for record in records
    ]


def issue_response(record):
    return IssueResponse(
        id=record.id,
        issue=ValidationIssue.model_validate(record.payload),
        state=record.state,
        decision=record.decision,
        decided_at=record.decided_at,
    )


@router.get("/extraction-runs/{run_id}/issues", response_model=list[IssueResponse])
def get_issues(run_id: UUID, db: Session = Depends(get_db)):
    run = require_run(db, run_id)
    records = db.scalars(
        select(ReviewIssue).where(ReviewIssue.run_id == run.id).order_by(ReviewIssue.id)
    ).all()
    return [issue_response(record) for record in records]


def _source_for_requirement(db: Session, requirement_id: str) -> dict | None:
    link = db.scalar(select(EvidenceLink).where(EvidenceLink.requirement_id == requirement_id))
    if link is None:
        return None
    span = db.get(SourceSpan, link.span_id)
    if span is None:
        return None
    return {
        "span_id": span.id,
        "page_number": span.page_number,
        "section_name": span.section_name,
        "quote": link.anchor.get("quote"),
    }


@router.get("/extraction-runs/{run_id}/review-workspace", response_model=ReviewWorkspaceResponse)
def review_workspace(run_id: UUID, db: Session = Depends(get_db)):
    run = require_run(db, run_id)
    rows = db.scalars(
        select(RequirementRecord)
        .where(RequirementRecord.run_id == run.id)
        .order_by(RequirementRecord.ordinal)
    ).all()
    requirements = [
        RequirementResponse(id=row.id, requirement=ValidatedRequirement.model_validate(row.payload))
        for row in rows
    ]
    by_ordinal = {row.ordinal: row for row in rows}
    issues = db.scalars(
        select(ReviewIssue).where(ReviewIssue.run_id == run.id).order_by(ReviewIssue.id)
    ).all()
    candidates: list[ReviewCandidateResponse] = []
    for record in issues:
        finding = ValidationIssue.model_validate(record.payload)
        row = (
            by_ordinal.get(finding.requirement_index)
            if finding.requirement_index is not None
            else None
        )
        # A validation issue references a candidate only when it has a stable
        # persisted requirement.  Missing tender clauses intentionally have no
        # invented source excerpt or candidate text.
        if row is not None:
            candidate = ValidatedRequirement.model_validate(row.payload)
            candidates.append(
                ReviewCandidateResponse(
                    ordinal=row.ordinal,
                    state="unresolved",
                    title=candidate.attribute.replace("_", " ").capitalize(),
                    reason=finding.detail,
                    source=_source_for_requirement(db, row.id),
                )
            )
    events = db.scalars(
        select(ReviewDecisionEvent)
        .where(ReviewDecisionEvent.run_id == run.id)
        .order_by(ReviewDecisionEvent.created_at, ReviewDecisionEvent.id)
    ).all()
    superseded_ids = {event.supersedes_event_id for event in events if event.supersedes_event_id}
    failed = next(
        (node.node for node in serialize_run(db, run).nodes if node.status == "failed"), None
    )
    if run.state == "failed":
        next_action = "Retry extraction safely; the failed run will remain in the audit trail."
    elif run.state in ("queued", "processing"):
        next_action = (
            "Extraction is still running. Refresh this workspace when the current stage finishes."
        )
    elif not requirements:
        next_action = "Inspect source spans and rejection reasons, then record an internal scenario assumption to explore an estimate without claiming tender compliance."
    else:
        next_action = "Review cited requirements and record any evidence-backed decision before relying on a configuration."
    return ReviewWorkspaceResponse(
        run=serialize_run(db, run),
        accepted_requirements=requirements,
        rejected_candidates=candidates,
        issues=[issue_response(row) for row in issues],
        events=[review_event_response(event, superseded_ids) for event in events],
        counts={
            "accepted": len(requirements),
            "rejected_or_unresolved": len(candidates),
            "issues": len(issues),
            "source_spans": db.scalar(
                select(func.count())
                .select_from(SourceSpan)
                .where(SourceSpan.document_version_id == run.document_version_id)
            )
            or 0,
        },
        failed_stage=failed,
        next_action=next_action,
    )


@router.post(
    "/extraction-runs/{run_id}/review-decisions",
    response_model=ReviewEventResponse,
    status_code=201,
)
def record_review_event(
    run_id: UUID,
    payload: ReviewEventRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_roles(Role.ADMIN, Role.REVIEWER)),
):
    run = require_run(db, run_id)
    if payload.issue_id and not db.scalar(
        select(ReviewIssue).where(
            ReviewIssue.id == str(payload.issue_id), ReviewIssue.run_id == run.id
        )
    ):
        raise error("issue_not_found", "Review issue was not found for this extraction run.")
    if payload.requirement_id and not db.scalar(
        select(RequirementRecord).where(
            RequirementRecord.id == str(payload.requirement_id), RequirementRecord.run_id == run.id
        )
    ):
        raise error("requirement_not_found", "Requirement was not found for this extraction run.")
    source = db.get(SourceSpan, str(payload.source_span_id)) if payload.source_span_id else None
    if source and source.document_version_id != run.document_version_id:
        raise error("source_not_found", "Source evidence does not belong to this tender version.")
    if (
        payload.action in {"accept_verified_extraction", "correct_transcription_or_normalization"}
        and source is None
    ):
        raise error(
            "source_evidence_required",
            "A source span is required for this evidence-backed decision.",
            422,
        )
    if payload.action == "correct_transcription_or_normalization" and payload.after_value is None:
        raise error(
            "corrected_value_required",
            "A corrected value and unit must be recorded without changing the original extraction.",
            422,
        )
    previous = (
        db.get(ReviewDecisionEvent, str(payload.supersedes_event_id))
        if payload.supersedes_event_id
        else None
    )
    if payload.supersedes_event_id and (previous is None or previous.run_id != run.id):
        raise error("review_event_not_found", "The review decision to revise was not found.", 404)
    if previous and db.scalar(
        select(ReviewDecisionEvent).where(ReviewDecisionEvent.supersedes_event_id == previous.id)
    ):
        raise error("review_event_already_revised", "Revise the current decision instead.", 409)
    event = ReviewDecisionEvent(
        run_id=run.id,
        issue_id=str(payload.issue_id) if payload.issue_id else None,
        requirement_id=str(payload.requirement_id) if payload.requirement_id else None,
        source_span_id=str(payload.source_span_id) if payload.source_span_id else None,
        action=payload.action,
        supersedes_event_id=previous.id if previous else None,
        reviewer=actor.subject,
        rationale=payload.rationale,
        before_value=payload.before_value,
        after_value=payload.after_value,
    )
    db.add(event)
    db.flush()
    record_audit(
        db,
        request,
        actor,
        action="extraction.review_event",
        resource_type="review_event",
        resource_id=event.id,
        trace_id=run.id,
        details={"action": payload.action},
    )
    db.commit()
    db.refresh(event)
    return review_event_response(event, set())


@router.post("/extraction-runs/{run_id}/retry", response_model=RunResponse, status_code=202)
def retry_extraction(
    run_id: UUID,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    queue: Queue = Depends(get_queue),
    actor: Actor = Depends(require_roles(Role.ADMIN, Role.REVIEWER)),
):
    previous = require_run(db, run_id)
    if previous.state != "failed":
        raise error(
            "retry_not_available",
            "Only a failed extraction can be retried as a new audit-linked run.",
            409,
        )
    snapshot = model_snapshot(get_settings())
    key = hashlib.sha256(
        json.dumps([previous.id, "retry", snapshot], sort_keys=True).encode()
    ).hexdigest()
    run = db.scalar(select(ExtractionRun).where(ExtractionRun.idempotency_key == key))
    if run is None:
        run = ExtractionRun(
            document_version_id=previous.document_version_id,
            idempotency_key=key,
            schema_version=SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config=snapshot,
            retry_of_id=previous.id,
        )
        db.add(run)
        db.commit()
    else:
        response.status_code = 200
    if run.state == "queued" and run.queue_job_id is None:
        try:
            queued = queue.enqueue(
                "app.tasks.extract_requirements_task",
                run.id,
                job_id=f"extraction-{run.id}",
                job_timeout=14400,
            )
            run.queue_job_id = queued.id
            record_audit(
                db,
                request,
                actor,
                action="extraction.retry",
                resource_type="extraction_run",
                resource_id=run.id,
                trace_id=run.id,
                details={"retry_of": previous.id},
            )
            db.commit()
        except Exception:  # noqa: BLE001 -- queue errors must not expose connection details
            db.rollback()
            raise error(
                "queue_unavailable", "The retry is stored. Repeat this request to dispatch it.", 503
            ) from None
    return serialize_run(db, run)


@router.post("/extraction-runs/{run_id}/issues/{issue_id}/decision", response_model=IssueResponse)
def review_issue(
    run_id: UUID,
    issue_id: UUID,
    decision: ReviewDecision,
    request: Request,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_roles(Role.ADMIN, Role.REVIEWER)),
):
    run = require_run(db, run_id)
    record = db.scalar(
        select(ReviewIssue)
        .where(ReviewIssue.id == str(issue_id), ReviewIssue.run_id == run.id)
        .with_for_update()
    )
    if record is None:
        raise error("not_found", "Review issue was not found.")
    payload = decision.model_dump()
    if record.decision is not None and record.decision != payload:
        raise error("decision_already_recorded", "This review decision is immutable.", 409)
    if record.decision is None:
        record.decision, record.decided_at, record.state = payload, datetime.now(UTC), "recorded"
        # Acknowledgment is not a validation waiver or engineering approval.
        run.review_state = "decision_recorded"
        record_audit(
            db,
            request,
            actor,
            action="extraction.review_decision",
            resource_type="review_issue",
            resource_id=record.id,
            trace_id=run.id,
        )
        db.commit()
    return issue_response(record)
