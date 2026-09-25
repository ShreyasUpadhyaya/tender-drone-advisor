from copy import deepcopy

import pytest
import test_extraction as helpers
from sqlalchemy import func, select
from test_solver import SEED

from app.analysis.models import (
    AnalysisRun,
    AnalysisSnapshot,
    GeneratedConfiguration,
    RequirementEvaluation,
)
from app.analysis.service import execute_analysis
from app.db import SessionLocal
from app.extraction.models import EvidenceLink, ExtractionRun
from app.extraction.workflow import execute_run
from app.main import app
from app.routes.documents import get_queue
from app.solver.contracts import Snapshot
from app.solver.orchestration import solve

ingested = helpers.ingested


@pytest.fixture
def inputs(client, ingested):
    version, span = ingested()
    run_id = helpers.start(client, version)
    model, _ = helpers.adapter(helpers.response(helpers.clean(span)))
    execute_run(run_id, SessionLocal, model)
    imported = client.post("/v1/catalog/import", json=SEED)
    assert imported.status_code == 201, imported.text
    return {
        "extraction_run_id": run_id,
        "catalog_version_id": imported.json()["catalog_version_id"],
        "analysis_date": "2026-09-25",
    }


def begin(client, inputs):
    response = client.post("/v1/analyses", json=inputs)
    assert response.status_code == 202, response.text
    analysis_id = response.json()["id"]
    execute_analysis(analysis_id)
    result = client.get(f"/v1/analyses/{analysis_id}").json()
    assert result["state"] == "completed", result
    return analysis_id, result


def test_analysis_api_bom_traceability_history_and_top_k(client, inputs):
    inputs["policy"] = {"top_k": 1}
    analysis_id, summary = begin(client, inputs)
    assert summary["status"] == "feasible" and summary["attempts"] == 1
    response = client.get(f"/v1/analyses/{analysis_id}/configurations").json()
    assert len(response["configurations"]) == 1 and response["total"] == 6
    config = response["configurations"][0]
    bom = client.get(f"/v1/analyses/{analysis_id}/configurations/{config['id']}/bom").json()
    assert len(bom["lines"]) == 9
    matrix = client.get(
        f"/v1/analyses/{analysis_id}/configurations/{config['id']}/requirements"
    ).json()
    assert len(matrix["evaluations"]) == 4
    assert all(e["extraction_run_id"] == inputs["extraction_run_id"] for e in matrix["evaluations"])
    assert all(e["requirement"]["validated_evidence"] for e in matrix["evaluations"])
    assert client.get(f"/v1/analyses/{analysis_id}/issues").status_code == 200
    same = client.post("/v1/analyses", json=inputs)
    assert (
        same.status_code == 200 and same.json()["id"] == analysis_id and same.json()["idempotent"]
    )
    execute_analysis(analysis_id)
    assert client.get(f"/v1/analyses/{analysis_id}").json()["attempts"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1
        assert db.scalar(select(func.count()).select_from(RequirementEvaluation)) == 24
        snapshot = Snapshot.model_validate(db.get(AnalysisSnapshot, analysis_id).payload)
        replay = solve(snapshot)
        persisted = db.scalars(
            select(GeneratedConfiguration)
            .where(GeneratedConfiguration.analysis_id == analysis_id)
            .order_by(GeneratedConfiguration.rank)
        ).all()
        assert [c.model_dump(mode="json") for c in replay.configurations] == [
            r.payload for r in persisted
        ]
    changed = deepcopy(inputs)
    changed["policy"]["top_k"] = 2
    new = client.post("/v1/analyses", json=changed)
    assert new.status_code == 202 and new.json()["id"] != analysis_id
    # Previously completed output is identical after a new policy is submitted.
    assert client.get(f"/v1/analyses/{analysis_id}/configurations").json() == response


@pytest.mark.parametrize(
    "field,value",
    [
        ("analysis_date", "25-09-2026"),
        ("analysis_date", "2026-02-30"),
        ("analysis_date", 1234),
        ("policy", {"top_k": "3"}),
        ("policy", {"cost": {"engineering_integration_paise": -1}}),
    ],
)
def test_json_contract_rejects_invalid_values(client, inputs, field, value):
    inputs[field] = value
    assert client.post("/v1/analyses", json=inputs).status_code == 422


@pytest.mark.parametrize(
    "specs",
    [
        {"min_voltage_v": "unknown", "max_voltage_v": 25},
        {"min_voltage_v": 30, "max_voltage_v": 25},
        {"max_current_a": -1},
        {"max_current_a": True},
    ],
)
def test_engineering_metadata_errors_are_structured_not_server_errors(client, specs):
    seed = deepcopy(SEED)
    next(item for item in seed["items"] if item["category"] == "motor")["specs"].update(specs)
    response = client.post("/v1/catalog/import", json=seed)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_catalog_record"


def test_invalid_evidence_and_schema_are_rejected(client, inputs):
    with SessionLocal() as db:
        db.get(ExtractionRun, inputs["extraction_run_id"]).schema_version = "unsupported"
        db.commit()
    result = client.post("/v1/analyses", json=inputs)
    assert result.status_code == 409 and result.json()["error"] == "unsupported_extraction_schema"
    with SessionLocal() as db:
        db.get(ExtractionRun, inputs["extraction_run_id"]).schema_version = "requirements-v2"
        link = db.scalar(select(EvidenceLink))
        db.delete(link)
        db.commit()
    result = client.post("/v1/analyses", json=inputs)
    assert result.status_code == 409 and result.json()["error"] == "requirement_evidence_invalid"


def test_review_status_is_never_approved(client, inputs):
    with SessionLocal() as db:
        row = db.get(ExtractionRun, inputs["extraction_run_id"])
        row.state, row.review_state = "needs_review", "pending"
        db.commit()
    analysis_id, result = begin(client, inputs)
    assert result["status"] == "needs_review"
    assert not result["summary"]["options"]
    configs = client.get(f"/v1/analyses/{analysis_id}/configurations").json()["configurations"]
    assert all(not c["recommended"] for c in configs)


def test_bounded_failure_is_atomic_and_idempotent(client, inputs):
    inputs["policy"] = {"max_combinations": 1}
    response = client.post("/v1/analyses", json=inputs)
    analysis_id = response.json()["id"]
    execute_analysis(analysis_id)
    failed = client.get(f"/v1/analyses/{analysis_id}").json()
    assert failed["state"] == "failed" and failed["error_code"] == "combination_limit_exceeded"
    assert client.get(f"/v1/analyses/{analysis_id}/configurations").json()["total"] == 0
    execute_analysis(analysis_id)
    assert client.get(f"/v1/analyses/{analysis_id}").json() == failed


def test_queue_failure_retry_and_typed_openapi(client, inputs):
    class BrokenQueue:
        def enqueue(self, *args, **kwargs):
            raise ConnectionError("PRIVATE QUEUE CONNECTION")

    original = app.dependency_overrides[get_queue]
    app.dependency_overrides[get_queue] = BrokenQueue
    response = client.post("/v1/analyses", json=inputs)
    assert response.status_code == 503 and "PRIVATE" not in response.text
    app.dependency_overrides[get_queue] = original
    repeated = client.post("/v1/analyses", json=inputs)
    assert repeated.status_code == 200
    schema = client.get("/openapi.json").json()
    for path, methods in schema["paths"].items():
        if path.startswith("/v1/analyses"):
            for method in methods.values():
                for code, response in method["responses"].items():
                    if code.startswith("2"):
                        assert "$ref" in response["content"]["application/json"]["schema"]


def test_worker_failure_rolls_back_all_result_rows(client, inputs, monkeypatch):
    response = client.post("/v1/analyses", json=inputs)
    from app.analysis import service

    original_solve = service.solve

    def broken(snapshot):
        result = original_solve(snapshot)
        result.configurations[-1].id = result.configurations[0].id
        return result

    monkeypatch.setattr(service, "solve", broken)
    execute_analysis(response.json()["id"])
    status = client.get(f"/v1/analyses/{response.json()['id']}").json()
    assert status["state"] == "failed"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(GeneratedConfiguration)) == 0
        assert db.scalar(select(func.count()).select_from(RequirementEvaluation)) == 0
