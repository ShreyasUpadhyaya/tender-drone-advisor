from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisRun
from app.audit import record_audit
from app.catalog.api import fail
from app.db import get_db
from app.routes.documents import get_queue
from app.scenarios.schemas import ScenarioCreate, ScenarioResponse
from app.scenarios.service import _serialize, create_scenario, get_scenario
from app.security import Actor, Role, require_authenticated, require_roles

router = APIRouter(
    prefix="/v1/scenarios", tags=["scenarios"], dependencies=[Depends(require_authenticated)]
)


@router.post(
    "",
    response_model=ScenarioResponse,
    status_code=202,
    responses={200: {"model": ScenarioResponse}},
)
def start_scenario(
    payload: ScenarioCreate,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    queue: Queue = Depends(get_queue),
    actor: Actor = Depends(require_roles(Role.ADMIN, Role.REVIEWER)),
):
    payload = payload.model_copy(update={"reviewer": actor.subject})
    try:
        version, repeated = create_scenario(db, payload, actor)
    except ValueError as exc:
        raise fail(
            str(exc), "The scenario inputs need a completed tender extraction and valid units.", 409
        ) from None
    if repeated:
        response.status_code = 200
    analysis = db.scalar(
        select(AnalysisRun).where(AnalysisRun.id == version.analysis_id).with_for_update()
    )
    if analysis.state == "queued" and analysis.queue_job_id is None:
        try:
            job = queue.enqueue(
                "app.analysis.service.execute_analysis",
                analysis.id,
                job_id=f"analysis-{analysis.id}",
                job_timeout=300,
            )
            analysis.queue_job_id = job.id
            record_audit(
                db,
                request,
                actor,
                action="scenario.start",
                resource_type="scenario_version",
                resource_id=version.id,
                trace_id=analysis.id,
                details={"idempotent": repeated},
            )
            db.commit()
        except Exception:  # noqa: BLE001 -- queue errors must not expose connection details
            db.rollback()
            raise fail(
                "queue_unavailable",
                "Scenario saved. Retry this request to dispatch the deterministic analysis.",
                503,
            ) from None
    return _serialize(db, version)


@router.get("/{scenario_version_id}", response_model=ScenarioResponse)
def scenario(scenario_version_id: UUID, db: Session = Depends(get_db)):
    row = get_scenario(db, str(scenario_version_id))
    if row is None:
        raise fail("scenario_not_found", "Scenario version was not found.", 404)
    return _serialize(db, row)
