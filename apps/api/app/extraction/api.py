import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from rq import Queue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.extraction.contracts import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ReviewDecision,
    ValidatedRequirement,
    ValidationIssue,
)
from app.extraction.models import ExtractionNodeRun, ExtractionRun, RequirementRecord, ReviewIssue
from app.extraction.provider import model_snapshot
from app.models import DocumentVersion, IngestionJob, ProcessingState
from app.routes.documents import get_queue
from app.settings import get_settings

router = APIRouter(prefix="/v1", tags=["extraction"])


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


def error(code, detail, status=404):
    return HTTPException(status_code=status, detail={"error": code, "detail": detail})


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
    db: Session = Depends(get_db),
    queue: Queue = Depends(get_queue),
):
    version = db.get(DocumentVersion, str(version_id))
    if version is None:
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


@router.post("/extraction-runs/{run_id}/issues/{issue_id}/decision", response_model=IssueResponse)
def review_issue(
    run_id: UUID, issue_id: UUID, decision: ReviewDecision, db: Session = Depends(get_db)
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
        db.commit()
    return issue_response(record)
