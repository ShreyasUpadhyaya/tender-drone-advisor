"""C08 reviewer and scenario contract regressions; all providers are deterministic fakes."""

import test_extraction as helpers
from sqlalchemy import func, select
from test_solver import SEED

from app.analysis.service import execute_analysis
from app.db import SessionLocal
from app.extraction.models import ExtractionRun, ReviewDecisionEvent
from app.extraction.workflow import execute_run
from app.scenarios.models import ScenarioAssumption, ScenarioVersion

ingested = helpers.ingested


def _empty_review_run(client, ingested):
    version, _ = ingested("A government tender with no configured fake extractor output.")
    run = helpers.start(client, version)
    model, _ = helpers.adapter(helpers.response([]))
    execute_run(run, SessionLocal, model)
    return run


def _complete_run(client, ingested):
    version, span = ingested()
    run = helpers.start(client, version)
    model, _ = helpers.adapter(helpers.response(helpers.clean(span)))
    execute_run(run, SessionLocal, model)
    return run


def test_review_workspace_exposes_zero_accepted_state_and_issue_mapping(client, ingested):
    run = _empty_review_run(client, ingested)
    workspace = client.get(f"/v1/extraction-runs/{run}/review-workspace")
    assert workspace.status_code == 200, workspace.text
    data = workspace.json()
    assert data["run"]["state"] == "needs_review"
    assert data["counts"]["accepted"] == 0
    assert data["counts"]["issues"] == 4
    assert data["rejected_candidates"] == []
    assert "internal scenario assumption" in data["next_action"].lower()
    assert all(item["issue"]["requirement_index"] is None for item in data["issues"])


def test_review_events_are_append_only_and_require_source_for_verified_claims(client, ingested):
    run = _complete_run(client, ingested)
    workspace = client.get(f"/v1/extraction-runs/{run}/review-workspace").json()
    requirement = workspace["accepted_requirements"][0]
    span_id = requirement["requirement"]["validated_evidence"][0]["span_id"]
    refused = client.post(
        f"/v1/extraction-runs/{run}/review-decisions",
        json={
            "action": "accept_verified_extraction",
            "reviewer": "reviewer-a",
            "rationale": "Checked clause.",
            "requirement_id": requirement["id"],
        },
    )
    assert refused.status_code == 422
    recorded = client.post(
        f"/v1/extraction-runs/{run}/review-decisions",
        json={
            "action": "correct_transcription_or_normalization",
            "reviewer": "reviewer-a",
            "rationale": "The cited clause confirms the corrected transcription.",
            "requirement_id": requirement["id"],
            "source_span_id": span_id,
            "before_value": {"value": 25, "unit": "km"},
            "after_value": {"value": 25, "unit": "km"},
        },
    )
    assert recorded.status_code == 201, recorded.text
    refreshed = client.get(f"/v1/extraction-runs/{run}/review-workspace").json()
    assert refreshed["events"][0]["reviewer"] == "local-demo-operator"
    assert refreshed["events"][0]["is_current"] is True
    assert refreshed["events"][0]["source_span_id"] == span_id
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ReviewDecisionEvent)) == 1
        assert db.get(ExtractionRun, run).state == "completed"


def test_review_revision_preserves_history_and_cannot_spoof_actor(client, ingested):
    run = _empty_review_run(client, ingested)
    issue = client.get(f"/v1/extraction-runs/{run}/review-workspace").json()["issues"][0]
    first = client.post(
        f"/v1/extraction-runs/{run}/review-decisions",
        json={
            "action": "mark_unresolved",
            "reviewer": "spoofed-user",
            "rationale": "No tender evidence states this capability.",
            "issue_id": issue["id"],
        },
    )
    assert first.status_code == 201
    assert first.json()["reviewer"] == "local-demo-operator"
    revised = client.post(
        f"/v1/extraction-runs/{run}/review-decisions",
        json={
            "action": "record_documented_assumption",
            "rationale": "Proceed with a separately labelled internal estimate.",
            "issue_id": issue["id"],
            "supersedes_event_id": first.json()["id"],
            "after_value": {"status": "internal_assumption_only"},
        },
    )
    assert revised.status_code == 201
    workspace = client.get(f"/v1/extraction-runs/{run}/review-workspace").json()
    assert len(workspace["events"]) == 2
    by_id = {event["id"]: event for event in workspace["events"]}
    assert by_id[first.json()["id"]]["is_current"] is False
    assert by_id[revised.json()["id"]]["is_current"] is True
    assert by_id[revised.json()["id"]]["supersedes_event_id"] == first.json()["id"]


def test_failed_extraction_retry_is_linked_and_idempotent(client, ingested):
    run = _complete_run(client, ingested)
    with SessionLocal() as db:
        source = db.get(ExtractionRun, run)
        source.state, source.error_code = "failed", "provider_failure"
        db.commit()
    first = client.post(f"/v1/extraction-runs/{run}/retry")
    second = client.post(f"/v1/extraction-runs/{run}/retry")
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["trace_id"] == second.json()["trace_id"]
    with SessionLocal() as db:
        retried = db.get(ExtractionRun, first.json()["trace_id"])
        assert retried.retry_of_id == run
        assert db.get(ExtractionRun, run).state == "failed"


def test_versioned_scenarios_replay_without_rewriting_tender_facts(client, ingested):
    run = _complete_run(client, ingested)
    imported = client.post("/v1/catalog/import", json=SEED).json()
    body = {
        "extraction_run_id": run,
        "catalog_version_id": imported["catalog_version_id"],
        "analysis_date": "2026-09-27",
        "name": "Government estimate",
        "intent": "cost_optimized",
        "reviewer": "reviewer-a",
        "rationale": "Cost planning only; tender compliance remains separately reviewed.",
        "assumptions": [
            {
                "category": "range",
                "attribute": "range",
                "operator": "minimum",
                "value": 20,
                "unit": "km",
                "rationale": "Internal planning assumption, not a tender claim.",
                "provenance": "internal_assumption",
            }
        ],
    }
    first = client.post("/v1/scenarios", json=body)
    assert first.status_code == 202, first.text
    scenario = first.json()
    execute_analysis(scenario["analysis"]["id"])
    replayed = client.post("/v1/scenarios", json=body)
    assert replayed.status_code == 200 and replayed.json()["id"] == scenario["id"]
    final = client.get(f"/v1/scenarios/{scenario['id']}").json()
    assert final["analysis"]["state"] == "completed"
    assert final["tender_requirements_verified"] is False
    assert final["assumptions_outstanding"] is True
    assert final["bid_compliance_review_required"] is True
    assert final["analysis"]["status"] == "needs_review"
    changed = {**body, "intent": "performance_oriented"}
    next_version = client.post("/v1/scenarios", json=changed)
    assert next_version.status_code == 202 and next_version.json()["id"] != scenario["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ScenarioVersion)) == 2
        assert db.scalar(select(func.count()).select_from(ScenarioAssumption)) == 2


def test_scenario_catalog_choice_is_immutable_and_limits_solver_category(client, ingested):
    run = _complete_run(client, ingested)
    imported = client.post("/v1/catalog/import", json=SEED).json()
    body = {
        "extraction_run_id": run,
        "catalog_version_id": imported["catalog_version_id"],
        "analysis_date": "2026-09-27",
        "name": "Constrained estimate",
        "intent": "baseline",
        "reviewer": "reviewer-a",
        "rationale": "Select a documented ESC option for comparison only.",
        "component_skus": ["S5-ESC-OVER"],
        "assumptions": [],
    }
    created = client.post("/v1/scenarios", json=body)
    assert created.status_code == 202, created.text
    scenario = created.json()
    assert scenario["component_preferences"] == ["S5-ESC-OVER"]
    execute_analysis(scenario["analysis"]["id"])
    configurations = client.get(f"/v1/analyses/{scenario['analysis']['id']}/configurations").json()[
        "configurations"
    ]
    assert configurations
    assert all(
        "S5-ESC-OVER" in [line["sku"] for line in configuration["bom"]]
        for configuration in configurations
    )
    replayed = client.post("/v1/scenarios", json=body)
    assert replayed.status_code == 200
    assert replayed.json()["id"] == scenario["id"]
