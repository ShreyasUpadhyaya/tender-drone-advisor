import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.catalog.models import CatalogItem
from app.extraction.models import ExtractionRun, RequirementRecord
from app.inventory.models import InventoryRecord, InventoryVersion
from app.inventory.schemas import (
    InventoryRecordCreate,
    InventoryRecordResponse,
    InventoryVersionCreate,
)
from app.security import Actor


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _payload(payload: InventoryRecordCreate | InventoryVersionCreate, actor: Actor) -> dict:
    return {
        "on_hand_quantity": payload.on_hand_quantity,
        "expected_quantity": payload.expected_quantity,
        "expected_on": payload.expected_on,
        "location": payload.location.strip(),
        "rationale": payload.rationale.strip(),
        "extraction_run_id": str(payload.extraction_run_id) if payload.extraction_run_id else None,
        "requirement_ids": sorted(str(value) for value in payload.requirement_ids),
        "requirement_categories": sorted(
            {value.strip() for value in payload.requirement_categories}
        ),
        "recorded_by_subject": actor.subject,
    }


def _validate_links(db, values: dict) -> None:
    run_id = values["extraction_run_id"]
    if run_id and db.get(ExtractionRun, run_id) is None:
        raise ValueError("extraction_run_not_found")
    ids = values["requirement_ids"]
    if ids:
        rows = db.scalars(select(RequirementRecord).where(RequirementRecord.id.in_(ids))).all()
        if len(rows) != len(set(ids)) or any(run_id and row.run_id != run_id for row in rows):
            raise ValueError("requirement_link_not_found")


def _version_response(row: InventoryVersion) -> dict:
    return {
        "id": row.id,
        "version_number": row.version_number,
        "on_hand_quantity": row.on_hand_quantity,
        "expected_quantity": row.expected_quantity,
        "expected_on": row.expected_on,
        "location": row.location,
        "rationale": row.rationale,
        "extraction_run_id": row.extraction_run_id,
        "requirement_ids": row.requirement_ids,
        "requirement_categories": row.requirement_categories,
        "recorded_by_subject": row.recorded_by_subject,
        "created_at": row.created_at,
    }


def serialize(db, record: InventoryRecord) -> InventoryRecordResponse:
    versions = db.scalars(
        select(InventoryVersion)
        .where(InventoryVersion.record_id == record.id)
        .order_by(InventoryVersion.version_number.desc())
    ).all()
    return InventoryRecordResponse(
        id=record.id,
        catalog_item_id=record.catalog_item_id,
        sku=record.sku,
        name=record.name,
        category=record.category,
        manufacturer=record.manufacturer,
        source_status=record.source_status,
        solver_eligible=record.catalog_item_id is not None,
        owner_subject=record.owner_subject,
        created_at=record.created_at,
        current=_version_response(versions[0]),
        history=[_version_response(value) for value in versions],
    )


def create_record(db, payload: InventoryRecordCreate, actor: Actor):
    values = _payload(payload, actor)
    _validate_links(db, values)
    if payload.catalog_item_id:
        item = db.get(CatalogItem, str(payload.catalog_item_id))
        if item is None:
            raise ValueError("catalog_item_not_found")
        sku, name, category, manufacturer = item.sku, item.name, item.category, item.manufacturer
        source_status = "catalog_linked"
        catalog_item_id = item.id
    else:
        sku = payload.sku.strip()
        name = payload.name.strip()
        category = str(payload.category)
        manufacturer = payload.manufacturer.strip()
        source_status = "pending_catalog_validation"
        catalog_item_id = None
    existing = db.scalar(
        select(InventoryRecord).where(
            InventoryRecord.workspace_id == actor.workspace_id, InventoryRecord.sku == sku
        )
    )
    identity = _hash({"workspace": actor.workspace_id, "sku": sku, **values})
    if existing:
        version = db.scalar(select(InventoryVersion).where(InventoryVersion.identity == identity))
        if version:
            return existing, True
        raise ValueError("inventory_record_exists_use_new_version")
    record = InventoryRecord(
        workspace_id=actor.workspace_id,
        catalog_item_id=catalog_item_id,
        sku=sku,
        name=name,
        category=category,
        manufacturer=manufacturer,
        source_status=source_status,
        owner_subject=actor.subject,
    )
    db.add(record)
    db.flush()
    db.add(InventoryVersion(record_id=record.id, version_number=1, identity=identity, **values))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(InventoryRecord).where(
                InventoryRecord.workspace_id == actor.workspace_id, InventoryRecord.sku == sku
            )
        )
        if existing is None:
            raise
        version = db.scalar(select(InventoryVersion).where(InventoryVersion.identity == identity))
        if version is None:
            raise ValueError("inventory_record_exists_use_new_version")
        return existing, True
    db.refresh(record)
    return record, False


def create_version(db, record: InventoryRecord, payload: InventoryVersionCreate, actor: Actor):
    if record.workspace_id != actor.workspace_id:
        raise ValueError("inventory_record_not_found")
    if record.owner_subject != actor.subject:
        raise PermissionError("inventory_owner_required")
    values = _payload(payload, actor)
    _validate_links(db, values)
    identity = _hash({"record": record.id, **values})
    existing = db.scalar(select(InventoryVersion).where(InventoryVersion.identity == identity))
    if existing:
        return record, True
    number = (
        int(
            db.scalar(
                select(func.max(InventoryVersion.version_number)).where(
                    InventoryVersion.record_id == record.id
                )
            )
            or 0
        )
        + 1
    )
    db.add(
        InventoryVersion(record_id=record.id, version_number=number, identity=identity, **values)
    )
    db.commit()
    return record, False
