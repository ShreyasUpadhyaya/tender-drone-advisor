import json

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.analysis.models import AnalysisRun, AnalysisSnapshot, GeneratedConfiguration
from app.db import SessionLocal
from app.extraction.provider import ProviderFailure
from app.models import DocumentVersion, SourceSpan
from app.rag.contracts import Fact, IndexCreate
from app.rag.models import Chunk, RagJob
from app.rag.providers import configuration, embed
from app.settings import get_settings
from app.solver.orchestration import fingerprint


def solver_context(db, analysis_id):
    run = db.get(AnalysisRun, str(analysis_id))
    if not run:
        raise ProviderFailure("analysis_not_found")
    snapshot = db.get(AnalysisSnapshot, run.id)
    if not snapshot:
        raise ProviderFailure("analysis_snapshot_missing")
    configs = db.scalars(
        select(GeneratedConfiguration)
        .where(GeneratedConfiguration.analysis_id == run.id)
        .order_by(GeneratedConfiguration.rank)
    ).all()
    return run, snapshot.payload, [c.payload for c in configs]


def source_facts(db, request):
    run, snapshot, configs = solver_context(db, request.analysis_id)
    if run.state != "completed" and set(request.kinds) & {"configuration", "rejection"}:
        raise ProviderFailure("solver_not_completed")
    facts = []
    common = {
        "analysis_id": run.id,
        "document_id": db.get(DocumentVersion, snapshot["document_version_id"]).document_id,
        "document_version_id": snapshot["document_version_id"],
        "catalog_version_id": snapshot["catalog_version_id"],
    }

    def add(kind, text, citation):
        if kind not in request.kinds or not text.strip():
            return
        for offset in range(0, len(text), request.policy.chunk_chars):
            part = text[offset : offset + request.policy.chunk_chars]
            anchor = {**common, **citation}
            if anchor.get("start") is not None:
                anchor["start"] += offset
                anchor["end"] = anchor["start"] + len(part)
            value = {"kind": kind, "text": part, "citation": anchor}
            facts.append(Fact(id=fingerprint(value), **value))

    spans = db.scalars(
        select(SourceSpan)
        .where(SourceSpan.document_version_id == snapshot["document_version_id"])
        .order_by(SourceSpan.chunk_index, SourceSpan.id)
    ).all()
    by_id = {s.id: s for s in spans}
    for span in spans:
        add(
            "tender",
            span.text,
            {
                "span_id": span.id,
                "page_number": span.page_number,
                "section_name": span.section_name,
                "start": 0,
                "end": len(span.text),
            },
        )
    for requirement in snapshot["requirements"]:
        for e in requirement["requirement"]["validated_evidence"]:
            span = by_id.get(e["span_id"])
            if not span or span.text[e["quote_start"] : e["quote_end"]] != e["quote"]:
                raise ProviderFailure("citation_source_changed")
            add(
                "requirement",
                e["quote"],
                {
                    "span_id": e["span_id"],
                    "page_number": e["page_number"],
                    "section_name": e["section_name"],
                    "start": e["quote_start"],
                    "end": e["quote_end"],
                    "requirement_id": requirement["id"],
                },
            )
    for item in snapshot["items"]:
        add(
            "catalog",
            json.dumps(
                {
                    k: item[k]
                    for k in (
                        "sku",
                        "name",
                        "manufacturer",
                        "category",
                        "specs",
                        "weight_g",
                        "availability",
                        "inventory_qty",
                        "prices",
                        "provenance",
                    )
                },
                sort_keys=True,
            ),
            {"item_id": item["id"], "item_version": item["item_version"]},
        )
    for rule in snapshot["rules"]:
        add("compatibility", json.dumps(rule, sort_keys=True), {})
    for c in configs:
        add(
            "configuration",
            json.dumps(
                {
                    k: c[k]
                    for k in (
                        "id",
                        "status",
                        "recommended",
                        "total_weight_g",
                        "cost",
                        "bom",
                        "requirement_coverage_bps",
                        "lead_time_days",
                    )
                },
                sort_keys=True,
            ),
            {"configuration_id": c["id"]},
        )
    for rejected in run.summary.get("rejected", []):
        add("rejection", json.dumps(rejected, sort_keys=True), {})
    facts = sorted({f.id: f for f in facts}.values(), key=lambda f: f.id)
    if len(facts) > request.policy.max_chunks:
        raise ProviderFailure("index_chunk_limit_exceeded")
    return facts


def create_job(db, kind, request, config, sources=None):
    body = request.model_dump(mode="json")
    body["kinds"] = sorted(set(body["kinds"])) if "kinds" in body else None
    if body["kinds"] is None:
        del body["kinds"]
    run, snapshot, _ = solver_context(db, request.analysis_id)
    payload = {"body": body}
    if sources is not None:
        payload["sources"] = [f.model_dump(mode="json") for f in sources]
    identity = fingerprint(
        {
            "type": kind,
            "analysis_identity": run.identity,
            "snapshot_hash": fingerprint(snapshot),
            "request": payload,
            "config": config,
        }
    )
    existing = db.scalar(select(RagJob).where(RagJob.identity == identity))
    if existing:
        db.commit()
        return existing, True
    job = RagJob(
        identity=identity,
        job_type=kind,
        namespace=config["namespace"],
        analysis_id=run.id,
        request=payload,
        config=config,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        job = db.scalar(select(RagJob).where(RagJob.identity == identity))
        if job is None:
            raise
        return job, True
    return job, False


def create_index(db, request, settings=None):
    settings = settings or get_settings()
    return create_job(db, "index", request, configuration(settings), source_facts(db, request))


def execute_index(job_id, session_factory=SessionLocal, settings=None, adapter=None):
    settings = settings or get_settings()
    with session_factory() as db:
        row = db.get(RagJob, job_id)
        if not row or row.job_type != "index":
            return
        claimed = db.execute(
            update(RagJob)
            .where(RagJob.id == job_id, RagJob.state == "queued")
            .values(state="running", attempts=RagJob.attempts + 1)
        )
        db.commit()
        if claimed.rowcount != 1:
            return
        try:
            if row.config != configuration(settings):
                raise ProviderFailure("worker_config_mismatch")
            request = IndexCreate.model_validate_json(json.dumps(row.request["body"]))
            sources = [Fact.model_validate(f) for f in row.request["sources"]]
            vectors = []
            for offset in range(0, len(sources), 64):
                vectors.extend(
                    embed(
                        settings,
                        row.config,
                        [f.text for f in sources[offset : offset + 64]],
                        request.allow_external,
                        request.policy.transient_retries,
                        adapter,
                    )
                )
            for fact, vector in zip(sources, vectors, strict=True):
                cite = fact.citation
                db.add(
                    Chunk(
                        id=fingerprint([row.identity, fact.id]),
                        index_id=row.id,
                        namespace=row.namespace,
                        kind=fact.kind,
                        document_version_id=cite.document_version_id,
                        catalog_version_id=cite.catalog_version_id,
                        requirement_id=cite.requirement_id,
                        item_id=cite.item_id,
                        text=fact.text,
                        citation=cite.model_dump(mode="json"),
                        embedding=vector,
                        vector=json.dumps(vector),
                        dimensions=len(vector),
                    )
                )
            row.state = "completed"
            row.result = {"count": len(sources)}
            db.commit()
        except Exception as exc:  # noqa: BLE001 -- safe provider/worker boundary
            db.rollback()
            row = db.get(RagJob, job_id)
            row.state = "failed"
            row.error_code = exc.code if isinstance(exc, ProviderFailure) else "index_failure"
            db.commit()
