import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import (
    CatalogItem,
    CatalogPrice,
    CatalogVersion,
    CompatibilityRule,
    Supplier,
)
from app.catalog.schemas import (
    CatalogImportRequest,
    CatalogImportResponse,
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogPriceResponse,
    CatalogVersionCreate,
    CatalogVersionCreateResponse,
    CompatibilityRuleResponse,
    CurrentCatalogVersionResponse,
    RetrievalRequest,
    RetrievalResponse,
    SupplierResponse,
)
from app.catalog.service import item_response, retrieve_candidates, retrieve_from_run
from app.catalog.validation import validate_item_payload
from app.db import get_db

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])


def fail(code: str, detail: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "detail": detail})


def version(db: Session, value: str) -> CatalogVersion:
    row = db.scalar(select(CatalogVersion).where(CatalogVersion.version == value))
    if row is None:
        raise fail("catalog_version_not_found", "Catalog version was not found.", 404)
    return row


@router.post("/versions", response_model=CatalogVersionCreateResponse, status_code=201)
def create_version(
    payload: CatalogVersionCreate, db: Session = Depends(get_db)
) -> CatalogVersionCreateResponse:
    existing = db.scalar(select(CatalogVersion).where(CatalogVersion.version == payload.version))
    if existing:
        return CatalogVersionCreateResponse(
            id=UUID(existing.id),
            version=existing.version,
            status=existing.status,
            source=existing.source,
            effective_from=existing.effective_from,
            effective_to=existing.effective_to,
            idempotent=True,
        )
    row = CatalogVersion(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return CatalogVersionCreateResponse(
        id=UUID(row.id),
        version=row.version,
        status=row.status,
        source=row.source,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        idempotent=False,
    )


@router.get("/versions/current", response_model=CurrentCatalogVersionResponse)
def current_version(db: Session = Depends(get_db)) -> CurrentCatalogVersionResponse:
    row = db.scalar(
        select(CatalogVersion)
        .where(CatalogVersion.status == "current")
        .order_by(CatalogVersion.created_at.desc())
    )
    if row is None:
        raise fail("catalog_empty", "No current catalog version exists.", 404)
    return CurrentCatalogVersionResponse(
        id=UUID(row.id),
        version=row.version,
        status=row.status,
        source=row.source,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
    )


@router.post("/import", response_model=CatalogImportResponse, status_code=201)
def import_catalog(payload: CatalogImportRequest, db: Session = Depends(get_db)):
    ver = db.scalar(select(CatalogVersion).where(CatalogVersion.version == payload.version.version))
    if ver is None:
        ver = CatalogVersion(**payload.version.model_dump())
        db.add(ver)
        db.flush()
    ids = []
    supplier_rows = {}
    for supplier_payload in payload.suppliers:
        supplier = db.scalar(
            select(Supplier).where(Supplier.supplier_code == supplier_payload.supplier_code)
        )
        if supplier is None:
            supplier = Supplier(
                supplier_code=supplier_payload.supplier_code,
                name=supplier_payload.name,
                provenance=supplier_payload.provenance,
            )
            db.add(supplier)
            db.flush()
        supplier_rows[supplier_payload.supplier_code] = supplier
    created_any = ver.created_at is None
    for p in payload.items:
        errors = validate_item_payload(p.model_dump())
        if errors:
            raise fail("invalid_catalog_record", json.dumps(errors, separators=(",", ":")))
        row = db.scalar(
            select(CatalogItem).where(
                CatalogItem.sku == p.sku, CatalogItem.item_version == p.item_version
            )
        )
        supplier_id = None
        if p.supplier_code:
            supplier = supplier_rows.get(p.supplier_code) or db.scalar(
                select(Supplier).where(Supplier.supplier_code == p.supplier_code)
            )
            if supplier is None:
                raise fail(
                    "invalid_supplier_reference", f"Unknown supplier code: {p.supplier_code}"
                )
            supplier_id = supplier.id
        if row is None:
            row = CatalogItem(
                catalog_version_id=ver.id,
                sku=p.sku,
                item_version=p.item_version,
                manufacturer=p.manufacturer,
                category=p.category,
                name=p.name,
                description=p.description,
                lifecycle_status=p.lifecycle_status,
                weight_kg=p.weight_grams / 1000,
                supplier_id=supplier_id,
                cost_paise=p.cost_paise,
                currency=p.currency,
                inventory_qty=p.inventory_qty,
                lead_time_days=p.lead_time_days,
                availability=p.availability,
                specs=p.specs,
                provenance=p.provenance,
            )
            db.add(row)
            db.flush()
            created_any = True
        elif row.catalog_version_id != ver.id:
            raise fail(
                "duplicate_sku_version", "SKU/version belongs to another catalog version", 409
            )
        ids.append(row.id)
    for price in payload.prices:
        item = db.scalar(
            select(CatalogItem).where(
                CatalogItem.sku == price.sku, CatalogItem.item_version == price.item_version
            )
        )
        if item is None:
            raise fail("invalid_price_reference", "Price references an unknown SKU/version")
        existing_price = db.scalar(
            select(CatalogPrice).where(
                CatalogPrice.item_id == item.id,
                CatalogPrice.effective_from == price.effective_from,
                CatalogPrice.currency == price.currency,
            )
        )
        if existing_price is None:
            db.add(
                CatalogPrice(
                    item_id=item.id,
                    amount_paise=price.amount_paise,
                    currency=price.currency,
                    effective_from=price.effective_from,
                    effective_to=price.effective_to,
                )
            )
        elif (
            existing_price.amount_paise != price.amount_paise
            or existing_price.effective_to != price.effective_to
        ):
            raise fail(
                "conflicting_price", "Effective-date price conflicts with an existing record", 409
            )
    imported = {p.sku: p for p in payload.items}
    for p in payload.items:
        specs = p.specs
        if p.category == "battery" and specs.get("cells") and specs.get("voltage_v"):
            voltage = float(specs["voltage_v"])
            cells = float(specs["cells"])
            if not 3.0 * cells <= voltage <= 4.3 * cells:
                raise fail("invalid_battery_spec", f"battery voltage/cell mismatch for {p.sku}")
        if p.category == "esc" and specs.get("max_current_a") is not None:
            for motor in imported.values():
                if (
                    motor.category == "motor"
                    and motor.specs.get("paired_esc_sku") == p.sku
                    and specs["max_current_a"] < motor.specs.get("max_current_a", 0)
                ):
                    raise fail(
                        "incompatible_electrical_limits",
                        f"ESC current is below motor limit for {p.sku}",
                    )
    for r in payload.compatibility_rules:
        source = db.scalar(
            select(CatalogItem).where(
                CatalogItem.sku == r.from_sku, CatalogItem.item_version == r.from_version
            )
        )
        target = db.scalar(
            select(CatalogItem).where(
                CatalogItem.sku == r.to_sku, CatalogItem.item_version == r.to_version
            )
        )
        if source is None or target is None:
            raise fail(
                "invalid_compatibility_reference",
                "Compatibility rule references an unknown SKU/version",
            )
        exists = db.scalar(
            select(CompatibilityRule).where(
                CompatibilityRule.from_item_id == source.id,
                CompatibilityRule.to_item_id == target.id,
                CompatibilityRule.rule_type == r.rule_type,
            )
        )
        if exists is None:
            db.add(
                CompatibilityRule(
                    from_item_id=source.id,
                    to_item_id=target.id,
                    rule_type=r.rule_type,
                    reason=r.reason,
                    constraints=r.constraints,
                    provenance=r.provenance,
                )
            )
    db.commit()
    return CatalogImportResponse(
        catalog_version=ver.version,
        catalog_version_id=UUID(ver.id),
        item_ids=[UUID(item_id) for item_id in ids],
        rule_count=len(payload.compatibility_rules),
        idempotent=not created_any,
    )


@router.post("/items", response_model=CatalogItemResponse, status_code=201)
def create_item(payload: CatalogItemCreate, db: Session = Depends(get_db)):
    ver = version(db, payload.catalog_version)
    errors = validate_item_payload(payload.model_dump())
    if errors:
        raise fail("invalid_catalog_record", json.dumps(errors, separators=(",", ":")))
    row = db.scalar(
        select(CatalogItem).where(
            CatalogItem.sku == payload.sku, CatalogItem.item_version == payload.item_version
        )
    )
    if row:
        return item_response(row, ver)
    row = CatalogItem(
        catalog_version_id=ver.id,
        sku=payload.sku,
        item_version=payload.item_version,
        manufacturer=payload.manufacturer,
        category=payload.category,
        name=payload.name,
        description=payload.description,
        lifecycle_status=payload.lifecycle_status,
        weight_kg=payload.weight_grams / 1000,
        cost_paise=payload.cost_paise,
        currency=payload.currency,
        inventory_qty=payload.inventory_qty,
        lead_time_days=payload.lead_time_days,
        availability=payload.availability,
        specs=payload.specs,
        provenance=payload.provenance,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return item_response(row, ver)


@router.get("/items", response_model=list[CatalogItemResponse])
def list_items(
    db: Session = Depends(get_db),
    catalog_version: str | None = None,
    category: str | None = None,
    manufacturer: str | None = None,
    availability: str | None = None,
    max_price_paise: int | None = Query(default=None, ge=0),
    max_weight_grams: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    ver = (
        version(db, catalog_version)
        if catalog_version
        else db.scalar(
            select(CatalogVersion)
            .where(CatalogVersion.status == "current")
            .order_by(CatalogVersion.created_at.desc())
        )
    )
    if ver is None:
        return []
    stmt = select(CatalogItem).where(CatalogItem.catalog_version_id == ver.id)
    if category:
        stmt = stmt.where(CatalogItem.category == category)
    if manufacturer:
        stmt = stmt.where(CatalogItem.manufacturer == manufacturer)
    if availability:
        stmt = stmt.where(CatalogItem.availability == availability)
    if max_price_paise is not None:
        stmt = stmt.where(CatalogItem.cost_paise <= max_price_paise)
    if max_weight_grams is not None:
        stmt = stmt.where(CatalogItem.weight_kg <= max_weight_grams / 1000)
    return [
        item_response(x, ver)
        for x in db.scalars(
            stmt.order_by(CatalogItem.sku, CatalogItem.item_version).offset(offset).limit(limit)
        ).all()
    ]


@router.get("/items/{item_id}", response_model=CatalogItemResponse)
def get_item(item_id: UUID, db: Session = Depends(get_db)):
    row = db.get(CatalogItem, str(item_id))
    if row is None:
        raise fail("not_found", "Catalog item was not found.", 404)
    return item_response(row, db.get(CatalogVersion, row.catalog_version_id))


@router.post("/retrieve", response_model=RetrievalResponse)
def retrieve(payload: RetrievalRequest, db: Session = Depends(get_db)) -> RetrievalResponse:
    return RetrievalResponse.model_validate(retrieve_candidates(db, payload))


@router.post("/retrieve-from-run/{run_id}", response_model=RetrievalResponse)
def retrieve_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    category: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> RetrievalResponse:
    try:
        return RetrievalResponse.model_validate(retrieve_from_run(db, str(run_id), category, limit))
    except ValueError as exc:
        raise fail("retrieval_rejected", str(exc), 409) from exc


@router.get("/compatibility-rules", response_model=list[CompatibilityRuleResponse])
def compatibility_rules(db: Session = Depends(get_db)) -> list[CompatibilityRuleResponse]:
    return [
        CompatibilityRuleResponse(
            id=UUID(r.id),
            from_item_id=UUID(r.from_item_id),
            to_item_id=UUID(r.to_item_id),
            rule_type=r.rule_type,
            reason=r.reason,
            constraints=r.constraints or {},
            provenance=r.provenance or {},
        )
        for r in db.scalars(select(CompatibilityRule).order_by(CompatibilityRule.id)).all()
    ]


@router.get("/suppliers", response_model=list[SupplierResponse])
def suppliers(db: Session = Depends(get_db)) -> list[SupplierResponse]:
    return [
        SupplierResponse(
            id=UUID(x.id),
            supplier_code=x.supplier_code,
            name=x.name,
            active=x.active,
            provenance=x.provenance or {},
        )
        for x in db.scalars(select(Supplier).order_by(Supplier.supplier_code)).all()
    ]


@router.get("/prices", response_model=list[CatalogPriceResponse])
def prices(
    db: Session = Depends(get_db), item_id: UUID | None = None
) -> list[CatalogPriceResponse]:
    stmt = select(CatalogPrice).order_by(CatalogPrice.item_id, CatalogPrice.effective_from)
    if item_id:
        stmt = stmt.where(CatalogPrice.item_id == str(item_id))
    return [
        CatalogPriceResponse(
            id=UUID(x.id),
            item_id=UUID(x.item_id),
            amount_paise=x.amount_paise,
            currency=x.currency,
            effective_from=x.effective_from,
            effective_to=x.effective_to,
        )
        for x in db.scalars(stmt).all()
    ]


@router.post("/items/{item_id}/deactivate", response_model=CatalogItemResponse)
def deactivate_item(item_id: UUID, db: Session = Depends(get_db)):
    row = db.get(CatalogItem, str(item_id))
    if row is None:
        raise fail("not_found", "Catalog item was not found.", 404)
    existing = db.scalar(
        select(CatalogItem).where(
            CatalogItem.sku == row.sku,
            CatalogItem.item_version == row.item_version + 1,
            CatalogItem.lifecycle_status == "inactive",
        )
    )
    if existing is not None:
        return item_response(existing, db.get(CatalogVersion, existing.catalog_version_id))
    replacement = CatalogItem(
        catalog_version_id=row.catalog_version_id,
        supplier_id=row.supplier_id,
        sku=row.sku,
        item_version=row.item_version + 1,
        manufacturer=row.manufacturer,
        category=row.category,
        name=row.name,
        description=row.description,
        lifecycle_status="inactive",
        weight_kg=row.weight_kg,
        cost_paise=row.cost_paise,
        currency=row.currency,
        inventory_qty=row.inventory_qty,
        lead_time_days=row.lead_time_days,
        availability=row.availability,
        specs=row.specs,
        provenance=row.provenance,
    )
    db.add(replacement)
    db.commit()
    db.refresh(replacement)
    return item_response(replacement, db.get(CatalogVersion, replacement.catalog_version_id))
