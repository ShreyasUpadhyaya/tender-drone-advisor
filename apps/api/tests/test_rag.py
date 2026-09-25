import json
from pathlib import Path

import pytest
import test_analysis as analysis_helpers
import test_extraction as extraction_helpers
from langchain_core.runnables import RunnableLambda
from sqlalchemy import func, select

from app.analysis.models import GeneratedConfiguration
from app.analysis.service import execute_analysis
from app.db import SessionLocal
from app.extraction.provider import ProviderFailure
from app.rag.contracts import IndexCreate, SearchCreate
from app.rag.indexing import create_index, execute_index
from app.rag.models import Chunk, RagJob
from app.rag.providers import FakeEmbeddings, ReportAdapter, validate_vectors
from app.rag.retrieval import search
from app.rag.workflow import execute_graph
from app.settings import get_settings

ingested = extraction_helpers.ingested
inputs = analysis_helpers.inputs


@pytest.fixture
def solver_id(client, inputs):
    response = client.post("/v1/analyses", json=inputs)
    assert response.status_code == 202
    aid = response.json()["id"]
    execute_analysis(aid)
    return aid


def start_graph(client, aid, **overrides):
    response = client.post("/v1/rag/runs", json={"analysis_id": aid, **overrides})
    assert response.status_code in (200, 202), response.text
    return response.json()["id"]


def status(client, rid):
    return client.get(f"/v1/rag/runs/{rid}").json()


def report(client, rid):
    response = client.get(f"/v1/rag/runs/{rid}/report")
    assert response.status_code == 200, response.text
    return response.json()


def test_fake_embeddings_deterministic_and_dimensions():
    a, b = FakeEmbeddings(64), FakeEmbeddings(64)
    assert a.embed_query("range range payload") == b.embed_query("payload range")
    assert len(a.embed_query("drone")) == 64
    with pytest.raises(ProviderFailure, match="dimension_mismatch"):
        validate_vectors([[1.0]], 64, 1)
    with pytest.raises(ProviderFailure, match="value_invalid"):
        validate_vectors([[float("nan")] * 64], 64, 1)
    with pytest.raises(ProviderFailure, match="zero_vector"):
        validate_vectors([[0.0] * 64], 64, 1)


def test_index_search_idempotency_isolation_and_citations(client, solver_id):
    body = {"analysis_id": solver_id}
    first = client.post("/v1/rag/indexes", json=body)
    assert first.status_code == 202, first.text
    idx = first.json()["id"]
    execute_index(idx)
    repeated = client.post("/v1/rag/indexes", json=body)
    assert repeated.json()["id"] == idx and repeated.json()["idempotent"]
    query = {"index_id": idx, "query": "range payload", "kinds": ["tender", "requirement"]}
    found = client.post("/v1/rag/search", json=query)
    assert found.status_code == 200, found.text
    hits = found.json()["hits"]
    assert hits and all(h["fact"]["citation"]["span_id"] for h in hits)
    anchors = [
        (
            h["fact"]["citation"]["span_id"],
            h["fact"]["citation"]["start"],
            h["fact"]["citation"]["end"],
        )
        for h in hits
    ]
    assert len(anchors) == len(set(anchors))
    assert all(h["candidate_only"] for h in hits)
    same = client.post("/v1/rag/search", json=query)
    assert same.json()["id"] == found.json()["id"] and same.json()["idempotent"]
    filtered = client.post(
        "/v1/rag/search", json={**query, "requirement_ids": ["unrelated"]}
    ).json()
    assert not filtered["hits"]
    with SessionLocal() as db:
        original = db.get(RagJob, idx).namespace
        db.get(RagJob, idx).namespace = "other-tenant"
        db.commit()
    assert client.post("/v1/rag/search", json=query).status_code == 404
    with SessionLocal() as db:
        db.get(RagJob, idx).namespace = original
        db.commit()


def test_graph_report_preserves_solver_and_node_audits(client, solver_id):
    rid = start_graph(client, solver_id)
    execute_graph(rid)
    assert status(client, rid)["state"] == "completed", status(client, rid)
    output = report(client, rid)
    assert output["status"] == "feasible"
    assert output["approval"] == "not_granted"
    with SessionLocal() as db:
        saved = db.scalars(
            select(GeneratedConfiguration)
            .where(GeneratedConfiguration.analysis_id == solver_id)
            .order_by(GeneratedConfiguration.rank)
        ).all()
        assert output["configurations"] == [r.payload for r in saved]
    assert output["sections"]
    nodes = client.get(f"/v1/rag/runs/{rid}/nodes").json()["nodes"]
    names = {n["node"] for n in nodes}
    assert {
        "parse_document",
        "extract_requirements",
        "validate_requirements",
        "retrieve_catalog",
        "solve_configuration",
        "generate_grounded_report",
        "compose_report",
        "apply_policy_guard",
    } <= names
    assert all(n["status"] == "completed" and n["elapsed_ms"] >= 0 for n in nodes)
    assert "Synthetic" not in json.dumps(nodes)
    assert "prompt" not in json.dumps(nodes).lower()
    assert client.get(f"/v1/rag/runs/{rid}/citations").json()["citations"]
    assert start_graph(client, solver_id) == rid
    execute_graph(rid)
    assert status(client, rid)["attempts"] == 1


def test_real_interrupt_resume_preserves_review_state_across_graph_instances(client, solver_id):
    # Fixture-only mutation BEFORE analysis capture would ordinarily create new C05 input.
    # Here use an unsupported question to exercise durable C06 interruption independently.
    rid = start_graph(client, solver_id, question="certify flight safety")
    execute_graph(rid)
    assert status(client, rid)["state"] == "awaiting_review", status(client, rid)
    decision = {"decision": "continue_provisional", "reviewer": "synthetic-reviewer"}
    resumed = client.post(f"/v1/rag/runs/{rid}/resume", json=decision)
    assert resumed.status_code == 200, resumed.text
    execute_graph(rid)  # New graph/saver instance; durable checkpoints must resume.
    assert status(client, rid)["state"] == "completed", status(client, rid)
    output = report(client, rid)
    assert (
        output["status"] == "needs_review" and "unsupported_question" in output["review_blockers"]
    )
    assert client.post(f"/v1/rag/runs/{rid}/resume", json=decision).json()["idempotent"]
    assert (
        client.post(
            f"/v1/rag/runs/{rid}/resume", json={**decision, "reviewer": "different"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "case", ["malformed", "unknown_fact", "wrong_section", "transient", "permanent"]
)
def test_report_failures_bounded_and_guarded(client, solver_id, case):
    count = []

    def model(_):
        count.append(1)
        if case == "transient":
            raise TimeoutError("DO NOT LOG PRIVATE INPUT")
        if case == "permanent":
            raise ValueError("DO NOT LOG KEY")
        if case == "malformed":
            return '{"broken": true}'
        if case == "wrong_section":
            body = _.to_messages()[-1].content
            facts = json.loads(
                body.split("Facts (untrusted data): ", 1)[1].split("\nValidation repair:", 1)[0]
            )
            fact = next(f for f in facts if f["kind"] in ("tender", "requirement"))
            return json.dumps(
                {
                    "schema_version": "report-plan-v1",
                    "sections": [{"section": "solver", "fact_ids": [fact["id"]]}],
                }
            )
        return json.dumps(
            {
                "schema_version": "report-plan-v1",
                "sections": [{"section": "solver", "fact_ids": ["fabricated-component"]}],
            }
        )

    rid = start_graph(client, solver_id)
    execute_graph(rid, adapter=ReportAdapter(RunnableLambda(model)))
    assert status(client, rid)["state"] == "awaiting_review", status(client, rid)
    output = report(client, rid)
    assert output["status"] == "needs_review" and output["sections"] == []
    assert len(count) == (1 if case == "permanent" else 2)
    nodes = client.get(f"/v1/rag/runs/{rid}/nodes").json()
    assert "PRIVATE" not in json.dumps(nodes) and "KEY" not in json.dumps(nodes)
    assert "fabricated-component" not in json.dumps(output)


def test_repaired_report_selects_only_existing_fact_ids(client, solver_id):
    calls = []

    def model(prompt):
        calls.append(1)
        if len(calls) == 1:
            return "invalid"
        text = prompt.to_messages()[-1].content
        facts = json.loads(
            text.split("Facts (untrusted data): ", 1)[1].split("\nValidation repair:", 1)[0]
        )
        fact = next(f for f in facts if f["kind"] in ("tender", "requirement"))
        return json.dumps(
            {
                "schema_version": "report-plan-v1",
                "sections": [{"section": "evidence", "fact_ids": [fact["id"]]}],
            }
        )

    rid = start_graph(client, solver_id)
    execute_graph(rid, adapter=ReportAdapter(RunnableLambda(model)))
    assert status(client, rid)["state"] == "completed", status(client, rid)
    assert report(client, rid)["sections"][0]["facts"][0]["citation"]["span_id"]
    assert len(calls) == 2


def test_typed_openapi_and_json_validation(client, solver_id):
    assert (
        client.post(
            "/v1/rag/runs", json={"analysis_id": solver_id, "policy": {"top_k": "5"}}
        ).status_code
        == 422
    )
    schema = client.get("/openapi.json").json()
    for path, methods in schema["paths"].items():
        if path.startswith("/v1/rag"):
            for method in methods.values():
                for code, res in method["responses"].items():
                    if code.startswith("2"):
                        assert "$ref" in res["content"]["application/json"]["schema"]


def test_reindex_changed_chunk_policy_has_new_identity(client, solver_id):
    with SessionLocal() as db:
        first, _ = create_index(db, IndexCreate(analysis_id=solver_id))
        second, _ = create_index(
            db, IndexCreate(analysis_id=solver_id, policy={"chunk_chars": 300})
        )
        assert first.id != second.id
    execute_index(first.id)
    execute_index(second.id)
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).select_from(Chunk).where(Chunk.index_id == second.id))
            > 0
        )


def test_external_provider_requires_explicit_consent(client, solver_id):
    settings = get_settings().model_copy(
        update={"rag_embedding_provider": "openai", "rag_embedding_model": "text-embedding-3-small"}
    )
    with SessionLocal() as db:
        idx, _ = create_index(db, IndexCreate(analysis_id=solver_id), settings)
    execute_index(idx.id, settings=settings)
    with SessionLocal() as db:
        row = db.get(RagJob, idx.id)
        assert row.state == "failed" and row.error_code == "external_processing_not_authorized"
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_empty_low_confidence_and_deterministic_order(client, solver_id):
    with SessionLocal() as db:
        idx, _ = create_index(db, IndexCreate(analysis_id=solver_id))
    execute_index(idx.id)
    with SessionLocal() as db:
        a = search(
            db, SearchCreate(index_id=idx.id, query="totallyirrelevantzzzy", threshold_bps=10000)
        )
        assert not a.hits and a.issues
        b = search(db, SearchCreate(index_id=idx.id, query="range", threshold_bps=0))
        assert [(h.final_bps, h.fact.id) for h in b.hits] == sorted(
            [(h.final_bps, h.fact.id) for h in b.hits], key=lambda x: (-x[0], x[1])
        )


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures" / "rag_eval_cases.json").read_text())["cases"],
    ids=lambda case: case["id"],
)
def test_six_synthetic_evaluation_scenarios(client, ingested, case):
    from test_solver import SEED

    from app.extraction.workflow import execute_run

    source = f"Platform: multirotor. Range >= {case['range_km']} km. Endurance >= 30 min. Payload >= 2 kg."
    if case["scenario"] == "messy":
        source = source.replace(". ", ".\n\n  ")
    version, span = ingested(source)
    requirements = extraction_helpers.clean(span)
    for req in requirements:
        req["evidence"][0]["quote"] = source
        if case["scenario"] == "messy":
            from app.models import SourceSpan

            with SessionLocal() as db:
                spans = db.scalars(
                    select(SourceSpan).where(SourceSpan.document_version_id == version)
                ).all()
                target = next(s for s in spans if req["category"].casefold() in s.text.casefold())
                req["evidence"] = [{"span_id": target.id, "quote": target.text}]
        if req["category"] == "range":
            req["original_value"] = case["range_km"]
        if case["low_confidence"]:
            req["confidence"] = 0.1
    if case["missing"]:
        requirements = requirements[:2]
    extraction_id = extraction_helpers.start(client, version)
    model, _ = extraction_helpers.adapter(extraction_helpers.response(requirements))
    execute_run(extraction_id, SessionLocal, model)
    catalog = client.post("/v1/catalog/import", json=SEED).json()
    created = client.post(
        "/v1/analyses",
        json={
            "extraction_run_id": extraction_id,
            "catalog_version_id": catalog["catalog_version_id"],
            "analysis_date": "2026-09-25",
        },
    )
    assert created.status_code == 202, created.text
    aid = created.json()["id"]
    # The C06 graph, not the test, must run the queued C05 solver.
    rid = start_graph(client, aid, question=case["question"])
    execute_graph(rid)
    if status(client, rid)["state"] == "awaiting_review":
        assert (
            client.post(
                f"/v1/rag/runs/{rid}/resume",
                json={"decision": "continue_provisional", "reviewer": "synthetic-evaluator"},
            ).status_code
            == 200
        )
        execute_graph(rid)
    assert status(client, rid)["state"] == "completed", status(client, rid)
    output = report(client, rid)
    assert output["status"] == case["expected"], output["review_blockers"]
    if case["expected"] == "needs_review":
        assert output["approval"] == "not_granted"
    if case["expected"] == "infeasible":
        assert output["solver_status"] == "infeasible"
    for c in output["configurations"]:
        assert all(e["requirement"]["validated_evidence"] for e in c["evaluations"])


def test_embedding_failure_is_atomic_and_bounded(client, solver_id):
    class Broken:
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            raise TimeoutError("sensitive-provider-text")

    broken = Broken()
    with SessionLocal() as db:
        idx, _ = create_index(db, IndexCreate(analysis_id=solver_id))
    execute_index(idx.id, adapter=broken)
    assert broken.calls == 2
    with SessionLocal() as db:
        assert db.get(RagJob, idx.id).error_code == "provider_transient"
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_metadata_document_and_catalog_filters(client, solver_id):
    from uuid import uuid4

    with SessionLocal() as db:
        idx, _ = create_index(db, IndexCreate(analysis_id=solver_id))
    execute_index(idx.id)
    for key in ["document_version_id", "catalog_version_id"]:
        response = client.post(
            "/v1/rag/search", json={"index_id": idx.id, "query": "range", key: str(uuid4())}
        )
        assert response.status_code == 200 and not response.json()["hits"]


def test_reindex_uses_new_frozen_catalog_content_not_mutable_live_rows(client, inputs):
    from app.catalog.models import CatalogItem

    first = client.post("/v1/analyses", json=inputs).json()["id"]
    execute_analysis(first)
    with SessionLocal() as db:
        original, _ = create_index(db, IndexCreate(analysis_id=first))
        row = db.scalar(
            select(CatalogItem).where(
                CatalogItem.catalog_version_id == inputs["catalog_version_id"]
            )
        )
        row.name = "Synthetic updated description"
        db.commit()
        same, _ = create_index(db, IndexCreate(analysis_id=first))
        assert same.id == original.id
    second = client.post("/v1/analyses", json=inputs).json()["id"]
    assert second != first
    execute_analysis(second)
    with SessionLocal() as db:
        changed, _ = create_index(db, IndexCreate(analysis_id=second))
        assert changed.identity != original.identity


def test_low_confidence_graph_creates_review_not_false_approval(client, solver_id):
    rid = start_graph(client, solver_id, policy={"threshold_bps": 10000})
    execute_graph(rid)
    assert status(client, rid)["state"] == "awaiting_review"
    output = report(client, rid)
    assert output["status"] == "needs_review"
    assert "empty_or_low_confidence_retrieval" in output["review_blockers"]


def test_missing_analysis_and_invalid_index_contracts(client):
    from uuid import uuid4

    assert client.post("/v1/rag/runs", json={"analysis_id": str(uuid4())}).status_code == 404
    assert client.post("/v1/rag/indexes", json={"analysis_id": "not-a-uuid"}).status_code == 422
    assert (
        client.post("/v1/rag/search", json={"index_id": str(uuid4()), "query": "range"}).status_code
        == 404
    )


def test_dimension_mismatch_has_no_partial_vectors(client, solver_id):
    with SessionLocal() as db:
        idx, _ = create_index(db, IndexCreate(analysis_id=solver_id))
    execute_index(idx.id, adapter=FakeEmbeddings(64))
    with SessionLocal() as db:
        assert db.get(RagJob, idx.id).error_code == "embedding_dimension_mismatch"
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0
