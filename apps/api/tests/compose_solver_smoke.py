"""Synthetic C05 HTTP/RQ/PostgreSQL release smoke. No paid provider calls.
Copy alongside solver_catalog.json into the API container; run with --seed path.
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from urllib.request import Request, urlopen

from langchain_core.runnables import RunnableLambda
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.analysis.models import AnalysisRun, AnalysisSnapshot, GeneratedConfiguration
from app.analysis.service import execute_analysis
from app.db import SessionLocal
from app.extraction.contracts import PROMPT_VERSION, SCHEMA_VERSION
from app.extraction.models import ExtractionRun
from app.extraction.provider import ModelAdapter, model_snapshot
from app.extraction.workflow import execute_run
from app.models import SourceSpan
from app.settings import get_settings
from app.solver.contracts import Snapshot
from app.solver.orchestration import fingerprint, solve


def request(base, path, data=None, content_type="application/json"):
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
    with urlopen(
        Request(base + path, data=data, headers={"Content-Type": content_type}), timeout=60
    ) as result:
        return json.load(result)


def extraction(base, scenario, range_km=25, missing=False):
    source_text = f"Synthetic C05 {scenario}. Platform: multirotor. Range >= {range_km} km. Endurance >= 30 min. Payload >= 2 kg."
    boundary = "c05-synthetic-fixture"
    data = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="c05-synthetic.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\n{source_text}\r\n--{boundary}--\r\n"
    ).encode()
    uploaded = request(base, "/v1/documents", data, f"multipart/form-data; boundary={boundary}")
    for _ in range(100):
        document = request(base, f"/v1/documents/{uploaded['document_id']}")
        if document["state"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert document["state"] == "completed"
    settings = get_settings().model_copy(
        update={"llm_provider": "fake", "llm_model": "c05-smoke-v1"}
    )
    model_config = model_snapshot(settings)
    key = fingerprint(
        [document["document_version_id"], SCHEMA_VERSION, PROMPT_VERSION, model_config]
    )
    with SessionLocal() as db:
        span = db.scalar(
            select(SourceSpan).where(
                SourceSpan.document_version_id == document["document_version_id"]
            )
        )
        span_id = span.id
        run = db.scalar(select(ExtractionRun).where(ExtractionRun.idempotency_key == key))
        if run is None:
            run = ExtractionRun(
                document_version_id=span.document_version_id,
                idempotency_key=key,
                schema_version=SCHEMA_VERSION,
                prompt_version=PROMPT_VERSION,
                model_config=model_config,
            )
            db.add(run)
            db.commit()
        run_id = run.id
    values = [
        ("platform", "multirotor", "", "enum"),
        ("range", range_km, "km", "minimum"),
        ("endurance", 30, "min", "minimum"),
        ("payload", 2, "kg", "minimum"),
    ]
    if missing:
        values = values[:2]
    output = {
        "schema_version": SCHEMA_VERSION,
        "requirements": [
            {
                "category": cat,
                "attribute": cat,
                "semantics": "mandatory",
                "operator": op,
                "raw_value": {"kind": "text" if op == "enum" else "scalar", "value": value},
                "original_unit": unit,
                "confidence": 0.95,
                "evidence": [{"span_id": span_id, "quote": source_text}],
            }
            for cat, value, unit, op in values
        ],
    }
    execute_run(
        run_id, SessionLocal, ModelAdapter(RunnableLambda(lambda _: json.dumps(output))), settings
    )
    return run_id


def wait_analysis(base, analysis_id):
    for _ in range(150):
        row = request(base, f"/v1/analyses/{analysis_id}")
        if row["state"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert row["state"] == "completed", row
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True)
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()
    seed = json.loads(Path(args.seed).read_text())
    imported = request(args.base, "/v1/catalog/import", seed)
    again = request(args.base, "/v1/catalog/import", seed)
    assert imported["item_ids"] == again["item_ids"]
    run_id = extraction(args.base, "complete")
    body = {
        "extraction_run_id": run_id,
        "catalog_version_id": imported["catalog_version_id"],
        "analysis_date": "2026-09-25",
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        starts = list(pool.map(lambda _: request(args.base, "/v1/analyses", body), range(4)))
    assert len({r["id"] for r in starts}) == 1
    analysis_id = starts[0]["id"]
    summary = wait_analysis(args.base, analysis_id)
    assert summary["status"] == "feasible"
    assert summary["attempts"] == 1
    configs = request(args.base, f"/v1/analyses/{analysis_id}/configurations?limit=50")[
        "configurations"
    ]
    valid = [c for c in configs if c["status"] == "feasible"]
    cheapest = next(c for c in valid if "lowest_cost" in c["labels"])
    assert "S5-ESC-VALID" in [s["item"]["sku"] for s in cheapest["selections"]]
    assert all("S5-ESC-CHEAP" not in [s["item"]["sku"] for s in c["selections"]] for c in valid)
    assert "explicit_incompatibility" in {
        i["code"] for r in summary["summary"]["rejected"] for i in r["issues"]
    }
    over = next(
        c
        for c in valid
        if any(s["item"]["sku"] == "S5-ESC-OVER" for s in c["selections"])
        and any(s["item"]["sku"] == "S5-BATTERY" for s in c["selections"])
    )
    assert over["total_weight_g"] > cheapest["total_weight_g"]
    assert over["cost"]["total_paise"] > cheapest["cost"]["total_paise"]
    assert over["objective_breakdown"]["overspec"] > 0
    assert all(
        c["immediately_buildable"] is False
        for c in configs
        if any(s["item"]["sku"].endswith("UNAVAILABLE") for s in c["selections"])
    )
    for c in configs:
        bom = request(args.base, f"/v1/analyses/{analysis_id}/configurations/{c['id']}/bom")
        matrix = request(
            args.base, f"/v1/analyses/{analysis_id}/configurations/{c['id']}/requirements"
        )
        assert all(l["item_id"] and l["price_id"] and l["item_version"] for l in bom["lines"])
        assert all(
            e["requirement"]["validated_evidence"] and e["extraction_run_id"] == run_id
            for e in matrix["evaluations"]
        )
    repeated = request(args.base, "/v1/analyses", body)
    assert repeated["id"] == analysis_id and repeated["idempotent"]
    execute_analysis(analysis_id)  # Terminal queue replay must not alter output.
    with SessionLocal() as db:
        row = db.get(AnalysisRun, analysis_id)
        assert row.attempts == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AnalysisRun)
                .where(AnalysisRun.identity == row.identity)
            )
            == 1
        )
        saved = db.get(AnalysisSnapshot, analysis_id).payload
        replay = solve(Snapshot.model_validate(saved))
        stored = db.scalars(
            select(GeneratedConfiguration)
            .where(GeneratedConfiguration.analysis_id == analysis_id)
            .order_by(GeneratedConfiguration.rank)
        ).all()
        assert [c.model_dump(mode="json") for c in replay.configurations] == [
            c.payload for c in stored
        ]
        try:
            db.execute(
                text("UPDATE analysis_runs SET state='queued' WHERE id=:id"), {"id": analysis_id}
            )
            db.commit()
            raise AssertionError("completed history was mutable")
        except DBAPIError:
            db.rollback()
        assert db.get(AnalysisRun, analysis_id).state == "completed"
    scenarios = {}
    for name, range_km, missing in [("impossible", 100, False), ("incomplete", 25, True)]:
        source = extraction(args.base, name, range_km, missing)
        request_body = {**body, "extraction_run_id": source}
        started = request(args.base, "/v1/analyses", request_body)
        result = wait_analysis(args.base, started["id"])
        expected = "needs_review" if missing else "infeasible"
        assert result["status"] == expected, result
        scenarios[name] = result["status"]
    alternate = {**body, "policy": {"weights": {"cost": 0, "weight": 100}}}
    changed = request(args.base, "/v1/analyses", alternate)
    assert changed["id"] != analysis_id
    wait_analysis(args.base, changed["id"])
    # New immutable catalog membership, with identical synthetic parts at version 2.
    new_seed = deepcopy(seed)
    new_seed["version"]["version"] = "synthetic-c05-v2"
    new_seed["idempotency_key"] = "synthetic-c05-catalog-v2"
    for item in new_seed["items"]:
        item["catalog_version"], item["item_version"] = "synthetic-c05-v2", 2
        if "assembly" in item["specs"]:
            for slot in item["specs"]["assembly"]["slots"]:
                for option in slot["options"]:
                    option["item_version"] = 2
                slot["baseline"]["item_version"] = 2
    for price in new_seed["prices"]:
        price["item_version"] = 2
    for rule in new_seed["compatibility_rules"]:
        rule["from_version"] = rule["to_version"] = 2
    new_catalog = request(args.base, "/v1/catalog/import", new_seed)
    new_body = {**body, "catalog_version_id": new_catalog["catalog_version_id"]}
    new_analysis = request(args.base, "/v1/analyses", new_body)
    assert new_analysis["id"] != analysis_id
    wait_analysis(args.base, new_analysis["id"])
    assert (
        request(args.base, f"/v1/analyses/{analysis_id}/configurations?limit=50")["configurations"]
        == configs
    )
    print(
        json.dumps(
            {
                "analysis_id": analysis_id,
                "status": summary["status"],
                "worker_attempts": summary["attempts"],
                "concurrent_requests": len(starts),
                "same_identity": True,
                "replay_identical": True,
                "valid_configurations": len(valid),
                "cheapest_material_paise": cheapest["cost"]["material_paise"],
                "cheapest_total_paise": cheapest["cost"]["total_paise"],
                "mass_g": cheapest["total_weight_g"],
                "overspec_extra_weight_g": over["total_weight_g"] - cheapest["total_weight_g"],
                "overspec_extra_cost_paise": over["cost"]["total_paise"]
                - cheapest["cost"]["total_paise"],
                "traceability": "all BOM lines and requirement decisions",
                "terminal_update_blocked": True,
                "policy_changes_identity": True,
                "catalog_changes_identity": True,
                **scenarios,
            }
        )
    )


if __name__ == "__main__":
    main()
