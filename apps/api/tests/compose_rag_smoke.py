"""Synthetic PostgreSQL/pgvector/RQ C06 smoke. No paid calls or private documents."""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select, text

from app.analysis.models import GeneratedConfiguration
from app.db import SessionLocal
from app.rag.models import RagJob, RetrievalRun

sys.path.insert(0, "/tmp")
from c05_smoke import extraction, request, wait_analysis


def wait_job(base, path):
    for _ in range(200):
        job = request(base, path)
        if job["state"] in ("completed", "failed", "awaiting_review"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish within smoke budget")


def main():
    base = "http://localhost:8000"
    seed = json.loads(Path("/tmp/c05_seed.json").read_text())
    catalog = request(base, "/v1/catalog/import", seed)
    source = extraction(base, "c06-complete")
    created = request(
        base,
        "/v1/analyses",
        {
            "extraction_run_id": source,
            "catalog_version_id": catalog["catalog_version_id"],
            "analysis_date": "2026-09-25",
        },
    )
    aid = created["id"]
    wait_analysis(base, aid)
    body = {"analysis_id": aid}
    with ThreadPoolExecutor(max_workers=4) as pool:
        indexes = list(pool.map(lambda _: request(base, "/v1/rag/indexes", body), range(4)))
    assert len({r["id"] for r in indexes}) == 1
    idx = indexes[0]["id"]
    index = wait_job(base, f"/v1/rag/indexes/{idx}")
    assert index["state"] == "completed", index
    query = {"index_id": idx, "query": "range payload", "kinds": ["tender", "requirement"]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        searches = list(pool.map(lambda _: request(base, "/v1/rag/search", query), range(4)))
    assert len({s["id"] for s in searches}) == 1
    assert searches[0]["hits"]
    with SessionLocal() as db:
        dimensions = (
            db.execute(
                text("SELECT DISTINCT vector_dims(vector) FROM rag_chunks WHERE index_id=:id"),
                {"id": idx},
            )
            .scalars()
            .all()
        )
        assert dimensions == [256]
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: request(base, "/v1/rag/runs", body), range(4)))
    assert len({r["id"] for r in runs}) == 1
    rid = runs[0]["id"]
    run = wait_job(base, f"/v1/rag/runs/{rid}")
    assert run["state"] == "completed", run
    report = request(base, f"/v1/rag/runs/{rid}/report")
    assert report["status"] == "feasible" and report["approval"] == "not_granted"
    with SessionLocal() as db:
        configs = db.scalars(
            select(GeneratedConfiguration)
            .where(GeneratedConfiguration.analysis_id == aid)
            .order_by(GeneratedConfiguration.rank)
        ).all()
        assert report["configurations"] == [c.payload for c in configs]
        assert db.get(RagJob, rid).attempts == 1
        # API duplicate calls must converge to a single persisted retrieval.
        assert (
            len(db.scalars(select(RetrievalRun).where(RetrievalRun.id == searches[0]["id"])).all())
            == 1
        )
    citations = request(base, f"/v1/rag/runs/{rid}/citations")["citations"]
    assert citations and all(c["analysis_id"] == aid for c in citations)
    nodes = request(base, f"/v1/rag/runs/{rid}/nodes")["nodes"]
    assert nodes and all(n["status"] == "completed" and n["elapsed_ms"] >= 0 for n in nodes)
    assert request(base, "/v1/rag/runs", body)["id"] == rid
    blocked = request(base, "/v1/rag/runs", {**body, "question": "certify flight"})
    pending = wait_job(base, f"/v1/rag/runs/{blocked['id']}")
    assert pending["state"] in ("awaiting_review", "completed"), pending
    request(
        base,
        f"/v1/rag/runs/{blocked['id']}/resume",
        {"decision": "continue_provisional", "reviewer": "synthetic-smoke"},
    )
    resumed = wait_job(base, f"/v1/rag/runs/{blocked['id']}")
    assert resumed["state"] == "completed", resumed
    assert request(base, f"/v1/rag/runs/{blocked['id']}/report")["status"] == "needs_review"
    print(
        json.dumps(
            {
                "index_id": idx,
                "graph_run_id": rid,
                "embedding_dimensions": dimensions[0],
                "chunks": index["count"],
                "concurrent_duplicates": 4,
                "same_identity": True,
                "pgvector_search": "passed",
                "solver_unchanged": True,
                "citations": len(citations),
                "audited_nodes": len(nodes),
                "durable_interrupt_resume": "passed",
                "provider": "fake",
            }
        )
    )


if __name__ == "__main__":
    main()
