import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.catalog.models import CatalogVersion
from app.db import SessionLocal
from app.extraction.contracts import ValidatedRequirement
from app.extraction.models import ExtractionRun, RequirementRecord

SEED = json.loads((Path(__file__).parent / "fixtures" / "catalog_seed.json").read_text())


def test_catalog_version_accepts_canonical_json_dates_and_is_idempotent(client):
    payload = {
        "version": "c04-manual-2026-09-24-01",
        "status": "draft",
        "source": "synthetic-demo",
        "effective_from": "2026-09-24",
        "effective_to": "2026-09-24",
    }
    first = client.post("/v1/catalog/versions", json=payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["version"] == "c04-manual-2026-09-24-01"
    assert body["status"] == "draft"
    assert body["source"] == "synthetic-demo"
    assert body["effective_from"] == "2026-09-24"
    assert body["effective_to"] == "2026-09-24"
    assert body["idempotent"] is False
    repeated = client.post("/v1/catalog/versions", json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["idempotent"] is True
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(CatalogVersion)
                .where(CatalogVersion.version == payload["version"])
            )
            == 1
        )


def test_catalog_version_json_dates_allow_null_and_reject_noncanonical_or_reversed_values(client):
    nullable = client.post(
        "/v1/catalog/versions",
        json={
            "version": "null-dates-v1",
            "effective_from": None,
            "effective_to": None,
        },
    )
    assert nullable.status_code == 201
    assert nullable.json()["effective_from"] is None
    assert nullable.json()["effective_to"] is None

    bad_cases = [
        ("bad-format-v1", "24-09-2026", None),
        ("datetime-v1", "2026-09-24T00:00:00Z", None),
        ("impossible-v1", "2026-02-30", None),
        ("reversed-v1", "2026-09-25", "2026-09-24"),
    ]
    for version, effective_from, effective_to in bad_cases:
        response = client.post(
            "/v1/catalog/versions",
            json={
                "version": version,
                "effective_from": effective_from,
                "effective_to": effective_to,
            },
        )
        assert response.status_code == 422, response.text


def test_catalog_import_price_dates_use_same_json_contract(client):
    payload = deepcopy(SEED)
    payload["idempotency_key"] = "iso-price-date-contract-v1"
    payload["version"]["version"] = "iso-price-date-contract-v1"
    payload["version"]["effective_from"] = "2026-09-24"
    payload["version"]["effective_to"] = None
    for item in payload["items"]:
        item["catalog_version"] = payload["version"]["version"]
    payload["prices"][0]["effective_from"] = "2026-01-01"
    payload["prices"][0]["effective_to"] = None
    assert client.post("/v1/catalog/import", json=payload).status_code == 201

    for suffix, effective_from, effective_to in [
        ("format", "01/01/2026", None),
        ("reversed", "2026-12-31", "2026-01-01"),
    ]:
        invalid = deepcopy(payload)
        invalid["version"]["version"] = f"invalid-price-date-{suffix}-v1"
        invalid["idempotency_key"] = f"invalid-price-date-{suffix}-contract-v1"
        for item in invalid["items"]:
            item["catalog_version"] = invalid["version"]["version"]
            item["item_version"] = 2
        for price in invalid["prices"]:
            price["item_version"] = 2
            price["effective_from"] = effective_from
            price["effective_to"] = effective_to
        assert client.post("/v1/catalog/import", json=invalid).status_code == 422


def test_catalog_openapi_exposes_typed_success_responses(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path, method, status in [
        ("/v1/catalog/versions", "post", "201"),
        ("/v1/catalog/versions", "get", "200"),
        ("/v1/catalog/versions/current", "get", "200"),
        ("/v1/catalog/retrieve", "post", "200"),
        ("/v1/catalog/retrieve-from-run/{run_id}", "post", "200"),
        ("/v1/catalog/compatibility-rules", "get", "200"),
        ("/v1/catalog/suppliers", "get", "200"),
        ("/v1/catalog/prices", "get", "200"),
    ]:
        response_schema = paths[path][method]["responses"][status]["content"]["application/json"][
            "schema"
        ]
        assert "$ref" in response_schema or "items" in response_schema
        assert response_schema.get("additionalProperties") is not True


def test_catalog_import_is_idempotent_and_filters_candidates(client):
    first = client.post("/v1/catalog/import", json=SEED)
    assert first.status_code == 201, first.text
    assert first.json()["rule_count"] == 1
    second = client.post("/v1/catalog/import", json=SEED)
    assert second.status_code == 201
    current = client.get("/v1/catalog/versions/current")
    assert current.status_code == 200
    assert current.json()["version"] == "demo-2026-09"
    items = client.get("/v1/catalog/items?category=camera").json()
    assert [item["sku"] for item in items] == ["SYN-CAM-RGB", "SYN-CAM-THERMAL"]
    result = client.post(
        "/v1/catalog/retrieve",
        json={
            "requirements": [
                {
                    "category": "payload",
                    "attribute": "payload_kg",
                    "operator": "maximum",
                    "normalized_value": 0.5,
                    "normalized_unit": "kg",
                    "semantics": "mandatory",
                }
            ],
            "category": "camera",
        },
    )
    assert result.status_code == 200
    assert result.json()["candidates"][0]["item"]["sku"] == "SYN-CAM-RGB"
    assert any(not candidate["eligible"] for candidate in result.json()["candidates"])
    assert len(client.get("/v1/catalog/prices").json()) == 2
    assert len(client.get("/v1/catalog/prices").json()) == 2
    assert client.get("/v1/catalog/suppliers").json()
    assert client.get("/v1/catalog/compatibility-rules").json()[0]["rule_type"] == "incompatible"
    all_items = client.get("/v1/catalog/items?limit=100").json()
    assert any(item["availability"] == "unavailable" for item in all_items)
    assert any(item["lifecycle_status"] == "deprecated" for item in all_items)


def test_catalog_versions_list_exposes_selectable_historical_snapshots(client):
    assert client.post("/v1/catalog/import", json=SEED).status_code == 201
    response = client.post(
        "/v1/catalog/versions",
        json={"version": "c05-solver-demo-v1", "status": "draft", "source": "synthetic-demo"},
    )
    assert response.status_code == 201
    versions = client.get("/v1/catalog/versions")
    assert versions.status_code == 200
    assert [row["version"] for row in versions.json()] == [
        "c05-solver-demo-v1",
        "demo-2026-09",
    ]
    assert all("id" in row and row["status"] in {"current", "draft"} for row in versions.json())


def test_compatibility_availability_and_overspec_explanations(client):
    assert client.post("/v1/catalog/import", json=SEED).status_code == 201
    incompatible = client.post(
        "/v1/catalog/retrieve",
        json={
            "category": "esc",
            "context_skus": ["SYN-MOTOR-40"],
            "requirements": [
                {
                    "category": "propulsion",
                    "attribute": "max_current_a",
                    "operator": "minimum",
                    "normalized_value": 10,
                    "normalized_unit": "A",
                }
            ],
        },
    ).json()["candidates"]
    undersized = next(row for row in incompatible if row["item"]["sku"] == "SYN-ESC-12")
    assert not undersized["eligible"] and any(
        "compatibility: incompatible" in x for x in undersized["breakdown"]
    )
    overspec = client.post(
        "/v1/catalog/retrieve",
        json={
            "category": "camera",
            "requirements": [
                {
                    "category": "payload",
                    "attribute": "payload_kg",
                    "operator": "minimum",
                    "normalized_value": 0.2,
                    "normalized_unit": "kg",
                }
            ],
        },
    ).json()["candidates"]
    assert all(row["eligible"] for row in overspec)
    assert all(row["penalties"] for row in overspec)
    unavailable = client.post(
        "/v1/catalog/retrieve",
        json={
            "category": "battery",
            "include_unavailable": True,
            "requirements": [
                {
                    "category": "battery",
                    "attribute": "capacity_c",
                    "operator": "minimum",
                    "normalized_value": 1,
                    "normalized_unit": "C",
                }
            ],
        },
    ).json()["candidates"]
    unavailable_row = next(row for row in unavailable if row["item"]["sku"] == "SYN-BAT-6S-40")
    assert not unavailable_row["eligible"] and any(
        "not immediately buildable" in x for x in unavailable_row["breakdown"]
    )


def test_catalog_rejects_invalid_units_and_negative_values(client):
    payload = {
        "catalog_version": "demo-2026-09",
        "sku": "BAD",
        "manufacturer": "Synthetic",
        "category": "battery",
        "name": "Bad",
        "description": "bad",
        "weight_grams": -1,
        "cost_paise": 1,
        "inventory_qty": 1,
        "lead_time_days": 1,
        "specs": {"capacity_unit": "litre"},
    }
    assert client.post("/v1/catalog/items", json=payload).status_code == 422


def test_catalog_version_update_preserves_old_record(client):
    version = client.post("/v1/catalog/versions", json={"version": "v2", "status": "current"})
    assert version.status_code == 201
    item = {
        "catalog_version": "v2",
        "sku": "SYN-V2",
        "manufacturer": "Synthetic",
        "category": "airframe",
        "name": "V2",
        "description": "versioned",
        "weight_grams": 100,
        "cost_paise": 100,
        "inventory_qty": 1,
        "lead_time_days": 1,
    }
    assert client.post("/v1/catalog/items", json=item).status_code == 201
    item["item_version"] = 2
    item["name"] = "V2 revised"
    assert client.post("/v1/catalog/items", json=item).status_code == 201
    assert len(client.get("/v1/catalog/items?catalog_version=v2").json()) == 2


def test_retrieval_from_c03_run_preserves_review_and_evidence(client):
    assert client.post("/v1/catalog/import", json=SEED).status_code == 201
    span_id = str(uuid4())
    requirement = ValidatedRequirement(
        category="range",
        attribute="range_m",
        semantics="mandatory",
        operator="minimum",
        original_value=40000,
        original_unit="m",
        confidence=0.95,
        evidence=[{"span_id": span_id, "quote": "Synthetic cited range 40000 m"}],
        normalized_value=40000,
        normalized_unit="m",
        validated_evidence=[
            {
                "document_id": str(uuid4()),
                "document_version_id": str(uuid4()),
                "span_id": span_id,
                "page_number": 1,
                "section_name": None,
                "chunk_index": 0,
                "quote": "Synthetic cited range 40000 m",
                "quote_start": 0,
                "quote_end": 29,
            }
        ],
    )
    with SessionLocal() as db:
        run = ExtractionRun(
            document_version_id=str(uuid4()),
            idempotency_key="c04-run-" + uuid4().hex,
            schema_version="requirements-v2",
            prompt_version="fixture",
            model_config={},
            state="needs_review",
            review_state="pending",
        )
        db.add(run)
        db.flush()
        record = RequirementRecord(
            run_id=run.id, ordinal=0, payload=requirement.model_dump(mode="json")
        )
        db.add(record)
        db.commit()
        run_id, record_id = run.id, record.id
    result = client.post(f"/v1/catalog/retrieve-from-run/{run_id}").json()
    assert result["review_required"] is True
    assert result["missing_critical_categories"] == ["endurance", "payload", "platform"]
    assert any(record_id in entry for row in result["candidates"] for entry in row["breakdown"])
    assert any(span_id in entry for row in result["candidates"] for entry in row["breakdown"])
    with SessionLocal() as db:
        db.get(ExtractionRun, run_id).schema_version = "requirements-v1"
        db.commit()
    assert client.post(f"/v1/catalog/retrieve-from-run/{run_id}").status_code == 409
