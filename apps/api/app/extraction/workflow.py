import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import TypedDict

from langchain_core.exceptions import OutputParserException
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError
from sqlalchemy import select, update

from app.extraction.contracts import PROMPT_VERSION, SCHEMA_VERSION, ValidatedRequirement
from app.extraction.models import (
    EvidenceLink,
    ExtractionNodeRun,
    ExtractionRun,
    RequirementRecord,
    ReviewIssue,
)
from app.extraction.provider import (
    ConfiguredAdapter,
    ProviderFailure,
    SchemaFailure,
    model_snapshot,
)
from app.extraction.validation import (
    REQUIRED_CATEGORIES,
    detect_conflicts,
    issue,
    normalize,
    validate_evidence,
)
from app.models import DocumentVersion, SourceSpan
from app.settings import get_settings

LOGGER = logging.getLogger(__name__)


class ExtractionState(TypedDict, total=False):
    run_id: str
    spans: dict[str, dict]
    batches: list[list[dict]]
    batch_results: dict[int, list]
    schema_errors: list[dict]
    failed_batch: int | None
    candidates: list
    normalized: list[dict]
    accepted: list[dict]
    issues: list
    repair_codes: list[str]
    repairs: int
    transient_retries: int
    error_code: str | None
    branch: str
    outcome: str


def build_graph(session_factory, adapter, settings):
    """Compiled StateGraph with safe per-node audits; raw state is never API-visible."""
    counts: dict[str, int] = {}

    def audited(name, fn):
        def invoke(state):
            counts[name] = counts.get(name, 0) + 1
            started = perf_counter()
            with session_factory() as db:
                trace = ExtractionNodeRun(
                    run_id=state["run_id"],
                    node=name,
                    attempt=counts[name],
                    status="running",
                    started_at=datetime.now(UTC),
                    summary={},
                )
                db.add(trace)
                db.commit()
                trace_id = trace.id
            try:
                changes = fn(state)
            except Exception:  # noqa: BLE001 -- safe node boundary for arbitrary provider failures
                # Never serialize exception bodies, model output, prompts or source text.
                changes = {"error_code": "node_failure", "outcome": "failed", "branch": "failed"}
            merged = {**state, **changes}
            with session_factory() as db:
                trace = db.get(ExtractionNodeRun, trace_id)
                trace.finished_at = datetime.now(UTC)
                trace.elapsed_ms = round((perf_counter() - started) * 1000)
                trace.status = "failed" if changes.get("error_code") else "completed"
                trace.error_code = changes.get("error_code")
                trace.branch = changes.get("branch")
                trace.summary = {
                    "span_count": len(merged.get("spans", {})),
                    "candidate_count": len(merged.get("candidates", [])),
                    "accepted_count": len(merged.get("accepted", [])),
                    "issue_codes": sorted({x.code for x in merged.get("issues", [])}),
                    "repair_count": merged.get("repairs", 0),
                    "transient_retry_count": merged.get("transient_retries", 0),
                    "batch_count": len(merged.get("batches", [])),
                    "completed_batches": len(merged.get("batch_results", {})),
                    "failed_batch": merged.get("failed_batch"),
                    "schema_errors": merged.get("schema_errors", []),
                }
                db.commit()
            return changes

        return invoke

    def load(state):
        with session_factory() as db:
            run = db.get(ExtractionRun, state["run_id"])
            version = db.get(DocumentVersion, run.document_version_id)
            spans = db.scalars(
                select(SourceSpan)
                .where(SourceSpan.document_version_id == version.id)
                .order_by(SourceSpan.chunk_index)
            ).all()
            return {
                "spans": {
                    s.id: {
                        "id": s.id,
                        "document_id": version.document_id,
                        "document_version_id": version.id,
                        "text": s.text,
                        "page_number": s.page_number,
                        "section_name": s.section_name,
                        "chunk_index": s.chunk_index,
                    }
                    for s in spans
                }
            }

    def prepare(state):
        batches, batch, size = [], [], 0
        # Bound input volume against output budget; never truncate a response to valid JSON.
        limit = min(settings.extraction_batch_chars, settings.llm_max_tokens)
        for span in state["spans"].values():
            # Split oversized spans without changing their immutable original anchors.
            for offset in range(0, len(span["text"]), limit):
                part = {"span_id": span["id"], "text": span["text"][offset : offset + limit]}
                if batch and size + len(part["text"]) > limit:
                    batches.append(batch)
                    batch, size = [], 0
                batch.append(part)
                size += len(part["text"])
        if batch:
            batches.append(batch)
        if not batches or len(batches) > settings.extraction_max_batches:
            return {"error_code": "document_batch_limit", "outcome": "failed", "branch": "failed"}
        return {"batches": batches}

    def extract(state):
        results = dict(state.get("batch_results", {}))
        index = 0
        try:
            for index, batch in enumerate(state["batches"]):
                if index not in results:
                    results[index] = adapter.extract(
                        batch, state.get("repair_codes", [])
                    ).requirements
        except (ValidationError, OutputParserException, SchemaFailure) as exc:
            errors = (
                exc.errors if isinstance(exc, SchemaFailure) else [{"expected": "valid_schema"}]
            )
            return {
                "batch_results": results,
                "failed_batch": index,
                "schema_errors": errors,
                "candidates": [],
                "issues": [
                    issue(
                        "invalid_model_schema",
                        None,
                        f"Batch {index} did not satisfy the extraction schema; see safe node diagnostics.",
                    )
                ],
                "branch": "repair",
            }
        except ProviderFailure as exc:
            if (
                exc.recoverable
                and state.get("transient_retries", 0) < settings.extraction_transient_retries
            ):
                return {
                    "branch": "transient",
                    "candidates": [],
                    "error_code": exc.code,
                    "batch_results": results,
                    "failed_batch": index,
                }
            return {"error_code": exc.code, "outcome": "failed", "branch": "failed"}
        return {
            "candidates": [c for i in sorted(results) for c in results[i]],
            "batch_results": results,
            "failed_batch": None,
            "schema_errors": [],
            "issues": [],
            "error_code": None,
            "branch": "normalize",
        }

    def normalize_units(state):
        normalized, findings = [], []
        for index, candidate in enumerate(state["candidates"]):
            try:
                value, unit = normalize(candidate)
                normalized.append(
                    {"index": index, "candidate": candidate, "value": value, "unit": unit}
                )
            except ValueError as exc:
                findings.append(
                    issue(str(exc), index, "Value or unit cannot be normalized deterministically.")
                )
        return {"normalized": normalized, "issues": findings}

    def validate(state):
        # Unsupported units/ambiguous domain values require review, not invented replacements.
        # Keep individually valid candidates and their citations; report every rejected item.
        return {"branch": "evidence"}

    def evidence(state):
        accepted, findings = [], list(state["issues"])
        for item in state["normalized"]:
            try:
                anchors = validate_evidence(item["candidate"], state["spans"])
                accepted.append({**item, "anchors": anchors})
            except ValueError as exc:
                findings.append(
                    issue(
                        str(exc),
                        item["index"],
                        "Citation or claimed numeric value does not match this document version.",
                    )
                )
        return {"accepted": accepted, "issues": findings}

    def conflicts(state):
        return {"issues": state["issues"] + detect_conflicts(state["accepted"])}

    def confidence(state):
        findings = list(state["issues"])
        for item in state["accepted"]:
            candidate = item["candidate"]
            if candidate.confidence < settings.extraction_confidence_threshold:
                findings.append(
                    issue(
                        "low_confidence",
                        item["index"],
                        "Confidence is below the configured review threshold.",
                    )
                )
            if candidate.semantics == "ambiguous" or candidate.original_value == "unknown":
                findings.append(
                    issue(
                        "unknown_or_ambiguous",
                        item["index"],
                        "Value or obligation needs human clarification.",
                    )
                )
        present = {item["candidate"].category for item in state["accepted"]}
        for category in sorted(REQUIRED_CATEGORIES - present):
            findings.append(
                issue(
                    "missing_critical_category",
                    None,
                    f"Review missing {category} information; no value inferred.",
                )
            )
        branch = "review" if any(x.blocking for x in findings) else "persist"
        return {
            "issues": findings,
            "branch": branch,
            "outcome": "needs_review" if branch == "review" else "completed",
        }

    def retry(state):
        transient = state["branch"] == "transient"
        return {
            "repairs": state.get("repairs", 0) + (not transient),
            "transient_retries": state.get("transient_retries", 0) + transient,
            "repair_codes": state.get("schema_errors", [])
            or sorted({x.code for x in state.get("issues", [])}),
            "error_code": None,
            "accepted": [],
            "normalized": [],
            "branch": "retry",
        }

    def review(state):
        findings = list(state.get("issues", []))
        if state.get("branch") == "repair":
            findings.append(
                issue(
                    "repair_exhausted", None, "Bounded repair was exhausted; human review required."
                )
            )
        return {"issues": findings, "outcome": "needs_review", "branch": "review"}

    def persist(state):
        with session_factory() as db:
            for item in state.get("accepted", []):
                validated = ValidatedRequirement(
                    **item["candidate"].model_dump(),
                    normalized_value=item["value"],
                    normalized_unit=item["unit"],
                    validated_evidence=item["anchors"],
                )
                record = RequirementRecord(
                    run_id=state["run_id"],
                    ordinal=item["index"],
                    payload=validated.model_dump(mode="json"),
                )
                db.add(record)
                db.flush()
                for anchor in item["anchors"]:
                    db.add(
                        EvidenceLink(
                            requirement_id=record.id,
                            span_id=anchor.span_id,
                            anchor=anchor.model_dump(),
                        )
                    )
            for finding in state.get("issues", []):
                db.add(
                    ReviewIssue(
                        run_id=state["run_id"],
                        payload=finding.model_dump(),
                        state="open" if finding.blocking else "informational",
                    )
                )
            db.commit()
        return {"branch": "finalize"}

    def finalize(state):
        with session_factory() as db:
            run = db.get(ExtractionRun, state["run_id"])
            run.state = state.get("outcome", "failed")
            run.error_code = state.get("error_code")
            run.review_state = "pending" if run.state == "needs_review" else "not_required"
            run.finished_at = datetime.now(UTC)
            db.commit()
        return {"branch": "end"}

    nodes = {
        "load_document_spans": load,
        "prepare_extraction_batches": prepare,
        "extract_requirements": extract,
        "normalize_units": normalize_units,
        "validate_requirements": validate,
        "check_evidence": evidence,
        "detect_conflicts": conflicts,
        "route_by_confidence": confidence,
        "retry_extraction": retry,
        "create_review_items": review,
        "persist_results": persist,
        "finalize_run": finalize,
    }
    graph = StateGraph(ExtractionState)
    for name, fn in nodes.items():
        graph.add_node(name, audited(name, fn))
    graph.add_edge(START, "load_document_spans")
    next_nodes = {
        "load_document_spans": "prepare_extraction_batches",
        "prepare_extraction_batches": "extract_requirements",
        "normalize_units": "validate_requirements",
        "check_evidence": "detect_conflicts",
        "detect_conflicts": "route_by_confidence",
        "retry_extraction": "extract_requirements",
        "create_review_items": "persist_results",
        "persist_results": "finalize_run",
    }
    for source, target in next_nodes.items():
        graph.add_conditional_edges(
            source,
            lambda s, t=target: "finalize_run" if s.get("outcome") == "failed" else t,
            list({target, "finalize_run"}),
        )

    def route_extraction(state):
        if state.get("outcome") == "failed":
            return "finalize_run"
        if state["branch"] == "transient":
            return "retry_extraction"
        if state["branch"] == "repair":
            return (
                "retry_extraction"
                if state.get("repairs", 0) < settings.extraction_max_repairs
                else "create_review_items"
            )
        return "normalize_units"

    graph.add_conditional_edges(
        "extract_requirements",
        route_extraction,
        ["finalize_run", "retry_extraction", "create_review_items", "normalize_units"],
    )
    graph.add_conditional_edges(
        "validate_requirements",
        lambda s: "check_evidence" if s["branch"] == "evidence" else route_extraction(s),
        ["check_evidence", "retry_extraction", "create_review_items", "finalize_run"],
    )
    graph.add_conditional_edges(
        "route_by_confidence",
        lambda s: (
            "finalize_run"
            if s.get("outcome") == "failed"
            else "create_review_items"
            if s["branch"] == "review"
            else "persist_results"
        ),
        ["finalize_run", "create_review_items", "persist_results"],
    )
    graph.add_edge("finalize_run", END)
    return graph.compile()


def execute_run(run_id, session_factory, adapter=None, settings=None):
    settings = settings or get_settings()
    with session_factory() as db:
        run = db.get(ExtractionRun, run_id)
        if run is None or run.state != "queued":
            return
        if (
            run.model_config != model_snapshot(settings)
            or run.schema_version != SCHEMA_VERSION
            or run.prompt_version != PROMPT_VERSION
        ):
            run.state, run.error_code, run.finished_at = (
                "failed",
                "worker_config_mismatch",
                datetime.now(UTC),
            )
            db.commit()
            return
        claimed = db.execute(
            update(ExtractionRun)
            .where(ExtractionRun.id == run_id, ExtractionRun.state == "queued")
            .values(state="processing")
        )
        db.commit()
        if claimed.rowcount != 1:
            return
    try:
        # Suppress ambient LangSmith tracing too: graph state contains protected source text.
        with tracing_context(enabled=False):
            graph = build_graph(session_factory, adapter or ConfiguredAdapter(settings), settings)
            graph.invoke(
                {
                    "run_id": run_id,
                    "repairs": 0,
                    "transient_retries": 0,
                    "issues": [],
                    "accepted": [],
                },
                {"recursion_limit": 100, "callbacks": []},
            )
    except Exception as exc:  # noqa: BLE001 -- persist safe final failure, never provider bodies
        with session_factory() as db:
            run = db.get(ExtractionRun, run_id)
            run.state = "failed"
            run.error_code = exc.code if isinstance(exc, ProviderFailure) else "workflow_failure"
            run.finished_at = datetime.now(UTC)
            db.commit()
            error_code = run.error_code
        LOGGER.warning("Extraction failed run_id=%s code=%s", run_id, error_code)


if __name__ == "__main__":
    print(build_graph(None, None, get_settings()).get_graph().draw_mermaid())
