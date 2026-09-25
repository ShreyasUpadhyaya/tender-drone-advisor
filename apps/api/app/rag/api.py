from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.catalog.api import fail
from app.db import get_db
from app.extraction.provider import ProviderFailure
from app.rag.contracts import (
    AuditList,
    AuditResponse,
    CitationsResponse,
    GroundedCreate,
    GroundedReport,
    IndexCreate,
    IssuesResponse,
    JobResponse,
    ResumeRequest,
    SearchCreate,
    SearchResponse,
)
from app.rag.indexing import create_index, create_job
from app.rag.models import NodeAudit, RagJob
from app.rag.providers import configuration, public_versions
from app.rag.retrieval import search
from app.routes.documents import get_queue
from app.settings import get_settings

router = APIRouter(prefix="/v1/rag", tags=["rag"])


def require_job(db, job_id, kind=None):
    row = db.get(RagJob, str(job_id))
    if not row or row.namespace != get_settings().rag_namespace or (kind and row.job_type != kind):
        raise fail("rag_job_not_found", "RAG job was not found.", 404)
    return row


def result(row, repeated=False):
    return JobResponse(
        id=row.id,
        identity=row.identity,
        analysis_id=row.analysis_id,
        state=row.state,
        error_code=row.error_code,
        attempts=row.attempts,
        count=row.result.get("count", 0),
        versions=public_versions(row.config),
        idempotent=repeated,
    )


def dispatch(db, row, queue):
    row = db.scalar(
        select(RagJob)
        .where(RagJob.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row.state == "queued" and not row.queue_job_id:
        try:
            path = (
                "app.rag.indexing.execute_index"
                if row.job_type == "index"
                else "app.rag.workflow.execute_graph"
            )
            job = queue.enqueue(
                path,
                row.id,
                job_id=f"rag-{row.id}-{'resume' if row.decision else 'start'}",
                job_timeout=600,
            )
            row.queue_job_id = job.id
            db.commit()
        except Exception:  # noqa: BLE001 -- redact queue connection details
            db.rollback()
            raise fail(
                "queue_unavailable", "Job stored. Repeat the same request to dispatch.", 503
            ) from None
    return row


def provider_error(exc):
    return fail(
        exc.code, exc.code.replace("_", " "), 404 if exc.code.endswith("not_found") else 409
    )


@router.post(
    "/indexes", response_model=JobResponse, status_code=202, responses={200: {"model": JobResponse}}
)
def index_create(
    payload: IndexCreate,
    response: Response,
    db: Session = Depends(get_db),
    queue=Depends(get_queue),
):
    try:
        row, repeated = create_index(db, payload)
        row = dispatch(db, row, queue)
    except ProviderFailure as exc:
        raise provider_error(exc) from None
    response.status_code = 200 if repeated else 202
    return result(row, repeated)


@router.get("/indexes/{index_id}", response_model=JobResponse)
def index_status(index_id: UUID, db: Session = Depends(get_db)):
    return result(require_job(db, index_id, "index"))


@router.post("/search", response_model=SearchResponse)
def hybrid_search(payload: SearchCreate, db: Session = Depends(get_db)):
    try:
        return search(db, payload)
    except ProviderFailure as exc:
        raise provider_error(exc) from None
    except SQLAlchemyError:
        db.rollback()
        raise fail(
            "retrieval_storage_failure", "Retrieval storage operation failed safely.", 503
        ) from None


@router.post(
    "/runs", response_model=JobResponse, status_code=202, responses={200: {"model": JobResponse}}
)
def graph_create(
    payload: GroundedCreate,
    response: Response,
    db: Session = Depends(get_db),
    queue=Depends(get_queue),
):
    try:
        row, repeated = create_job(db, "graph", payload, configuration(get_settings()))
        row = dispatch(db, row, queue)
    except ProviderFailure as exc:
        raise provider_error(exc) from None
    response.status_code = 200 if repeated else 202
    return result(row, repeated)


@router.get("/runs/{run_id}", response_model=JobResponse)
def graph_status(run_id: UUID, db: Session = Depends(get_db)):
    return result(require_job(db, run_id, "graph"))


@router.post("/runs/{run_id}/resume", response_model=JobResponse)
def graph_resume(
    run_id: UUID, payload: ResumeRequest, db: Session = Depends(get_db), queue=Depends(get_queue)
):
    require_job(db, run_id, "graph")
    row = db.scalar(
        select(RagJob)
        .where(RagJob.id == str(run_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    decision = payload.model_dump()
    if row.decision:
        if row.decision != decision:
            raise fail(
                "review_decision_immutable", "A different decision is already recorded.", 409
            )
        return result(dispatch(db, row, queue), True)
    if row.state != "awaiting_review":
        raise fail("run_not_interrupted", "Run is not waiting for human review.", 409)
    row.decision = decision
    row.state = "queued"
    row.queue_job_id = None
    db.commit()
    return result(dispatch(db, row, queue))


def require_report(db, run_id):
    row = require_job(db, run_id, "graph")
    if "schema_version" not in row.result:
        raise fail("report_not_ready", "No grounded report is available yet.", 409)
    return GroundedReport.model_validate(row.result)


@router.get("/runs/{run_id}/report", response_model=GroundedReport)
def report(run_id: UUID, db: Session = Depends(get_db)):
    return require_report(db, run_id)


@router.get("/runs/{run_id}/citations", response_model=CitationsResponse)
def citations(run_id: UUID, db: Session = Depends(get_db)):
    report = require_report(db, run_id)
    values = {
        c.model_dump_json(): c
        for section in report.sections
        for f in section.facts
        for c in [f.citation]
    }
    return CitationsResponse(citations=list(values.values()))


@router.get("/runs/{run_id}/issues", response_model=IssuesResponse)
def issues(run_id: UUID, db: Session = Depends(get_db)):
    row = require_job(db, run_id, "graph")
    if "schema_version" in row.result:
        report = GroundedReport.model_validate(row.result)
        return IssuesResponse(
            review_blockers=report.review_blockers,
            clarification_questions=report.clarification_questions,
        )
    nodes = db.scalars(select(NodeAudit).where(NodeAudit.run_id == row.id)).all()
    codes = sorted({c for n in nodes for c in n.summary.get("issue_codes", [])})
    return IssuesResponse(
        review_blockers=codes,
        clarification_questions=[f"Resolve {c.replace('_', ' ')}." for c in codes],
    )


@router.get("/runs/{run_id}/nodes", response_model=AuditList)
def nodes(run_id: UUID, db: Session = Depends(get_db)):
    row = require_job(db, run_id, "graph")
    records = db.scalars(
        select(NodeAudit)
        .where(NodeAudit.run_id == row.id)
        .order_by(NodeAudit.started_at, NodeAudit.id)
    ).all()
    return AuditList(
        run_id=row.id,
        nodes=[
            AuditResponse(
                node=n.node,
                attempt=n.attempt,
                status=n.status,
                started_at=n.started_at,
                finished_at=n.finished_at,
                elapsed_ms=n.elapsed_ms,
                branch=n.branch,
                error_code=n.error_code,
                summary=n.summary,
            )
            for n in records
        ],
    )
