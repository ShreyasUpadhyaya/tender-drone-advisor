"""Scenario snapshots deliberately reuse C05 rather than implementing a second solver."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.analysis.api import response_run
from app.analysis.models import AnalysisRun
from app.analysis.schemas import AnalysisCreate
from app.analysis.service import create_analysis
from app.catalog.models import CatalogItem
from app.extraction.contracts import RequirementCandidate, ValidatedRequirement, ValidationIssue
from app.extraction.models import EvidenceLink, ExtractionRun, RequirementRecord, ReviewIssue
from app.extraction.validation import normalize
from app.inventory.models import InventoryRecord, InventoryVersion
from app.scenarios.models import ScenarioAssumption, ScenarioVersion, ScenarioWorkspace
from app.scenarios.schemas import AssumptionInput, ScenarioCreate, ScenarioResponse
from app.security import Actor
from app.solver.contracts import Weights


def _identity(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _candidate(item: AssumptionInput) -> RequirementCandidate:
    return RequirementCandidate(
        category=item.category,
        attribute=item.attribute,
        semantics="mandatory",
        operator=item.operator,
        original_value=item.value,
        original_unit=item.unit or "unknown",
        confidence=1.0,
        evidence=[],
    )


def _policy(payload: ScenarioCreate):
    policy = payload.policy.model_copy(deep=True)
    if payload.intent == "cost_optimized":
        policy.weights = Weights(
            cost=10, weight=1, overspec=1, lead_time=2, inventory_risk=2, coverage=5
        )
    elif payload.intent == "performance_oriented":
        policy.weights = Weights(
            cost=1, weight=2, overspec=2, lead_time=2, inventory_risk=2, coverage=10
        )
    return policy


def _serialize(db, version: ScenarioVersion) -> ScenarioResponse:
    assumptions = db.scalars(
        select(ScenarioAssumption)
        .where(ScenarioAssumption.scenario_version_id == version.id)
        .order_by(ScenarioAssumption.category, ScenarioAssumption.attribute)
    ).all()
    source = db.get(ExtractionRun, version.source_extraction_run_id)
    analysis = db.get(AnalysisRun, version.analysis_id)
    inventory = [db.get(InventoryVersion, value) for value in version.inventory_version_ids]
    inventory = [value for value in inventory if value is not None]
    records = {value.record_id: db.get(InventoryRecord, value.record_id) for value in inventory}
    return ScenarioResponse(
        id=version.id,
        scenario_id=version.scenario_id,
        version_number=version.version_number,
        intent=version.intent,
        rationale=version.rationale,
        reviewer=version.reviewer,
        component_preferences=version.component_preferences,
        inventory_version_ids=version.inventory_version_ids,
        inventory_overlays=[
            {
                "sku": records[row.record_id].sku,
                "name": records[row.record_id].name,
                "on_hand_quantity": row.on_hand_quantity,
                "expected_quantity": row.expected_quantity,
                "expected_on": row.expected_on,
                "recorded_by_subject": row.recorded_by_subject,
                "solver_eligible": records[row.record_id].catalog_item_id is not None,
            }
            for row in inventory
        ],
        source_extraction_run_id=version.source_extraction_run_id,
        scenario_extraction_run_id=version.scenario_extraction_run_id,
        analysis=response_run(analysis, False),
        tender_requirements_verified=bool(
            source
            and source.state == "completed"
            and source.review_state == "not_required"
            and analysis.state == "completed"
            and analysis.status != "needs_review"
        ),
        engineering_catalog_feasibility=analysis.status or "queued",
        assumptions_outstanding=bool(assumptions)
        or bool(source and source.review_state != "not_required")
        or analysis.status == "needs_review",
        assumptions=[
            {
                "id": row.id,
                "category": row.category,
                "attribute": row.attribute,
                "operator": row.operator,
                "original_value": row.original_value,
                "original_unit": row.original_unit,
                "normalized_value": row.normalized_value,
                "normalized_unit": row.normalized_unit,
                "rationale": row.rationale,
                "provenance": row.provenance,
            }
            for row in assumptions
        ],
    )


def create_scenario(db, payload: ScenarioCreate, actor: Actor | None = None):
    source = db.get(ExtractionRun, str(payload.extraction_run_id))
    if source is None or source.state not in ("completed", "needs_review"):
        raise ValueError("source_extraction_not_terminal")
    normalized = []
    for item in payload.assumptions:
        candidate = _candidate(item)
        value, unit = normalize(candidate)
        if value == "unknown":
            raise ValueError("scenario_assumption_must_be_specific")
        normalized.append((item, candidate, value, unit))
    inventory_versions: list[InventoryVersion] = []
    for record_id in sorted({str(value) for value in payload.inventory_record_ids}):
        record = db.get(InventoryRecord, record_id)
        if (
            record is None
            or record.catalog_item_id is None
            or (actor is not None and record.workspace_id != actor.workspace_id)
        ):
            raise ValueError("scenario_inventory_not_solver_eligible")
        catalog_item = db.get(CatalogItem, record.catalog_item_id)
        if catalog_item is None or catalog_item.catalog_version_id != str(
            payload.catalog_version_id
        ):
            raise ValueError("scenario_inventory_not_in_catalog_snapshot")
        latest = db.scalar(
            select(InventoryVersion)
            .where(InventoryVersion.record_id == record.id)
            .order_by(InventoryVersion.version_number.desc())
        )
        if latest is None:
            raise ValueError("scenario_inventory_version_not_found")
        inventory_versions.append(latest)
    key_payload = {
        "source": source.id,
        "catalog": str(payload.catalog_version_id),
        "intent": payload.intent,
        "policy": _policy(payload).model_dump(mode="json"),
        "component_skus": sorted(set(payload.component_skus)),
        "inventory_version_ids": sorted(value.id for value in inventory_versions),
        "assumptions": [
            {
                "candidate": candidate.model_dump(mode="json"),
                "value": value,
                "unit": unit,
                "rationale": item.rationale,
                "provenance": item.provenance,
            }
            for item, candidate, value, unit in normalized
        ],
    }
    identity = _identity(key_payload)
    existing = db.scalar(select(ScenarioVersion).where(ScenarioVersion.identity == identity))
    if existing:
        return existing, True

    workspace = db.scalar(
        select(ScenarioWorkspace).where(
            ScenarioWorkspace.source_extraction_run_id == source.id,
            ScenarioWorkspace.catalog_version_id == str(payload.catalog_version_id),
            ScenarioWorkspace.name == payload.name,
        )
    )
    if workspace is None:
        workspace = ScenarioWorkspace(
            source_extraction_run_id=source.id,
            catalog_version_id=str(payload.catalog_version_id),
            name=payload.name,
        )
        db.add(workspace)
        db.flush()
    selected_items = []
    if payload.component_skus:
        selected_items = db.scalars(
            select(CatalogItem).where(
                CatalogItem.catalog_version_id == str(payload.catalog_version_id),
                CatalogItem.sku.in_(payload.component_skus),
            )
        ).all()
        if len({item.sku for item in selected_items}) != len(set(payload.component_skus)):
            raise ValueError("scenario_component_not_in_catalog_snapshot")
    number = (
        db.scalar(
            select(func.count())
            .select_from(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == workspace.id)
        )
        + 1
    )
    scenario_run = ExtractionRun(
        document_version_id=source.document_version_id,
        idempotency_key=_identity({"scenario": identity, "kind": "scenario-input"}),
        schema_version=source.schema_version,
        prompt_version=source.prompt_version,
        model_config={"provider": "reviewer-scenario"},
        state="needs_review"
        if source.review_state != "not_required" or normalized
        else "completed",
        review_state="pending"
        if source.review_state != "not_required" or normalized
        else "not_required",
        finished_at=datetime.now(UTC),
    )
    db.add(scenario_run)
    db.flush()
    version = ScenarioVersion(
        scenario_id=workspace.id,
        version_number=number,
        identity=identity,
        intent=payload.intent,
        rationale=payload.rationale,
        reviewer=payload.reviewer,
        component_preferences=sorted({item.sku for item in selected_items}),
        inventory_version_ids=sorted(value.id for value in inventory_versions),
        source_extraction_run_id=source.id,
        scenario_extraction_run_id=scenario_run.id,
    )
    db.add(version)
    db.flush()
    scenario_run.model_config = {"provider": "reviewer-scenario", "scenario_version_id": version.id}
    # Copy cited tender facts exactly. Their evidence links are copied unchanged;
    # assumptions are separate records with no tender citation and an explicit provenance.
    source_rows = db.scalars(
        select(RequirementRecord)
        .where(RequirementRecord.run_id == source.id)
        .order_by(RequirementRecord.ordinal)
    ).all()
    ordinal = 0
    for row in source_rows:
        copied = RequirementRecord(run_id=scenario_run.id, ordinal=ordinal, payload=row.payload)
        ordinal += 1
        db.add(copied)
        db.flush()
        for link in db.scalars(select(EvidenceLink).where(EvidenceLink.requirement_id == row.id)):
            db.add(EvidenceLink(requirement_id=copied.id, span_id=link.span_id, anchor=link.anchor))
    for item, candidate, value, unit in normalized:
        assumed = ValidatedRequirement(
            **candidate.model_dump(),
            normalized_value=value,
            normalized_unit=unit,
            validated_evidence=[],
        )
        record = RequirementRecord(
            run_id=scenario_run.id, ordinal=ordinal, payload=assumed.model_dump(mode="json")
        )
        ordinal += 1
        db.add(record)
        db.flush()
        db.add(
            ScenarioAssumption(
                scenario_version_id=version.id,
                requirement_id=record.id,
                category=item.category,
                attribute=item.attribute,
                operator=item.operator,
                original_value=item.value,
                original_unit=item.unit,
                normalized_value=value,
                normalized_unit=unit,
                rationale=item.rationale,
                provenance=item.provenance,
            )
        )
    # The source review items remain unresolved facts. A scenario cannot clear them.
    for old_issue in db.scalars(select(ReviewIssue).where(ReviewIssue.run_id == source.id)):
        db.add(
            ReviewIssue(run_id=scenario_run.id, payload=old_issue.payload, state=old_issue.state)
        )
    if normalized:
        db.add(
            ReviewIssue(
                run_id=scenario_run.id,
                payload=ValidationIssue(
                    code="scenario_assumptions_outstanding",
                    detail="Internal scenario assumptions support an estimate only; tender compliance remains unresolved.",
                ).model_dump(),
                state="open",
            )
        )
    db.flush()
    analysis, repeated = create_analysis(
        db,
        AnalysisCreate(
            extraction_run_id=scenario_run.id,
            catalog_version_id=payload.catalog_version_id,
            analysis_date=payload.analysis_date,
            policy=_policy(payload),
        ),
    )
    version.analysis_id = analysis.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ScenarioVersion).where(ScenarioVersion.identity == identity))
        if existing is None:
            raise
        return existing, True
    return version, repeated


def get_scenario(db, scenario_version_id: str) -> ScenarioVersion | None:
    return db.get(ScenarioVersion, scenario_version_id)
