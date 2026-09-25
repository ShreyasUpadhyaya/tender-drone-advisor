import json
import math
from uuid import uuid4

from langsmith import tracing_context
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.extraction.provider import ProviderFailure
from app.rag.contracts import Citation, Fact, Hit, SearchResponse
from app.rag.models import Chunk, RagJob, RetrievalRun
from app.rag.providers import embed, tokens
from app.settings import get_settings
from app.solver.orchestration import fingerprint


def cosine(a, b):
    denominator = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0.0


def search(db, request, settings=None, adapter=None):
    settings = settings or get_settings()
    # Serialize duplicate index searches before any provider call on PostgreSQL.
    index = db.scalar(select(RagJob).where(RagJob.id == str(request.index_id)).with_for_update())
    if not index or index.job_type != "index" or index.namespace != settings.rag_namespace:
        raise ProviderFailure("index_not_found")
    if index.state != "completed":
        raise ProviderFailure("index_not_ready")
    body = request.model_dump(mode="json")
    identity = fingerprint({"index": index.identity, "request": body})
    existing = db.scalar(select(RetrievalRun).where(RetrievalRun.identity == identity))
    if existing:
        result = SearchResponse.model_validate(existing.result)
        result.idempotent = True
        db.commit()
        return result
    stmt = select(Chunk).where(
        Chunk.index_id == index.id,
        Chunk.namespace == settings.rag_namespace,
        Chunk.kind.in_(request.kinds),
    )
    if request.requirement_ids:
        stmt = stmt.where(Chunk.requirement_id.in_(request.requirement_ids))
    if request.document_version_id:
        stmt = stmt.where(Chunk.document_version_id == str(request.document_version_id))
    if request.catalog_version_id:
        stmt = stmt.where(Chunk.catalog_version_id == str(request.catalog_version_id))
    if request.item_ids:
        stmt = stmt.where(Chunk.item_id.in_(request.item_ids))
    rows = db.scalars(stmt.order_by(Chunk.id)).all()
    vector = None
    if rows:
        with tracing_context(enabled=False):
            vector = embed(
                settings,
                index.config,
                [request.query],
                request.allow_external,
                index.request["body"]["policy"]["transient_retries"],
                adapter,
            )[0]
    similarity = {}
    if rows and db.bind.dialect.name == "postgresql":
        # Metadata restrictions are applied by the filtered IDs BEFORE vector scoring.
        for row_id, distance in db.execute(
            text(
                "SELECT id, vector <=> CAST(:v AS vector) FROM rag_chunks WHERE index_id=:idx AND namespace=:ns AND id = ANY(:ids)"
            ),
            {
                "v": json.dumps(vector),
                "idx": index.id,
                "ns": settings.rag_namespace,
                "ids": [r.id for r in rows],
            },
        ):
            similarity[row_id] = max(0.0, min(1.0, 1 - float(distance)))
    q = tokens(request.query)
    hits = []
    weight = index.request["body"]["policy"]["vector_weight_bps"]
    seen = set()
    for row in rows:
        semantic = round(10000 * similarity.get(row.id, max(0.0, cosine(vector, row.embedding))))
        lexical = 10000 * len(q & tokens(row.text)) // max(1, len(q))
        score = (semantic * weight + lexical * (10000 - weight)) // 10000
        if score < request.threshold_bps:
            continue
        cite = Citation.model_validate(row.citation)
        fact = Fact(id=row.id, kind=row.kind, text=row.text, citation=cite)
        hits.append(Hit(fact=fact, semantic_bps=semantic, lexical_bps=lexical, final_bps=score))
    ordered = []
    for hit in sorted(hits, key=lambda h: (-h.final_bps, h.fact.id)):
        c = hit.fact.citation
        key = (c.span_id, c.start, c.end) if c.span_id else (hit.fact.kind, hit.fact.text)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(hit)
    issues = [] if ordered else ["empty_or_low_confidence_retrieval"]
    run = RetrievalRun(
        id=str(uuid4()), identity=identity, index_id=index.id, request=body, result={}
    )
    db.add(run)
    result = SearchResponse(
        id=run.id,
        index_id=index.id,
        state="completed",
        hits=ordered[: request.top_k],
        issues=issues,
    )
    run.result = result.model_dump(mode="json")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(RetrievalRun).where(RetrievalRun.identity == identity))
        return SearchResponse.model_validate(existing.result).model_copy(
            update={"idempotent": True}
        )
    return result
