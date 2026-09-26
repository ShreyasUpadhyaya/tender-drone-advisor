"""Admin-owned inventory overlays remain auditable and scenario-scoped."""

import test_extraction as helpers
from test_solver import SEED

from app.analysis.models import AnalysisSnapshot
from app.analysis.service import execute_analysis
from app.db import SessionLocal
from app.extraction.workflow import execute_run
from app.main import app
from app.security import Actor, Role, get_actor

ingested = helpers.ingested


def _run(client, ingested):
    version, span = ingested()
    run = helpers.start(client, version)
    model, _ = helpers.adapter(helpers.response(helpers.clean(span)))
    execute_run(run, SessionLocal, model)
    return run


def _catalog(client):
    imported = client.post("/v1/catalog/import", json=SEED).json()
    items = client.get("/v1/catalog/items", params={"catalog_version": "synthetic-c05-v1"}).json()
    return imported, items


def _record(client, item, run, quantity=2, expected=0, expected_on=None):
    body = {
        "catalog_item_id": item["id"],
        "on_hand_quantity": quantity,
        "expected_quantity": expected,
        "expected_on": expected_on,
        "location": "Demo stores",
        "rationale": "Physical count completed by the responsible administrator.",
        "extraction_run_id": run,
        "requirement_categories": ["platform"],
    }
    return client.post("/v1/inventory/records", json=body), body


def test_admin_inventory_is_idempotent_versioned_and_owner_only(client, ingested):
    run = _run(client, ingested)
    _, items = _catalog(client)
    item = next(value for value in items if value["sku"] == "S5-FRAME")
    created, body = _record(client, item, run)
    assert created.status_code == 201, created.text
    repeated = client.post("/v1/inventory/records", json=body)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == created.json()["id"]
    assert created.json()["owner_subject"] == "local-demo-operator"
    assert created.json()["solver_eligible"] is True
    workspace = client.get("/v1/inventory/session").json()["workspace_id"]

    app.dependency_overrides[get_actor] = lambda: Actor(
        subject="other-admin",
        workspace_id=workspace,
        roles=frozenset({Role.ADMIN}),
    )
    denied = client.post(
        f"/v1/inventory/records/{created.json()['id']}/versions",
        json={**body, "on_hand_quantity": 3, "catalog_item_id": None},
    )
    assert denied.status_code == 422  # create-only field remains forbidden on revision
    revision = {key: value for key, value in body.items() if key != "catalog_item_id"}
    denied = client.post(
        f"/v1/inventory/records/{created.json()['id']}/versions",
        json={**revision, "on_hand_quantity": 3},
    )
    assert denied.status_code == 403
    app.dependency_overrides[get_actor] = lambda: Actor(
        subject="local-demo-operator",
        workspace_id=workspace,
        roles=frozenset({Role.ADMIN}),
        demo=True,
    )
    updated = client.post(
        f"/v1/inventory/records/{created.json()['id']}/versions",
        json={**revision, "on_hand_quantity": 3},
    )
    assert updated.status_code == 201
    assert updated.json()["current"]["version_number"] == 2
    assert len(updated.json()["history"]) == 2


def test_non_admin_is_forbidden_and_unlisted_stock_never_enters_solver(client):
    app.dependency_overrides[get_actor] = lambda: Actor(
        subject="reviewer-a",
        workspace_id="demo-workspace",
        roles=frozenset({Role.REVIEWER}),
    )
    denied = client.post(
        "/v1/inventory/records",
        json={
            "sku": "LOCAL-UNKNOWN",
            "name": "Unvalidated local payload",
            "category": "payload",
            "manufacturer": "Unknown",
            "on_hand_quantity": 1,
            "location": "Stores",
            "rationale": "Awaiting engineering and catalog validation.",
        },
    )
    assert denied.status_code == 403
    app.dependency_overrides[get_actor] = lambda: Actor(
        subject="stock-admin",
        workspace_id="demo-workspace",
        roles=frozenset({Role.ADMIN}),
    )
    created = client.post(
        "/v1/inventory/records",
        json={
            "sku": "LOCAL-UNKNOWN",
            "name": "Unvalidated local payload",
            "category": "payload",
            "manufacturer": "Unknown",
            "on_hand_quantity": 1,
            "expected_quantity": 0,
            "location": "Stores",
            "rationale": "Awaiting engineering and catalog validation.",
        },
    )
    assert created.status_code == 201
    assert created.json()["solver_eligible"] is False
    assert created.json()["source_status"] == "pending_catalog_validation"


def test_scenario_snapshots_inventory_version_and_new_count_changes_identity(client, ingested):
    run = _run(client, ingested)
    imported, items = _catalog(client)
    item = next(value for value in items if value["sku"] == "S5-FRAME")
    created, body = _record(client, item, run, quantity=2)
    record = created.json()
    scenario = {
        "extraction_run_id": run,
        "catalog_version_id": imported["catalog_version_id"],
        "analysis_date": "2026-09-27",
        "name": "Manual inventory estimate",
        "intent": "baseline",
        "rationale": "Use the administrator-counted stock only for this estimate.",
        "inventory_record_ids": [record["id"]],
        "assumptions": [],
    }
    first = client.post("/v1/scenarios", json=scenario)
    assert first.status_code == 202, first.text
    execute_analysis(first.json()["analysis"]["id"])
    replay = client.post("/v1/scenarios", json=scenario)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert first.json()["inventory_overlays"][0]["on_hand_quantity"] == 2
    final = client.get(f"/v1/scenarios/{first.json()['id']}").json()
    assert final["analysis"]["status"] == "feasible"
    assert final["tender_requirements_verified"] is True
    with SessionLocal() as db:
        snapshot = (
            db.query(AnalysisSnapshot).filter_by(analysis_id=first.json()["analysis"]["id"]).one()
        )
        frame = next(value for value in snapshot.payload["items"] if value["sku"] == "S5-FRAME")
        assert frame["inventory_qty"] == 2
        assert frame["provenance"]["manual_inventory"]["recorded_by"] == "local-demo-operator"

    revision = {key: value for key, value in body.items() if key != "catalog_item_id"}
    updated = client.post(
        f"/v1/inventory/records/{record['id']}/versions",
        json={**revision, "on_hand_quantity": 4},
    )
    assert updated.status_code == 201
    second = client.post("/v1/scenarios", json=scenario)
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["analysis"]["id"] != first.json()["analysis"]["id"]
    assert second.json()["inventory_overlays"][0]["on_hand_quantity"] == 4
    assert first.json()["inventory_version_ids"] != second.json()["inventory_version_ids"]


def test_scenario_rejects_inventory_from_a_different_catalog_snapshot(client, ingested):
    run = _run(client, ingested)
    _, items = _catalog(client)
    item = next(value for value in items if value["sku"] == "S5-FRAME")
    record, _ = _record(client, item, run)

    imported = client.post(
        "/v1/catalog/versions",
        json={
            "version": "synthetic-c05-v2",
            "status": "draft",
            "source": "synthetic-demo",
            "effective_from": "2026-09-27",
        },
    )
    assert imported.status_code == 201, imported.text

    rejected = client.post(
        "/v1/scenarios",
        json={
            "extraction_run_id": run,
            "catalog_version_id": imported.json()["id"],
            "analysis_date": "2026-09-27",
            "name": "Mismatched inventory estimate",
            "intent": "baseline",
            "rationale": "The stock item must belong to this exact catalog snapshot.",
            "inventory_record_ids": [record.json()["id"]],
            "assumptions": [],
        },
    )
    assert rejected.status_code == 409
    assert "scenario_inventory_not_in_catalog_snapshot" in rejected.text
