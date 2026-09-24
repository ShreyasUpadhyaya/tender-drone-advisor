import json
from copy import deepcopy

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import SessionLocal
from app.extraction.contracts import RequirementCandidate, ValidatedRequirement
from app.extraction.models import EvidenceLink, ExtractionNodeRun, ExtractionRun, RequirementRecord
from app.extraction.provider import ModelAdapter, build_adapter
from app.extraction.validation import normalize
from app.extraction.workflow import build_graph, execute_run
from app.models import SourceSpan
from app.settings import Settings, get_settings
from app.tasks import ingest_document_task

TEXT = "Platform: multirotor. Range >= 25 km. Endurance >= 30 min. Payload >= 2 kg."


def candidate(
    span_id, category="range", value=25.0, unit="km", operator="minimum", quote=TEXT, **overrides
):
    return {
        "category": category,
        "attribute": category,
        "semantics": "mandatory",
        "operator": operator,
        "original_value": value,
        "original_unit": unit,
        "confidence": 0.95,
        "evidence": [{"span_id": span_id, "quote": quote}],
        **overrides,
    }


def clean(span_id):
    return [
        candidate(span_id),
        candidate(span_id, "platform", "multirotor", "", "enum"),
        candidate(span_id, "endurance", 30.0, "min"),
        candidate(span_id, "payload", 2.0, "kg"),
    ]


def response(requirements):
    wire = []
    for item in requirements:
        if "original_value" not in item:
            wire.append(item)
            continue
        item = deepcopy(item)
        value = item.pop("original_value")
        kind = {
            "minimum": "scalar",
            "maximum": "scalar",
            "exact": "scalar",
            "range": "range",
            "boolean": "boolean",
            "text": "text",
            "enum": "text",
        }[item["operator"]]
        item["raw_value"] = (
            {"kind": "unknown", "value": None}
            if value == "unknown"
            else {"kind": "range", "lower": value[0], "upper": value[1]}
            if kind == "range"
            else {"kind": kind, "value": value}
        )
        wire.append(item)
    return json.dumps({"schema_version": "requirements-v2", "requirements": wire})


def adapter(*outputs):
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        output = outputs[min(len(calls) - 1, len(outputs) - 1)]
        if isinstance(output, Exception):
            raise output
        return output

    return ModelAdapter(RunnableLambda(invoke)), calls


@pytest.fixture
def ingested(client, storage, monkeypatch):
    monkeypatch.setattr("app.tasks.get_storage", lambda: storage)

    def create(text=TEXT):
        upload = client.post(
            "/v1/documents", files={"file": ("synthetic.txt", text.encode(), "text/plain")}
        ).json()
        ingest_document_task(upload["document_version_id"])
        with SessionLocal() as db:
            span = db.scalar(
                select(SourceSpan).where(
                    SourceSpan.document_version_id == upload["document_version_id"]
                )
            )
            return upload["document_version_id"], span.id

    return create


def start(client, version):
    result = client.post(f"/v1/document-versions/{version}/extractions")
    assert result.status_code == 202
    return result.json()["trace_id"]


def run_result(client, run):
    status = client.get(f"/v1/extraction-runs/{run}")
    assert status.status_code == 200
    requirements = client.get(f"/v1/extraction-runs/{run}/requirements").json()
    issues = client.get(f"/v1/extraction-runs/{run}/issues").json()
    return status.json(), requirements, {x["issue"]["code"] for x in issues}


def test_clean_extraction_persists_si_evidence_and_safe_audit(client, ingested):
    version, span = ingested()
    run = start(client, version)
    model, calls = adapter(response(clean(span)))
    execute_run(run, SessionLocal, model)
    status, requirements, codes = run_result(client, run)
    assert status["state"] == "completed"
    assert len(requirements) == 4 and codes == set()
    assert requirements[0]["requirement"]["normalized_value"] == 25000
    assert requirements[0]["requirement"]["normalized_unit"] == "m"
    anchor = requirements[0]["requirement"]["validated_evidence"][0]
    assert anchor["document_version_id"] == version and anchor["span_id"] == span
    assert TEXT[anchor["quote_start"] : anchor["quote_end"]] == anchor["quote"]
    assert len(calls) == 1
    assert all(
        node["status"] == "completed" and node["elapsed_ms"] >= 0 for node in status["nodes"]
    )
    assert TEXT not in json.dumps(status) and "Source spans JSON" not in json.dumps(status)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(EvidenceLink)) == 4
    execute_run(run, SessionLocal, model)
    assert len(calls) == 1  # terminal worker replay is a no-op
    repeated = client.post(f"/v1/document-versions/{version}/extractions")
    assert repeated.status_code == 200 and repeated.json()["trace_id"] == run


@pytest.mark.parametrize(
    "case,expected",
    [
        ("missing", "unknown_or_ambiguous"),
        ("low", "low_confidence"),
        ("conflict", "conflicting_requirement"),
        ("unsupported", "unsupported_unit"),
        ("wrong_dimension", "unit_dimension_mismatch"),
        ("hallucinated", "invalid_evidence"),
        ("bad_quote", "invalid_evidence"),
        ("bad_number", "unsupported_numeric_claim"),
        ("no_citation", "missing_evidence"),
        ("bad_text", "unsupported_text_claim"),
        ("empty", "missing_critical_category"),
    ],
)
def test_review_cases(client, ingested, case, expected):
    version, span = ingested(TEXT + " Range maximum 10 km.")
    items = clean(span)
    if case == "missing":
        items[0]["original_value"] = "unknown"
    elif case == "low":
        items[0]["confidence"] = 0.2
    elif case == "conflict":
        items.append(candidate(span, value=10.0, operator="maximum", quote="Range maximum 10 km."))
    elif case == "unsupported":
        items[0]["original_unit"] = "furlongs"
    elif case == "wrong_dimension":
        items[0]["original_unit"] = "kg"
    elif case == "hallucinated":
        items[0]["evidence"][0]["span_id"] = "invented"
    elif case == "bad_quote":
        items[0]["evidence"][0]["quote"] = "invented quote"
    elif case == "bad_number":
        items[0]["original_value"] = 250.0
    elif case == "no_citation":
        items[0]["evidence"] = []
    elif case == "bad_text":
        items[1]["original_value"] = "fixed-wing"
    elif case == "empty":
        items = []
    model, calls = adapter(response(items))
    run = start(client, version)
    execute_run(run, SessionLocal, model)
    status, _, codes = run_result(client, run)
    assert status["state"] == "needs_review" and expected in codes
    assert len(calls) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "not json",
        '{"schema_version":"requirements-v1","requirements":[',
        response([{"feasible": True}]),
    ],
)
def test_invalid_json_repairs_once_then_review(client, ingested, invalid):
    version, _ = ingested()
    run = start(client, version)
    model, calls = adapter(invalid)
    execute_run(run, SessionLocal, model)
    status, requirements, codes = run_result(client, run)
    assert status["state"] == "needs_review" and len(calls) == 2
    assert not requirements and "repair_exhausted" in codes
    assert [n["attempt"] for n in status["nodes"] if n["node"] == "extract_requirements"] == [1, 2]


def test_repair_success_and_duplicate_detection(client, ingested):
    version, span = ingested()
    run = start(client, version)
    items = clean(span)
    items.append(deepcopy(items[0]))
    model, calls = adapter("bad", response(items))
    execute_run(run, SessionLocal, model)
    status, requirements, codes = run_result(client, run)
    assert status["state"] == "completed" and len(calls) == 2 and len(requirements) == 5
    assert codes == {"duplicate_requirement"}


def test_transient_failure_bounded_and_auth_failure_not_retried(client, ingested):
    version, _ = ingested()
    run = start(client, version)
    model, calls = adapter(TimeoutError("PRIVATE PROVIDER PAYLOAD"))
    execute_run(run, SessionLocal, model)
    status, _, _ = run_result(client, run)
    assert status["state"] == "failed" and len(calls) == 2
    assert "PRIVATE" not in json.dumps(status)
    version2, _ = ingested(TEXT + " auth test")
    run2 = start(client, version2)
    model, calls = adapter(RuntimeError("PRIVATE AUTH KEY"))
    execute_run(run2, SessionLocal, model)
    assert run_result(client, run2)[0]["state"] == "failed" and len(calls) == 1


def test_review_decision_is_immutable_and_cannot_waive_validation(client, ingested):
    version, _ = ingested()
    run = start(client, version)
    execute_run(run, SessionLocal, build_adapter(Settings()))
    issue_id = client.get(f"/v1/extraction-runs/{run}/issues").json()[0]["id"]
    endpoint = f"/v1/extraction-runs/{run}/issues/{issue_id}/decision"
    body = {"decision": "acknowledge", "reviewer": "demo-reviewer", "note": "Ask the buyer."}
    assert client.post(endpoint, json=body).status_code == 200
    assert client.post(endpoint, json=body).status_code == 200
    body["note"] = "Changed decision"
    assert client.post(endpoint, json=body).status_code == 409
    assert run_result(client, run)[0]["state"] == "needs_review"


def test_cross_document_evidence_rejected(client, ingested):
    version, _ = ingested()
    _, other_span = ingested(TEXT + " Other tender")
    run = start(client, version)
    model, _ = adapter(response(clean(other_span)))
    execute_run(run, SessionLocal, model)
    assert run_result(client, run)[1] == []
    assert "invalid_evidence" in run_result(client, run)[2]


def test_messy_whitespace_and_missing_values(client, ingested):
    text = " Range\t :   unknown  \n  buyer to confirm. "
    version, span = ingested(text)
    run = start(client, version)
    model, _ = adapter(
        response(
            [candidate(span, value="unknown", quote="Range\t :   unknown", semantics="ambiguous")]
        )
    )
    execute_run(run, SessionLocal, model)
    status, requirements, _ = run_result(client, run)
    assert status["state"] == "needs_review"
    assert requirements[0]["requirement"]["normalized_value"] == "unknown"


@pytest.mark.parametrize(
    "category,value,unit,operator,expected,si",
    [
        ("range", 2.5, "km", "minimum", 2500, "m"),
        ("endurance", 30.0, "min", "minimum", 1800, "s"),
        ("temperature", [-20.0, 50.0], "°C", "range", [253.15, 323.15], "K"),
        ("wind_tolerance", 36.0, "km/h", "maximum", 10, "m/s"),
        ("battery", 10.0, "Wh", "exact", 36000, "J"),
        ("commercial", 125.25, "INR", "maximum", 12525, "paise"),
    ],
)
def test_unit_conversions(category, value, unit, operator, expected, si):
    result, actual_unit = normalize(
        RequirementCandidate.model_validate(candidate("x", category, value, unit, operator))
    )
    assert result == pytest.approx(expected) and actual_unit == si


@pytest.mark.parametrize(
    "value,operator",
    [(True, "minimum"), ([30.0, 10.0], "range"), (float("nan"), "exact"), ("25 or 30", "minimum")],
)
def test_schema_rejects_invalid_numeric_values(value, operator):
    with pytest.raises(ValidationError):
        RequirementCandidate.model_validate(candidate("x", value=value, operator=operator))


def test_versioned_configuration_and_graph_export(client, ingested, monkeypatch):
    version, _ = ingested()
    first = start(client, version)
    settings = get_settings().model_copy(update={"llm_model": "fixture-v2"})
    monkeypatch.setattr("app.extraction.api.get_settings", lambda: settings)
    second = start(client, version)
    assert first != second
    execute_run(first, SessionLocal, settings=settings)
    assert run_result(client, first)[0]["error_code"] == "worker_config_mismatch"
    mermaid = build_graph(None, None, settings).get_graph().draw_mermaid()
    assert "retry_extraction" in mermaid and "create_review_items" in mermaid


def test_api_rejects_incomplete_ingestion_and_missing_run(client):
    upload = client.post(
        "/v1/documents", files={"file": ("pending.txt", b"pending", "text/plain")}
    ).json()
    assert (
        client.post(
            f"/v1/document-versions/{upload['document_version_id']}/extractions"
        ).status_code
        == 409
    )
    assert client.get("/v1/extraction-runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/v1/extraction-runs/invalid").status_code == 422


def test_queue_failure_recovers_idempotently(client, ingested):
    from app.main import app
    from app.routes.documents import get_queue

    version, _ = ingested()

    class BrokenQueue:
        def enqueue(self, *args, **kwargs):
            raise ConnectionError("PRIVATE REDIS URI")

    original = app.dependency_overrides[get_queue]
    app.dependency_overrides[get_queue] = BrokenQueue
    endpoint = f"/v1/document-versions/{version}/extractions"
    failure = client.post(endpoint)
    assert failure.status_code == 503 and "PRIVATE" not in failure.text
    app.dependency_overrides[get_queue] = original
    assert client.post(endpoint).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ExtractionRun)) == 1
        assert db.scalar(select(func.count()).select_from(RequirementRecord)) == 0
        assert db.scalar(select(func.count()).select_from(ExtractionNodeRun)) == 0


def test_substring_unit_cannot_change_kilometres_to_metres(client, ingested):
    version, span = ingested()
    run = start(client, version)
    items = clean(span)
    items[0]["original_unit"] = "m"
    model, _ = adapter(response(items))
    execute_run(run, SessionLocal, model)
    assert "unsupported_unit_claim" in run_result(client, run)[2]


def test_multiple_batches_and_oversized_span_preserve_original_anchor(
    client, ingested, monkeypatch
):
    text = "Range minimum 25 km. " + " " * 220 + "Range maximum 10 km."
    version, span = ingested(text)
    settings = get_settings().model_copy(update={"extraction_batch_chars": 100})
    monkeypatch.setattr("app.extraction.api.get_settings", lambda: settings)
    run = start(client, version)
    model, calls = adapter(
        response([candidate(span, quote="Range minimum 25 km.")]),
        response([]),
        response([candidate(span, value=10.0, operator="maximum", quote="Range maximum 10 km.")]),
    )
    execute_run(run, SessionLocal, model, settings)
    status, requirements, codes = run_result(client, run)
    assert len(calls) == 3 and len(requirements) == 2
    assert "conflicting_requirement" in codes and status["state"] == "needs_review"
    anchor = requirements[1]["requirement"]["validated_evidence"][0]
    assert text[anchor["quote_start"] : anchor["quote_end"]] == anchor["quote"]


def test_no_key_fails_with_safe_audited_node(client, ingested, monkeypatch):
    version, _ = ingested()
    settings = get_settings().model_copy(update={"llm_provider": "openai"})
    monkeypatch.setattr("app.extraction.api.get_settings", lambda: settings)
    run = start(client, version)
    execute_run(run, SessionLocal, settings=settings)
    status, _, _ = run_result(client, run)
    assert status["state"] == "failed" and status["error_code"] == "provider_key_missing"
    assert any(
        n["node"] == "extract_requirements" and n["status"] == "failed" for n in status["nodes"]
    )


def test_disabled_repair_does_not_call_model_twice(client, ingested, monkeypatch):
    version, _ = ingested()
    settings = get_settings().model_copy(update={"extraction_max_repairs": 0})
    monkeypatch.setattr("app.extraction.api.get_settings", lambda: settings)
    run = start(client, version)
    model, calls = adapter("bad")
    execute_run(run, SessionLocal, model, settings)
    assert len(calls) == 1 and run_result(client, run)[0]["state"] == "needs_review"


def test_normalized_money_range_stays_integer_paise():
    raw = candidate("span", "commercial", [100.0, 200.0], "INR", "range")
    normalized = ValidatedRequirement(
        **raw, normalized_value=[10000, 20000], normalized_unit="paise", validated_evidence=[]
    )
    assert all(type(value) is int for value in normalized.normalized_value)
    with pytest.raises(ValidationError):
        ValidatedRequirement(
            **raw,
            normalized_value=[10000.1, 20000.2],
            normalized_unit="paise",
            validated_evidence=[],
        )
