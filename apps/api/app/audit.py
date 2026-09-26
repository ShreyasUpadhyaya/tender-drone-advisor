"""Append-only, redacted audit writes for mutating operations."""

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.security import Actor


def record_audit(
    db: Session,
    request: Request,
    actor: Actor,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: str = "success",
    trace_id: str | None = None,
    details: dict[str, str | int | bool] | None = None,
) -> None:
    """Only safe IDs/counts/codes are allowed in details; never source content."""
    db.add(
        AuditEvent(
            workspace_id=actor.workspace_id,
            actor_subject=actor.subject,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=getattr(request.state, "request_id", None),
            trace_id=trace_id,
            outcome=outcome,
            details=details or {},
        )
    )
