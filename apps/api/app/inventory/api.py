from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.catalog.api import fail
from app.db import get_db
from app.inventory.models import InventoryRecord
from app.inventory.schemas import (
    InventoryRecordCreate,
    InventoryRecordResponse,
    InventoryVersionCreate,
    SessionResponse,
)
from app.inventory.service import create_record, create_version, serialize
from app.security import Actor, Role, require_authenticated, require_roles

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])


@router.get("/session", response_model=SessionResponse)
def session(actor: Actor = Depends(require_authenticated)) -> SessionResponse:
    return SessionResponse(
        subject=actor.subject,
        workspace_id=actor.workspace_id,
        roles=sorted(role.value for role in actor.roles),
        demo=actor.demo,
    )


@router.get("/records", response_model=list[InventoryRecordResponse])
def records(
    db: Session = Depends(get_db), actor: Actor = Depends(require_authenticated)
) -> list[InventoryRecordResponse]:
    rows = db.scalars(
        select(InventoryRecord)
        .where(InventoryRecord.workspace_id == actor.workspace_id)
        .order_by(InventoryRecord.category, InventoryRecord.sku)
    ).all()
    return [serialize(db, row) for row in rows]


@router.post("/records", response_model=InventoryRecordResponse, status_code=201)
def add_record(
    payload: InventoryRecordCreate,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_roles(Role.ADMIN)),
) -> InventoryRecordResponse:
    try:
        row, repeated = create_record(db, payload, actor)
    except ValueError as exc:
        raise fail(
            str(exc), "Stock must reference valid catalog/tender data and explicit units.", 409
        ) from None
    if repeated:
        response.status_code = 200
    record_audit(
        db,
        request,
        actor,
        action="inventory.create",
        resource_type="inventory_record",
        resource_id=row.id,
        details={"idempotent": repeated, "source_status": row.source_status},
    )
    db.commit()
    return serialize(db, row)


@router.post(
    "/records/{record_id}/versions", response_model=InventoryRecordResponse, status_code=201
)
def revise_record(
    record_id: UUID,
    payload: InventoryVersionCreate,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_roles(Role.ADMIN)),
) -> InventoryRecordResponse:
    row = db.get(InventoryRecord, str(record_id))
    if row is None or row.workspace_id != actor.workspace_id:
        raise fail("inventory_record_not_found", "Stock record was not found.", 404)
    try:
        row, repeated = create_version(db, row, payload, actor)
    except PermissionError:
        raise fail(
            "inventory_owner_required",
            "Only the administrator who recorded this stock item may override its count or availability.",
            403,
        ) from None
    except ValueError as exc:
        raise fail(str(exc), "Stock update references invalid tender data.", 409) from None
    if repeated:
        response.status_code = 200
    record_audit(
        db,
        request,
        actor,
        action="inventory.revise",
        resource_type="inventory_record",
        resource_id=row.id,
        details={"idempotent": repeated},
    )
    db.commit()
    return serialize(db, row)
