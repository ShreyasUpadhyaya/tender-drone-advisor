import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.solver.contracts import Snapshot
from app.solver.numbers import SolverError
from app.solver.orchestration import fingerprint, solve

SEED = json.loads((Path(__file__).parent / "fixtures" / "solver_catalog.json").read_text())


def example_snapshot():
    items = []
    for item in SEED["items"]:
        items.append(
            {
                "id": item["sku"],
                "catalog_version_id": "synthetic-catalog",
                "sku": item["sku"],
                "item_version": 1,
                "category": item["category"],
                "name": item["name"],
                "manufacturer": item["manufacturer"],
                "weight_g": item["weight_grams"],
                "lifecycle_status": "active",
                "availability": item["availability"],
                "inventory_qty": item["inventory_qty"],
                "lead_time_days": item["lead_time_days"],
                "supplier_id": "synthetic-supplier",
                "supplier_active": True,
                "specs": deepcopy(item["specs"]),
                "provenance": {"source": "synthetic-demo"},
                "prices": [
                    {
                        "id": item["sku"] + "-price",
                        "amount_paise": item["cost_paise"],
                        "currency": "INR",
                        "effective_from": "2026-01-01",
                    }
                ],
            }
        )
    requirements = []
    for category, value, unit, operator in [
        ("platform", "multirotor", "none", "enum"),
        ("range", 25000, "m", "minimum"),
        ("endurance", 1800, "s", "minimum"),
        ("payload", 2, "kg", "minimum"),
    ]:
        quote = f"Synthetic {category}: {value} {unit}."
        requirements.append(
            {
                "id": category,
                "extraction_run_id": "synthetic-extraction",
                "requirement": {
                    "category": category,
                    "attribute": category,
                    "semantics": "mandatory",
                    "operator": operator,
                    "original_value": value,
                    "original_unit": unit,
                    "normalized_value": value,
                    "normalized_unit": unit,
                    "confidence": 0.95,
                    "evidence": [{"span_id": "synthetic-span", "quote": quote}],
                    "validated_evidence": [
                        {
                            "document_id": "synthetic-document",
                            "document_version_id": "synthetic-version",
                            "span_id": "synthetic-span",
                            "page_number": 1,
                            "section_name": None,
                            "chunk_index": 0,
                            "quote": quote,
                            "quote_start": 0,
                            "quote_end": len(quote),
                        }
                    ],
                },
            }
        )
    return Snapshot.model_validate(
        {
            "extraction_run_id": "synthetic-extraction",
            "document_version_id": "synthetic-version",
            "extraction_schema_version": "requirements-v2",
            "prompt_version": "fixture",
            "model_identifier": "fake/fixture",
            "extraction_state": "completed",
            "extraction_review_state": "not_required",
            "catalog_version_id": "synthetic-catalog",
            "catalog_version": "synthetic-c05-v1",
            "analysis_date": "2026-09-25",
            "policy": {},
            "requirements": requirements,
            "items": items,
            "rules": [
                {
                    "id": "synthetic-rule",
                    "from_item_id": "S5-MOTOR",
                    "to_item_id": "S5-ESC-CHEAP",
                    "rule_type": "incompatible",
                    "reason": "ESC current below motor",
                    "constraints": {},
                }
            ],
        }
    )


def change(snapshot, fn):
    data = snapshot.model_dump(mode="json")
    fn(data)
    return Snapshot.model_validate(data)


def part(snapshot, sku):
    return next(x for x in snapshot.items if x.sku == sku)


def all_codes(result):
    return (
        {i.code for c in result.configurations for i in c.issues}
        | {i.code for r in result.rejected for i in r.issues}
        | {i.code for i in result.issues}
    )


def test_valid_combinations_cost_tradeoffs_traceability_and_determinism():
    snapshot = example_snapshot()
    result = solve(snapshot)
    assert result.status == "feasible"
    assert result.valid_count == 4
    assert result.model_dump(mode="json") == solve(snapshot).model_dump(mode="json")
    valid = [c for c in result.configurations if c.status == "feasible"]
    cheapest = next(c for c in valid if "lowest_cost" in c.labels)
    assert "S5-ESC-VALID" in [s.item.sku for s in cheapest.selections]
    assert cheapest.outcome == "feasible"
    assert any(c.modified and c.outcome == "feasible_with_changes" for c in valid)
    assert any("S5-ESC-OVER" in [s.item.sku for s in c.selections] for c in valid)
    assert any(c.objective_breakdown["overspec"] > 0 for c in valid)
    assert {"explicit_incompatibility", "inventory_unavailable"} <= all_codes(result)
    assert all(not c.recommended for c in result.configurations if c.status != "feasible")
    assert all(e.requirement.validated_evidence and e.actual for c in valid for e in c.evaluations)
    assert all(l.price_id and l.item_version == 1 for c in valid for l in c.bom)
    assert all(type(c.cost.total_paise) is int for c in valid)
    assert cheapest.cost.material_paise == 1878000
    assert cheapest.cost.total_paise == 2065800
    assert cheapest.total_weight_g == 6400


@pytest.mark.parametrize(
    "sku,field,value,code",
    [
        ("S5-MOTOR", "max_current_a", 100, "esc_current_below_motor"),
        ("S5-BATTERY", "voltage_v", 100, "battery_voltage_incompatible"),
        ("S5-BATTERY", "max_current_a", 1, "battery_current_insufficient"),
        ("S5-FRAME", "payload_kg", 1, "payload_capacity_exceeded"),
        ("S5-FRAME", "mtow_kg", 2, "mtow_exceeded"),
        ("S5-CAMERA", "interface", "wrong-interface", "interface_incompatible"),
    ],
)
def test_verified_safety_rejections(sku, field, value, code):
    snapshot = example_snapshot()
    part(snapshot, sku).specs[field] = value
    result = solve(snapshot)
    assert code in all_codes(result)
    assert all(
        not c.recommended for c in result.configurations if any(i.code == code for i in c.issues)
    )


@pytest.mark.parametrize("category,value", [("range", 100000), ("endurance", 10000)])
def test_impossible_mandatory_requirement_cannot_be_overridden_by_cost(category, value):
    snapshot = example_snapshot()
    record = next(r for r in snapshot.requirements if r.id == category)
    record.requirement.normalized_value = value
    snapshot.policy.weights.cost = 100
    result = solve(snapshot)
    assert result.status == "infeasible" and result.outcome == "not_feasible"
    assert not result.options
    assert "mandatory_constraint_failed" in all_codes(result)
    assert all(not c.recommended for c in result.configurations)


@pytest.mark.parametrize(
    "case",
    [
        "missing_category",
        "review",
        "envelope",
        "assembly",
        "unknown_attribute",
        "unsupported_unit",
        "missing_price",
        "expired_price",
        "currency",
        "overlap",
    ],
)
def test_unknowns_require_review(case):
    snapshot = example_snapshot()
    if case == "missing_category":
        snapshot.requirements = snapshot.requirements[:3]
    elif case == "review":
        snapshot.extraction_state, snapshot.extraction_review_state = (
            "needs_review",
            "decision_recorded",
        )
    elif case in ("envelope", "assembly"):
        del part(snapshot, "S5-FRAME").specs[case]
    elif case == "unknown_attribute":
        snapshot.requirements[1].requirement.attribute = "unmapped_radius"
    elif case == "unsupported_unit":
        snapshot.requirements[1].requirement.normalized_unit = "km"
    else:
        item = part(snapshot, "S5-FRAME")
        if case == "missing_price":
            item.prices = []
        elif case == "expired_price":
            item.prices[0].effective_to = date(2026, 1, 31)
        elif case == "currency":
            item.prices[0].currency = "USD"
        else:
            item.prices.append(item.prices[0].model_copy(update={"id": "overlapping"}))
    result = solve(snapshot)
    assert result.status == "needs_review"
    assert not result.options and all(not c.recommended for c in result.configurations)


def test_optional_scoring_does_not_make_hard_failure_and_conditionals_remain_explicit():
    snapshot = example_snapshot()
    preferred = deepcopy(snapshot.requirements[1])
    preferred.id = "preferred-range"
    preferred.requirement.semantics = "preferred"
    preferred.requirement.normalized_value = 100000
    snapshot.requirements.append(preferred)
    snapshot.policy.nonblocking_assumptions = ["Synthetic operator-supplied labour estimate."]
    result = solve(snapshot)
    assert result.status == "conditionally_feasible"
    assert all(c.requirement_coverage_bps == 8000 for c in result.configurations if c.recommended)
    assert any(
        e.reason_code == "preference_not_met" for c in result.configurations for e in c.evaluations
    )


def test_price_selection_and_separated_cost_arithmetic():
    snapshot = example_snapshot()
    item = part(snapshot, "S5-FRAME")
    previous = item.prices[0].model_copy(
        update={
            "id": "old-price",
            "amount_paise": 1,
            "effective_from": date(2025, 1, 1),
            "effective_to": date(2025, 12, 31),
        }
    )
    item.prices.append(previous)
    snapshot.policy.fleet_quantity = 3
    cost = snapshot.policy.cost
    cost.engineering_integration_paise = 50001
    cost.labour_paise_per_drone = 10001
    cost.overhead_bps, cost.contingency_bps, cost.tax_bps, cost.margin_bps = 100, 500, 1800, 200
    result = solve(snapshot)
    cheapest = next(c for c in result.configurations if "lowest_cost" in c.labels)
    assert all(l.subtotal_paise == l.unit_cost_paise * l.quantity for l in cheapest.bom)
    assert all(l.price_id != "old-price" for l in cheapest.bom)
    assert cheapest.cost.labour_paise == 30003
    base = cheapest.cost.material_paise + 50001 + 30003
    assert cheapest.cost.overhead_paise == (base * 100 + 9999) // 10000
    assert cheapest.cost.contingency_paise == (base * 500 + 9999) // 10000
    assert cheapest.cost.total_paise == sum(
        [
            base,
            cheapest.cost.overhead_paise,
            cheapest.cost.contingency_paise,
            cheapest.cost.tax_paise,
            cheapest.cost.margin_paise,
        ]
    )


def test_deprecated_supplier_inventory_and_objective_weights():
    snapshot = example_snapshot()
    part(snapshot, "S5-ESC-OVER").lifecycle_status = "deprecated"
    first = solve(snapshot)
    assert "inactive_or_deprecated_component" in all_codes(first)
    assert all(
        "S5-ESC-OVER" not in [s.item.sku for s in c.selections]
        for c in first.configurations
        if c.recommended
    )
    snapshot.policy.weights.cost = 0
    snapshot.policy.weights.weight = 100
    second = solve(snapshot)
    recommended = next(c for c in second.configurations if c.recommended)
    assert "S5-BATTERY-LIGHT" in [s.item.sku for s in recommended.selections]
    part(snapshot, "S5-FRAME").supplier_active = False
    assert solve(snapshot).status == "infeasible"


def test_limits_overflow_and_safe_precision():
    snapshot = example_snapshot()
    snapshot.policy.max_combinations = 1
    with pytest.raises(SolverError, match="combination_limit"):
        solve(snapshot)
    snapshot = example_snapshot()
    snapshot.policy.max_evaluations = 1
    with pytest.raises(SolverError, match="evaluation_limit"):
        solve(snapshot)
    snapshot = example_snapshot()
    part(snapshot, "S5-FRAME").prices[0].amount_paise = 2**63 - 1
    with pytest.raises(SolverError, match="integer_overflow"):
        solve(snapshot)
    data = example_snapshot().model_dump(mode="json")
    data["items"][0]["prices"][0]["amount_paise"] = -1
    with pytest.raises(ValidationError):
        Snapshot.model_validate(data)


def test_rated_mtow_requirement_is_not_confused_with_actual_mass():
    snapshot = example_snapshot()
    record = deepcopy(snapshot.requirements[-1])
    record.id = "rated-mtow"
    record.requirement.category = "mtow"
    record.requirement.attribute = "mtow"
    record.requirement.operator = "maximum"
    record.requirement.original_value = 10
    record.requirement.normalized_value = 10
    snapshot.requirements.append(record)
    result = solve(snapshot)
    assert result.status == "infeasible"
    assert all(c.total_weight_g < 10000 for c in result.configurations)
    assert all(
        e.actual.value == 15000 and e.result == "failed"
        for c in result.configurations
        for e in c.evaluations
        if e.requirement_id == "rated-mtow"
    )
    record.requirement.operator = "minimum"
    assert solve(snapshot).status == "feasible"


def test_changed_policy_snapshot_identity_and_replay():
    snapshot = example_snapshot()
    original = fingerprint(snapshot.model_dump(mode="json"))
    snapshot.policy.top_k = 1
    result = solve(snapshot)
    assert result.truncated_top_k
    assert original != fingerprint(snapshot.model_dump(mode="json"))
    snapshot.catalog_version_id = "new-version"
    assert original != fingerprint(snapshot.model_dump(mode="json"))
    cloned = Snapshot.model_validate_json(snapshot.model_dump_json())
    assert solve(snapshot) == solve(cloned)
