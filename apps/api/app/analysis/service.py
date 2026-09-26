import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.analysis.models import (
    AnalysisBOM,
    AnalysisCost,
    AnalysisIssue,
    AnalysisRun,
    AnalysisSnapshot,
    GeneratedConfiguration,
    RequirementEvaluation,
)
from app.catalog.models import CatalogItem, CatalogPrice, CatalogVersion, CompatibilityRule
from app.db import SessionLocal
from app.extraction.contracts import ValidatedRequirement
from app.extraction.models import EvidenceLink, ExtractionRun, RequirementRecord, ReviewIssue
from app.inventory.models import InventoryRecord, InventoryVersion
from app.models import DocumentVersion, SourceSpan
from app.observability import metrics, stage_timer
from app.scenarios.models import ScenarioAssumption, ScenarioVersion
from app.solver.contracts import SOLVER_VERSION, Item, Price, Requirement, Rule, Snapshot
from app.solver.explanations import issue
from app.solver.numbers import SolverError, scaled
from app.solver.orchestration import fingerprint, solve

LOGGER = logging.getLogger(__name__)


def capture(db, request):
    # One MVCC snapshot prevents price/availability drift during capture on PostgreSQL.
    if db.bind.dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    run = db.get(ExtractionRun, str(request.extraction_run_id))
    catalog = db.get(CatalogVersion, str(request.catalog_version_id))
    if not run or not catalog:
        raise SolverError("analysis_input_not_found")
    if run.schema_version != "requirements-v2":
        raise SolverError("unsupported_extraction_schema")
    if run.state not in ("completed", "needs_review"):
        raise SolverError("extraction_not_terminal")
    version = db.get(DocumentVersion, run.document_version_id)
    requirements = []
    scenario_assumption_requirement_ids = set()
    if run.model_config.get("provider") == "reviewer-scenario":
        scenario_assumption_requirement_ids = set(
            db.scalars(
                select(ScenarioAssumption.requirement_id).where(
                    ScenarioAssumption.scenario_version_id
                    == run.model_config.get("scenario_version_id")
                )
            ).all()
        )
    scenario = (
        db.get(ScenarioVersion, run.model_config.get("scenario_version_id"))
        if run.model_config.get("provider") == "reviewer-scenario"
        else None
    )
    preferred_by_category: dict[str, set[str]] = {}
    inventory_by_item: dict[str, InventoryVersion] = {}
    if scenario:
        for item in db.scalars(
            select(CatalogItem).where(
                CatalogItem.catalog_version_id == catalog.id,
                CatalogItem.sku.in_(scenario.component_preferences),
            )
        ):
            preferred_by_category.setdefault(item.category, set()).add(item.sku)
        for version_id in scenario.inventory_version_ids:
            inventory = db.get(InventoryVersion, version_id)
            record = db.get(InventoryRecord, inventory.record_id) if inventory else None
            if inventory and record and record.catalog_item_id:
                inventory_by_item[record.catalog_item_id] = inventory
    for row in db.scalars(
        select(RequirementRecord)
        .where(RequirementRecord.run_id == run.id)
        .order_by(RequirementRecord.ordinal)
    ):
        req = ValidatedRequirement.model_validate(row.payload)
        links = db.scalars(select(EvidenceLink).where(EvidenceLink.requirement_id == row.id)).all()
        is_internal_assumption = row.id in scenario_assumption_requirement_ids
        if (not is_internal_assumption and not req.validated_evidence) or len(links) != len(
            req.validated_evidence
        ):
            raise SolverError("requirement_evidence_invalid")
        for anchor in req.validated_evidence:
            span = db.get(SourceSpan, anchor.span_id)
            if (
                not span
                or not version
                or span.document_version_id != version.id
                or anchor.document_version_id != version.id
                or anchor.document_id != version.document_id
                or anchor.page_number != span.page_number
                or anchor.section_name != span.section_name
                or anchor.chunk_index != span.chunk_index
                or not 0 <= anchor.quote_start < anchor.quote_end <= len(span.text)
                or span.text[anchor.quote_start : anchor.quote_end] != anchor.quote
                or not any(l.span_id == span.id and l.anchor == anchor.model_dump() for l in links)
            ):
                raise SolverError("requirement_evidence_invalid")
        requirements.append(Requirement(id=row.id, extraction_run_id=run.id, requirement=req))
    source_issues = [
        issue("extraction_issue_" + r.payload.get("code", "unresolved"), "review")
        for r in db.scalars(
            select(ReviewIssue).where(ReviewIssue.run_id == run.id).order_by(ReviewIssue.id)
        )
        if r.payload.get("blocking", True)
    ]
    items = []
    rows = db.scalars(
        select(CatalogItem)
        .where(CatalogItem.catalog_version_id == catalog.id)
        .order_by(CatalogItem.sku, CatalogItem.item_version)
    ).all()
    for item in rows:
        if (
            item.category in preferred_by_category
            and item.sku not in preferred_by_category[item.category]
        ):
            continue
        prices = db.scalars(
            select(CatalogPrice)
            .where(CatalogPrice.item_id == item.id)
            .order_by(CatalogPrice.effective_from, CatalogPrice.id)
        ).all()
        manual = inventory_by_item.get(item.id)
        inventory_qty = item.inventory_qty
        availability = item.availability
        lead_time_days = item.lead_time_days
        provenance = dict(item.provenance)
        if manual:
            arrived = (
                manual.expected_quantity
                if manual.expected_on and manual.expected_on <= request.analysis_date
                else 0
            )
            inventory_qty = manual.on_hand_quantity + arrived
            if inventory_qty > 0:
                availability = "in_stock"
                lead_time_days = 0
            elif manual.expected_quantity and manual.expected_on:
                availability = "backorder"
                lead_time_days = max(0, (manual.expected_on - request.analysis_date).days)
            else:
                availability = "unavailable"
                lead_time_days = 0
            provenance["manual_inventory"] = {
                "version_id": manual.id,
                "version_number": manual.version_number,
                "recorded_by": manual.recorded_by_subject,
                "location": manual.location,
            }
        items.append(
            Item(
                id=item.id,
                catalog_version_id=item.catalog_version_id,
                sku=item.sku,
                item_version=item.item_version,
                category=item.category,
                name=item.name,
                manufacturer=item.manufacturer,
                weight_g=scaled(item.weight_kg, 1000),
                lifecycle_status=item.lifecycle_status,
                availability=availability,
                inventory_qty=inventory_qty,
                lead_time_days=lead_time_days,
                supplier_id=item.supplier_id,
                supplier_active=item.supplier.active if item.supplier else None,
                specs=item.specs,
                provenance=provenance,
                prices=[
                    Price(
                        id=p.id,
                        amount_paise=p.amount_paise,
                        currency=p.currency,
                        effective_from=p.effective_from,
                        effective_to=p.effective_to,
                    )
                    for p in prices
                ],
            )
        )
    ids = [x.id for x in items]
    rules = [
        Rule(
            id=r.id,
            from_item_id=r.from_item_id,
            to_item_id=r.to_item_id,
            rule_type=r.rule_type,
            constraints=r.constraints,
            reason=r.reason,
        )
        for r in db.scalars(
            select(CompatibilityRule)
            .where(CompatibilityRule.from_item_id.in_(ids), CompatibilityRule.to_item_id.in_(ids))
            .order_by(CompatibilityRule.id)
        )
    ]
    return Snapshot(
        extraction_run_id=run.id,
        document_version_id=run.document_version_id,
        extraction_schema_version=run.schema_version,
        prompt_version=run.prompt_version,
        model_identifier=str(run.model_config.get("provider", "unknown"))
        + "/"
        + str(run.model_config.get("model", "unknown")),
        extraction_state=run.state,
        extraction_review_state=run.review_state,
        catalog_version_id=catalog.id,
        catalog_version=catalog.version,
        catalog_effective_from=catalog.effective_from,
        catalog_effective_to=catalog.effective_to,
        analysis_date=request.analysis_date,
        policy=request.policy,
        requirements=requirements,
        items=items,
        rules=rules,
        source_issues=source_issues,
    )


def create_analysis(db, request):
    snapshot = capture(db, request)
    payload = snapshot.model_dump(mode="json")
    identity = fingerprint(payload)
    existing = db.scalar(select(AnalysisRun).where(AnalysisRun.identity == identity))
    if existing:
        db.commit()
        return existing, True
    row = AnalysisRun(
        identity=identity,
        extraction_run_id=snapshot.extraction_run_id,
        catalog_version_id=snapshot.catalog_version_id,
        solver_version=SOLVER_VERSION,
    )
    db.add(row)
    try:
        db.flush()
        db.add(AnalysisSnapshot(analysis_id=row.id, payload=payload))
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(select(AnalysisRun).where(AnalysisRun.identity == identity))
        if row is None:
            raise
        return row, True
    return row, False


def execute_analysis(analysis_id, session_factory=SessionLocal):
    with (
        stage_timer("analysis", trace_id=str(analysis_id), job_id=str(analysis_id)),
        session_factory() as db,
    ):
        claimed = db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == analysis_id, AnalysisRun.state == "queued")
            .values(state="running", attempts=AnalysisRun.attempts + 1)
        )
        db.commit()
        if claimed.rowcount != 1:
            return
        try:
            row = db.get(AnalysisRun, analysis_id)
            payload = db.get(AnalysisSnapshot, analysis_id).payload
            if row.identity != fingerprint(payload) or row.solver_version != SOLVER_VERSION:
                raise SolverError("analysis_snapshot_or_solver_mismatch")
            snapshot = Snapshot.model_validate(payload)
            result = solve(snapshot)
            for config in result.configurations:
                db.add(
                    GeneratedConfiguration(
                        id=config.id,
                        analysis_id=row.id,
                        platform_id=config.platform_id,
                        rank=config.rank,
                        status=config.status,
                        payload=config.model_dump(mode="json"),
                    )
                )
                db.flush()
                for line in config.bom:
                    db.add(
                        AnalysisBOM(
                            configuration_id=config.id,
                            item_id=line.item_id,
                            price_id=line.price_id,
                            payload=line.model_dump(mode="json"),
                        )
                    )
                for evaluation in config.evaluations:
                    db.add(
                        RequirementEvaluation(
                            configuration_id=config.id,
                            requirement_id=evaluation.requirement_id,
                            result=evaluation.result,
                            payload=evaluation.model_dump(mode="json"),
                        )
                    )
                db.add(
                    AnalysisCost(
                        configuration_id=config.id,
                        policy_version=config.cost.policy.version,
                        payload=config.cost.model_dump(mode="json"),
                    )
                )
                for index, finding in enumerate(config.issues):
                    db.add(
                        AnalysisIssue(
                            analysis_id=row.id,
                            configuration_id=config.id,
                            ordinal=index,
                            severity=finding.severity,
                            payload=finding.model_dump(mode="json"),
                        )
                    )
            for index, finding in enumerate(result.issues):
                db.add(
                    AnalysisIssue(
                        analysis_id=row.id,
                        ordinal=index,
                        severity=finding.severity,
                        payload=finding.model_dump(mode="json"),
                    )
                )
            # Flush all immutable children before marking the parent terminal, in one transaction.
            db.flush()
            row.state, row.status, row.outcome = "completed", result.status, result.outcome
            row.finished_at = datetime.now(UTC)
            row.summary = {
                "examined_combinations": result.examined_combinations,
                "configuration_count": len(result.configurations),
                "valid_count": result.valid_count,
                "provisional_count": result.provisional_count,
                "top_k": snapshot.policy.top_k,
                "truncated_top_k": result.truncated_top_k,
                "options": result.options,
                "rejected": [r.model_dump(mode="json") for r in result.rejected],
            }
            db.commit()
            metrics.increment(
                "tda_solver_outcomes_total", outcome=str(row.outcome), status=str(row.status)
            )
            LOGGER.info(
                "analysis_completed id=%s status=%s count=%d",
                row.id,
                row.status,
                len(result.configurations),
            )
        except Exception as exc:  # noqa: BLE001 -- terminal safe error, atomic rollback
            db.rollback()
            row = db.get(AnalysisRun, analysis_id)
            row.state = "failed"
            row.error_code = (
                exc.code
                if isinstance(exc, SolverError)
                else "invalid_snapshot"
                if isinstance(exc, ValidationError)
                else "analysis_internal_error"
            )
            row.finished_at = datetime.now(UTC)
            db.commit()
            metrics.increment("tda_solver_failures_total", code=str(row.error_code))
            LOGGER.warning(
                "analysis_failed id=%s code=%s exception_type=%s",
                analysis_id,
                row.error_code,
                type(exc).__name__,
            )
