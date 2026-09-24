"""Synthetic PostgreSQL/MinIO integration smoke; pipe into docker compose exec -T api python -."""

import hashlib
import json
import time
from urllib.request import Request, urlopen

from langchain_core.runnables import RunnableLambda
from sqlalchemy import func, select

from app.db import SessionLocal
from app.extraction.contracts import PROMPT_VERSION, SCHEMA_VERSION
from app.extraction.models import EvidenceLink, ExtractionRun, RequirementRecord
from app.extraction.provider import ModelAdapter, model_snapshot
from app.extraction.workflow import execute_run
from app.models import SourceSpan
from app.settings import get_settings

BASE = "http://localhost:8000"
TEXT = "Platform: multirotor. Range >= 25 km. Endurance >= 30 min. Payload >= 2 kg."


def request(path, body=None, content_type="application/json", method=None):
    with urlopen(
        Request(BASE + path, data=body, headers={"Content-Type": content_type}, method=method),
        timeout=30,
    ) as result:
        return json.load(result)


def main():
    boundary = "tda-c03-synthetic-smoke"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="c03-synthetic.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\n{TEXT}\r\n--{boundary}--\r\n"
    ).encode()
    uploaded = request("/v1/documents", body, f"multipart/form-data; boundary={boundary}")
    for _ in range(60):
        status = request(f"/v1/documents/{uploaded['document_id']}")
        if status["state"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert status["state"] == "completed", status["state"]
    version = status["document_version_id"]
    settings = get_settings().model_copy(
        update={"llm_provider": "fake", "llm_model": "scripted-smoke-v1"}
    )
    snapshot = model_snapshot(settings)
    identity = json.dumps([version, SCHEMA_VERSION, PROMPT_VERSION, snapshot], sort_keys=True)
    key = hashlib.sha256(identity.encode()).hexdigest()
    with SessionLocal() as db:
        source = db.scalar(select(SourceSpan).where(SourceSpan.document_version_id == version))
        span_id = source.id
        run = db.scalar(select(ExtractionRun).where(ExtractionRun.idempotency_key == key))
        if run is None:
            run = ExtractionRun(
                document_version_id=version,
                idempotency_key=key,
                schema_version=SCHEMA_VERSION,
                prompt_version=PROMPT_VERSION,
                model_config=snapshot,
            )
            db.add(run)
            db.commit()
        run_id = run.id
    requirements = []
    for category, value, unit, operator in [
        ("platform", "multirotor", "", "enum"),
        ("range", 25.0, "km", "minimum"),
        ("endurance", 30.0, "min", "minimum"),
        ("payload", 2.0, "kg", "minimum"),
    ]:
        requirements.append(
            {
                "category": category,
                "attribute": category,
                "semantics": "mandatory",
                "operator": operator,
                "raw_value": {"kind": "text" if operator == "enum" else "scalar", "value": value},
                "original_unit": unit,
                "confidence": 0.95,
                "evidence": [{"span_id": span_id, "quote": TEXT}],
            }
        )
    raw = json.dumps({"schema_version": SCHEMA_VERSION, "requirements": requirements})
    adapter = ModelAdapter(RunnableLambda(lambda _: raw))
    execute_run(run_id, SessionLocal, adapter, settings)
    status = request(f"/v1/extraction-runs/{run_id}")
    output = request(f"/v1/extraction-runs/{run_id}/requirements")
    assert status["state"] == "completed" and len(output) == 4
    assert output[1]["requirement"]["normalized_value"] == 25000
    assert all(
        item["requirement"]["validated_evidence"][0]["span_id"] == span_id for item in output
    )
    assert all(node["status"] == "completed" for node in status["nodes"])
    with SessionLocal() as db:
        evidence_count = db.scalar(
            select(func.count())
            .select_from(EvidenceLink)
            .join(RequirementRecord)
            .where(RequirementRecord.run_id == run_id)
        )
        assert evidence_count == 4
    print(
        json.dumps(
            {
                "scripted_run_id": run_id,
                "state": status["state"],
                "requirements": len(output),
                "evidence_links": evidence_count,
                "critical_citation_coverage": 1.0,
                "nodes": len(status["nodes"]),
            }
        )
    )


if __name__ == "__main__":
    main()
