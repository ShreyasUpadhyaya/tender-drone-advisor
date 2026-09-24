"""Synthetic regressions for v1 shape failures. No private tender text or identifiers."""

import json
from copy import deepcopy

import pytest
import test_extraction as helpers
from pydantic import ValidationError
from test_extraction import (
    TEXT,
    adapter,
    candidate,
    clean,
    response,
    run_result,
    start,
)

from app.db import SessionLocal
from app.extraction.contracts import RequirementCandidate, WireBatch, WireRequirement
from app.extraction.provider import SchemaFailure, build_adapter
from app.extraction.validation import normalize
from app.extraction.workflow import execute_run
from app.settings import Settings

ingested = helpers.ingested


def wire(value=25, operator="minimum", unit="km"):
    return json.loads(
        response([candidate("synthetic-span", value=value, operator=operator, unit=unit)])
    )["requirements"][0]


@pytest.mark.parametrize(
    "value,expected", [("25", 25000), ("25.5", 25500), (25, 25000), (25.5, 25500)]
)
def test_numeric_strings_preserve_raw_and_normalize(value, expected):
    req = WireRequirement.model_validate(wire(value)).candidate()
    assert req.original_value == value and req.raw_value.value == value
    assert normalize(req) == (expected, "m")
    assert normalize(req) == normalize(req)


@pytest.mark.parametrize("value", ["25 or 30", "25 km", "1,000", "NaN", "1e999", " 25 "])
def test_ambiguous_numeric_is_not_coerced(value):
    with pytest.raises(ValueError):
        WireRequirement.model_validate(wire(value)).candidate()


def test_ranges_text_booleans_unknown_and_nullable_unit():
    assert normalize(WireRequirement.model_validate(wire(["2.5", 25], "range")).candidate()) == (
        [2500, 25000],
        "m",
    )
    for value, operator in [
        ("rotary or fixed wing", "text"),
        ("rotary or fixed wing", "enum"),
        (False, "boolean"),
        ("unknown", "minimum"),
    ]:
        req = WireRequirement.model_validate(wire(value, operator, None)).candidate()
        assert req.raw_unit is None and req.original_value == value
        assert normalize(req)[0] == value
    missing = wire()
    del missing["original_unit"]
    with pytest.raises(ValidationError):
        WireRequirement.model_validate(missing)  # All provider keys required, nullable != omitted.


def test_exact_qualitative_and_enum_list_regressions():
    # The reproduced failure shapes: exact + prose; enum + [str, str].
    for item in [wire("supplier specified", "exact"), wire(["rotary", "fixed"], "enum")]:
        model, _ = adapter(
            json.dumps({"schema_version": "requirements-v2", "requirements": [item]})
        )
        with pytest.raises(SchemaFailure):
            model.extract([], [])
    corrected = WireRequirement.model_validate(wire("rotary or fixed", "enum", None)).candidate()
    assert corrected.original_value == "rotary or fixed"


@pytest.mark.parametrize(
    "change",
    [
        {"operator": "at_least"},
        {"category": "imaginary"},
        {"operator": "range"},
        {"confidence": None},
        {"raw_value": None},
    ],
)
def test_invalid_enums_missing_and_operator_kinds(change):
    item = {**wire(), **change}
    with pytest.raises(ValueError):
        WireRequirement.model_validate(item).candidate()


def test_openai_schema_all_objects_closed_and_required(monkeypatch):
    import langchain_openai

    captured = {}

    class FakeChat:
        def __init__(self, **kwargs):
            pass

        def bind(self, **kwargs):
            from langchain_core.runnables import RunnableLambda

            captured.update(kwargs)
            return RunnableLambda(lambda _: response([]))

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChat)
    build_adapter(Settings(llm_provider="openai", llm_api_key="synthetic-not-a-key"))
    fmt = captured["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema == WireBatch.model_json_schema()

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(schema)


def test_repair_preserves_citations_and_has_errors_and_schema(client, ingested):
    version, span = ingested()
    good = clean(span)
    bad = deepcopy(good)
    bad[0]["original_value"] = "25 or 30"
    model, calls = adapter(response(bad), response(good))
    run = start(client, version)
    execute_run(run, SessionLocal, model)
    status, records, _ = run_result(client, run)
    assert status["state"] == "completed" and len(calls) == 2 and len(records) == 4
    repair = calls[1].to_string()
    assert "raw_value" in repair and "expected" in repair and span in repair
    assert all(r["requirement"]["evidence"] == good[i]["evidence"] for i, r in enumerate(records))
    safe = json.dumps(status)
    assert "25 or 30" not in safe and TEXT not in safe


def test_unresolved_ambiguity_is_explicit_review(client, ingested):
    version, span = ingested()
    model, calls = adapter(response([candidate(span, value="25 or 30")]))
    run = start(client, version)
    execute_run(run, SessionLocal, model)
    status, records, issues = run_result(client, run)
    assert status["state"] == "needs_review" and len(calls) == 2 and not records
    assert {"invalid_model_schema", "repair_exhausted"} <= issues
    paths = [e["path"] for e in status["nodes"][2]["summary"]["schema_errors"]]
    assert any(p[:2] == ["requirements", 0] and "raw_value" in p for p in paths)


def test_unsupported_unit_preserves_other_valid_records(client, ingested):
    version, span = ingested()
    items = clean(span)
    items[0]["original_unit"] = "furlong"
    model, calls = adapter(response(items))
    run = start(client, version)
    execute_run(run, SessionLocal, model)
    status, records, issues = run_result(client, run)
    assert status["state"] == "needs_review" and len(records) == 3 and len(calls) == 1
    assert "unsupported_unit" in issues


def test_historical_v1_requirement_readable():
    record = RequirementCandidate.model_validate(candidate("legacy-span"))
    assert record.raw_value is None and normalize(record) == (25000, "m")


def test_retry_does_not_repeat_successful_batches(client, ingested, monkeypatch):
    from app.settings import get_settings

    version, span = ingested("Range minimum 25 km. " + " " * 120 + "End.")
    settings = get_settings().model_copy(update={"extraction_batch_chars": 100})
    monkeypatch.setattr("app.extraction.api.get_settings", lambda: settings)
    model, calls = adapter(
        response([candidate(span, quote="Range minimum 25 km.")]), "bad", response([])
    )
    run = start(client, version)
    execute_run(run, SessionLocal, model, settings)
    assert len(calls) == 3 and len(run_result(client, run)[1]) == 1
