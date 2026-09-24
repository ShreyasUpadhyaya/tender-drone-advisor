from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import CatalogItem, CatalogVersion, CompatibilityRule
from app.catalog.schemas import CandidateRequirement, CatalogItemResponse, RetrievalRequest
from app.extraction.contracts import ValidatedRequirement
from app.extraction.models import ExtractionRun, RequirementRecord


def item_response(item: CatalogItem, version: CatalogVersion) -> CatalogItemResponse:
    return CatalogItemResponse(
        catalog_version=version.version,
        supplier_code=item.supplier.supplier_code if item.supplier else None,
        sku=item.sku,
        item_version=item.item_version,
        manufacturer=item.manufacturer,
        category=item.category,
        name=item.name,
        description=item.description,
        lifecycle_status=item.lifecycle_status,
        weight_grams=round(item.weight_kg * 1000),
        cost_paise=item.cost_paise,
        currency=item.currency,
        inventory_qty=item.inventory_qty,
        lead_time_days=item.lead_time_days,
        availability=item.availability,
        specs=item.specs or {},
        provenance=item.provenance or {},
        id=UUID(item.id),
        catalog_version_id=UUID(version.id),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def match(item: CatalogItem, req: CandidateRequirement) -> tuple[bool, str]:
    actual: Any = (item.specs or {}).get(req.attribute)
    if actual is None and req.attribute in {"weight", "weight_grams"}:
        actual = item.weight_kg
    if actual is None:
        return (req.semantics != "mandatory", f"missing {req.attribute}")
    try:
        if req.operator == "minimum" and actual < req.normalized_value:
            return False, f"{actual} below minimum {req.normalized_value}"
        if req.operator == "maximum" and actual > req.normalized_value:
            return False, f"{actual} above maximum {req.normalized_value}"
        if req.operator == "exact" and actual != req.normalized_value:
            return False, f"{actual} differs from {req.normalized_value}"
        if (
            req.operator == "enum"
            and str(actual).casefold() != str(req.normalized_value).casefold()
        ):
            return False, f"{actual} differs from {req.normalized_value}"
        return True, "matched"
    except (TypeError, ValueError):
        return (req.semantics != "mandatory", f"type mismatch for {req.attribute}")


def retrieve_candidates(db: Session, request: RetrievalRequest) -> dict[str, Any]:
    version = (
        db.scalar(select(CatalogVersion).where(CatalogVersion.version == request.catalog_version))
        if request.catalog_version
        else db.scalar(
            select(CatalogVersion)
            .where(CatalogVersion.status == "current")
            .order_by(CatalogVersion.created_at.desc())
        )
    )
    if version is None:
        return {
            "catalog_version": None,
            "candidates": [],
            "missing_requirements": ["catalog_version"],
        }
    stmt = select(CatalogItem).where(CatalogItem.catalog_version_id == version.id)
    if request.category:
        stmt = stmt.where(CatalogItem.category == request.category)
    if not request.include_unavailable:
        stmt = stmt.where(
            CatalogItem.availability != "unavailable", CatalogItem.lifecycle_status == "active"
        )
    context_items = (
        db.scalars(
            select(CatalogItem).where(
                CatalogItem.catalog_version_id == version.id,
                CatalogItem.sku.in_(request.context_skus),
            )
        ).all()
        if request.context_skus
        else []
    )
    context_ids = {item.id for item in context_items}
    results = []
    for item in db.scalars(stmt.order_by(CatalogItem.sku, CatalogItem.item_version)).all():
        breakdown, matched, eligible, penalties = [], 0, True, []
        for req in request.requirements:
            ok, why = match(item, req)
            evidence = ",".join(req.evidence_ids) if req.evidence_ids else "none"
            breakdown.append(
                f"{req.requirement_id or 'request'}/{evidence} {req.category}.{req.attribute}: {why}"
            )
            if ok:
                matched += 1
            elif req.semantics == "mandatory":
                eligible = False
            if req.operator == "minimum":
                actual = (item.specs or {}).get(req.attribute)
                if (
                    isinstance(actual, (int, float))
                    and isinstance(req.normalized_value, (int, float))
                    and actual > req.normalized_value * 1.5
                ):
                    penalties.append(
                        {
                            "type": "overspec",
                            "attribute": req.attribute,
                            "weight_kg": item.weight_kg,
                            "cost_paise": item.cost_paise,
                        }
                    )
        if item.availability != "in_stock":
            eligible = False
            breakdown.append(f"availability: {item.availability}; not immediately buildable")
        if context_ids:
            incompatible = db.scalars(
                select(CompatibilityRule).where(
                    CompatibilityRule.rule_type == "incompatible",
                    CompatibilityRule.to_item_id == item.id,
                    CompatibilityRule.from_item_id.in_(context_ids),
                )
            ).all()
            for rule in incompatible:
                eligible = False
                breakdown.append(f"compatibility: incompatible; {rule.reason}")
        score = matched / max(1, len(request.requirements))
        results.append(
            {
                "item": item_response(item, version),
                "eligible": eligible,
                "semantic_score": 0.0,
                "structured_match_score": score,
                "final_score": score,
                "breakdown": breakdown,
                "penalties": penalties,
            }
        )
    results.sort(
        key=lambda x: (-x["eligible"], -x["final_score"], x["item"].sku, x["item"].item_version)
    )
    return {
        "catalog_version": version.version,
        "candidates": results[: request.limit],
        "context_skus": sorted(request.context_skus),
        "missing_requirements": [],
    }


def retrieve_from_run(
    db: Session, run_id: str, category: str | None = None, limit: int = 20
) -> dict[str, Any]:
    run = db.get(ExtractionRun, run_id)
    if run is None:
        raise ValueError("extraction run not found")
    if run.schema_version != "requirements-v2":
        raise ValueError("unsupported extraction schema version")
    requirements = []
    for record in db.scalars(
        select(RequirementRecord)
        .where(RequirementRecord.run_id == run.id)
        .order_by(RequirementRecord.ordinal)
    ).all():
        req = ValidatedRequirement.model_validate(record.payload)
        requirements.append(
            CandidateRequirement(
                requirement_id=record.id,
                category=req.category,
                attribute=req.attribute,
                operator=req.operator,
                normalized_value=req.normalized_value,
                normalized_unit=req.normalized_unit,
                semantics=req.semantics,
                evidence_ids=[e.span_id for e in req.validated_evidence],
            )
        )
    result = retrieve_candidates(
        db, RetrievalRequest(requirements=requirements, category=category, limit=limit)
    )
    result["missing_critical_categories"] = sorted(
        {"platform", "range", "endurance", "payload"} - {r.category for r in requirements}
    )
    result.update(
        extraction_run_id=UUID(run.id),
        extraction_review_state=run.review_state,
        review_required=run.review_state != "not_required",
    )
    return result
