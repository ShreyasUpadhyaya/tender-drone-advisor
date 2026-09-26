from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisIssue, AnalysisRun, GeneratedConfiguration
from app.analysis.schemas import (
    AnalysisCreate,
    AnalysisIssuesResponse,
    AnalysisResponse,
    AnalysisSummary,
    BOMResponse,
    ConfigurationList,
    EvaluationResponse,
)
from app.analysis.service import create_analysis
from app.audit import record_audit
from app.catalog.api import fail
from app.db import get_db
from app.routes.documents import get_queue
from app.security import Actor, Role, require_authenticated, require_roles
from app.solver.contracts import Configuration, Issue
from app.solver.numbers import SolverError

router = APIRouter(
    prefix="/v1/analyses", tags=["analysis"], dependencies=[Depends(require_authenticated)]
)


def require_run(db, analysis_id):
    row = db.get(AnalysisRun, str(analysis_id))
    if row is None:
        raise fail("analysis_not_found", "Analysis was not found.", 404)
    return row


def response_run(row, idempotent=False):
    return AnalysisResponse(
        id=row.id,
        identity=row.identity,
        extraction_run_id=row.extraction_run_id,
        catalog_version_id=row.catalog_version_id,
        solver_version=row.solver_version,
        state=row.state,
        status=row.status,
        outcome=row.outcome,
        error_code=row.error_code,
        attempts=row.attempts,
        summary=AnalysisSummary.model_validate(row.summary),
        created_at=row.created_at,
        finished_at=row.finished_at,
        idempotent=idempotent,
    )


@router.post(
    "",
    response_model=AnalysisResponse,
    status_code=202,
    responses={200: {"model": AnalysisResponse}},
)
def start_analysis(
    payload: AnalysisCreate,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    queue: Queue = Depends(get_queue),
    actor: Actor = Depends(require_roles(Role.ADMIN, Role.REVIEWER)),
):
    try:
        run, repeated = create_analysis(db, payload)
    except SolverError as exc:
        raise fail(
            exc.code,
            exc.code.replace("_", " "),
            404 if exc.code == "analysis_input_not_found" else 409,
        ) from None
    except ValidationError:
        raise fail(
            "analysis_input_invalid", "Persisted analysis input violates its schema.", 409
        ) from None
    if repeated:
        response.status_code = 200
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run.id).with_for_update())
    if run.state == "queued" and run.queue_job_id is None:
        try:
            job = queue.enqueue(
                "app.analysis.service.execute_analysis",
                run.id,
                job_id=f"analysis-{run.id}",
                job_timeout=300,
            )
            run.queue_job_id = job.id
            record_audit(
                db,
                request,
                actor,
                action="analysis.start",
                resource_type="analysis",
                resource_id=run.id,
                trace_id=run.id,
                details={"idempotent": repeated},
            )
            db.commit()
        except Exception:  # noqa: BLE001 -- never expose queue credentials
            db.rollback()
            raise fail(
                "queue_unavailable", "Analysis is stored. Repeat the request to dispatch.", 503
            ) from None
    return response_run(run, repeated)


@router.get("/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(analysis_id: UUID, db: Session = Depends(get_db)):
    return response_run(require_run(db, analysis_id))


@router.get("/{analysis_id}/configurations", response_model=ConfigurationList)
def configurations(
    analysis_id: UUID,
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=50),
):
    row = require_run(db, analysis_id)
    limit = limit or row.summary.get("top_k", 5)
    stmt = select(GeneratedConfiguration).where(GeneratedConfiguration.analysis_id == row.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    records = db.scalars(
        stmt.order_by(GeneratedConfiguration.rank).offset(offset).limit(limit)
    ).all()
    return ConfigurationList(
        analysis_id=row.id,
        total=total,
        offset=offset,
        limit=limit,
        configurations=[Configuration.model_validate(r.payload) for r in records],
    )


def require_configuration(db, analysis_id, configuration_id):
    require_run(db, analysis_id)
    record = db.scalar(
        select(GeneratedConfiguration).where(
            GeneratedConfiguration.analysis_id == str(analysis_id),
            GeneratedConfiguration.id == configuration_id,
        )
    )
    if record is None:
        raise fail("configuration_not_found", "Configuration was not found in this analysis.", 404)
    return Configuration.model_validate(record.payload)


@router.get("/{analysis_id}/configurations/{configuration_id}", response_model=Configuration)
def configuration(analysis_id: UUID, configuration_id: str, db: Session = Depends(get_db)):
    return require_configuration(db, analysis_id, configuration_id)


@router.get("/{analysis_id}/configurations/{configuration_id}/bom", response_model=BOMResponse)
def bom(analysis_id: UUID, configuration_id: str, db: Session = Depends(get_db)):
    config = require_configuration(db, analysis_id, configuration_id)
    return BOMResponse(configuration_id=config.id, lines=config.bom, cost=config.cost)


@router.get(
    "/{analysis_id}/configurations/{configuration_id}/requirements",
    response_model=EvaluationResponse,
)
def requirements(analysis_id: UUID, configuration_id: str, db: Session = Depends(get_db)):
    config = require_configuration(db, analysis_id, configuration_id)
    return EvaluationResponse(configuration_id=config.id, evaluations=config.evaluations)


@router.get("/{analysis_id}/issues", response_model=AnalysisIssuesResponse)
def issues(analysis_id: UUID, db: Session = Depends(get_db)):
    row = require_run(db, analysis_id)
    records = db.scalars(
        select(AnalysisIssue)
        .where(AnalysisIssue.analysis_id == row.id)
        .order_by(AnalysisIssue.configuration_id, AnalysisIssue.ordinal)
    ).all()
    return AnalysisIssuesResponse(
        analysis_id=row.id,
        issues=[Issue.model_validate(r.payload) for r in records],
        rejected=AnalysisSummary.model_validate(row.summary).rejected,
    )
