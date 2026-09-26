"""Minimal production-readiness health and operations API."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.observability import metrics
from app.recovery import mark_stale_jobs
from app.security import Actor, Role, require_authenticated, require_roles
from app.settings import Settings, get_settings
from app.storage import get_storage

router = APIRouter(
    prefix="/v1/ops", tags=["operations"], dependencies=[Depends(require_authenticated)]
)


class DependencyCheck(BaseModel):
    name: str
    status: Literal["ok", "unavailable", "not_configured"]
    detail: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: list[DependencyCheck]


class RecoveryResponse(BaseModel):
    stale_marked: dict[str, int]


def readiness(db: Session, settings: Settings) -> ReadinessResponse:
    checks: list[DependencyCheck] = []
    try:
        db.execute(text("SELECT 1"))
        checks.append(DependencyCheck(name="postgresql", status="ok", detail="database reachable"))
    except Exception:  # noqa: BLE001 -- readiness must safely summarize any driver failure
        checks.append(
            DependencyCheck(name="postgresql", status="unavailable", detail="database unavailable")
        )
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
        checks.append(DependencyCheck(name="redis", status="ok", detail="queue reachable"))
    except Exception:  # noqa: BLE001 -- readiness must safely summarize any driver failure
        checks.append(
            DependencyCheck(name="redis", status="unavailable", detail="queue unavailable")
        )
    try:
        get_storage().check_ready()
        checks.append(
            DependencyCheck(name="object_storage", status="ok", detail="bucket reachable")
        )
    except Exception:  # noqa: BLE001 -- readiness must safely summarize any storage failure
        checks.append(
            DependencyCheck(
                name="object_storage", status="unavailable", detail="object storage unavailable"
            )
        )
    provider_ok = settings.llm_provider == "fake" or bool(settings.llm_api_key.get_secret_value())
    checks.append(
        DependencyCheck(
            name="model_provider",
            status="ok" if provider_ok else "not_configured",
            detail="fake demo provider"
            if settings.llm_provider == "fake"
            else "credential not configured",
        )
    )
    return ReadinessResponse(
        status="ready" if all(check.status == "ok" for check in checks) else "degraded",
        dependencies=checks,
    )


@router.get("/readiness", response_model=ReadinessResponse)
def get_readiness(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ReadinessResponse:
    return readiness(db, settings)


@router.get("/status", response_model=ReadinessResponse)
def status_page(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ReadinessResponse:
    """Safe demo operations summary; detailed traces remain in their own APIs."""
    return readiness(db, settings)


@router.get("/metrics")
def get_metrics(actor: Actor = Depends(require_roles(Role.ADMIN))) -> dict[str, object]:
    del actor
    return metrics.snapshot()


@router.post("/recovery/stale", response_model=RecoveryResponse)
def recover_stale(
    actor: Actor = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecoveryResponse:
    del actor
    return RecoveryResponse(stale_marked=mark_stale_jobs(db, settings.job_stale_seconds))


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}
