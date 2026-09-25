from datetime import UTC, datetime
from time import perf_counter
from typing import TypedDict

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from sqlalchemy import func, select, update

from app.analysis.models import AnalysisIssue
from app.analysis.service import execute_analysis
from app.db import SessionLocal
from app.extraction.provider import ProviderFailure
from app.models import DocumentVersion, SourceSpan
from app.rag.checkpoints import DatabaseSaver
from app.rag.contracts import (
    Fact,
    GroundedReport,
    IndexCreate,
    ReportPlan,
    ReportSection,
    SearchCreate,
)
from app.rag.indexing import create_index, execute_index, solver_context
from app.rag.models import NodeAudit, RagJob, RetrievalRun
from app.rag.providers import (
    InvalidReport,
    configuration,
    public_versions,
    report_adapter,
)
from app.rag.retrieval import search
from app.settings import get_settings
from app.solver.contracts import Configuration, Issue, Rejection


class GraphState(TypedDict, total=False):
    run_id: str
    phase: str
    issues: list[str]
    retrieval_ids: list[str]
    repairs: int
    transient_retries: int
    branch: str
    error_code: str | None
    plan: dict
    repair_code: str
    acknowledged: bool


QUESTIONS = {"summarize", "cost", "risks", "requirements", "alternatives"}


def build_graph(sessions, settings, adapter=None, embedding=None):
    def context(state):
        with sessions() as db:
            job = db.get(RagJob, state["run_id"])
            solver, snapshot, configs = solver_context(db, job.analysis_id)
            return job, solver, snapshot, configs

    def audited(name, fn):
        def invoke(state):
            started = perf_counter()
            with sessions() as db:
                attempt = 1 + (
                    db.scalar(
                        select(func.max(NodeAudit.attempt)).where(
                            NodeAudit.run_id == state["run_id"], NodeAudit.node == name
                        )
                    )
                    or 0
                )
                row = NodeAudit(
                    run_id=state["run_id"],
                    node=name,
                    attempt=attempt,
                    status="running",
                    started_at=datetime.now(UTC),
                    summary={},
                )
                db.add(row)
                db.commit()
                audit_id = row.id
            interrupted = None
            try:
                changes = fn(state)
            except GraphInterrupt as exc:
                interrupted = exc
                changes = {"branch": "human_review"}
            except Exception as exc:  # noqa: BLE001 -- safe provider/worker boundary
                changes = {
                    "error_code": exc.code if isinstance(exc, ProviderFailure) else "node_failure",
                    "branch": "failed",
                }
            with sessions() as db:
                row = db.get(NodeAudit, audit_id)
                row.finished_at = datetime.now(UTC)
                row.elapsed_ms = round((perf_counter() - started) * 1000)
                row.status = (
                    "interrupted"
                    if interrupted
                    else "failed"
                    if changes.get("error_code")
                    else "completed"
                )
                row.branch = changes.get("branch", "continue")
                row.error_code = changes.get("error_code")
                row.summary = {
                    "issue_codes": changes.get("issues", state.get("issues", [])),
                    "retrieval_count": len(
                        changes.get("retrieval_ids", state.get("retrieval_ids", []))
                    ),
                    "repair_count": changes.get("repairs", state.get("repairs", 0)),
                }
                db.commit()
            if interrupted:
                raise interrupted
            return changes

        return invoke

    def parse(state):
        _, _, snapshot, _ = context(state)
        with sessions() as db:
            doc = db.get(DocumentVersion, snapshot["document_version_id"])
            if not doc or doc.document.state != "completed":
                raise ProviderFailure("document_not_completed")
        return {"branch": "load_extraction_context"}

    def extraction(state):
        _, _, snapshot, _ = context(state)
        if snapshot["extraction_schema_version"] != "requirements-v2":
            raise ProviderFailure("unsupported_extraction_schema")
        return {"branch": "validate_requirements"}

    def validate(state):
        job, _, snapshot, _ = context(state)
        issues = list(state.get("issues", []))
        if job.request["body"]["question"].casefold().strip() not in QUESTIONS:
            issues.append("unsupported_question")
        if (
            snapshot["extraction_state"] != "completed"
            or snapshot["extraction_review_state"] != "not_required"
        ):
            issues.append("extraction_needs_review")
        present = {r["requirement"]["category"] for r in snapshot["requirements"]}
        issues.extend(
            "missing_critical_" + c
            for c in sorted({"platform", "payload", "range", "endurance"} - present)
        )
        return {
            "issues": sorted(set(issues)),
            "phase": "validation",
            "branch": "human_review" if issues else "retrieve_catalog",
        }

    def human(state):
        decision = interrupt({"review_required": True, "issue_codes": state.get("issues", [])})
        if decision != "continue_provisional":
            raise ProviderFailure("invalid_review_decision")
        return {"acknowledged": True, "branch": "continue_provisional"}

    def retrieve(state, kinds, query):
        job, _, _, _ = context(state)
        body = job.request["body"]
        index_request = IndexCreate(
            analysis_id=job.analysis_id,
            kinds=kinds,
            policy=body["policy"],
            allow_external=body["allow_external"],
        )
        with sessions() as db:
            idx, _ = create_index(db, index_request, settings)
            idx_id = idx.id
        execute_index(idx_id, sessions, settings, embedding)
        with sessions() as db:
            idx = db.get(RagJob, idx_id)
            if idx.state != "completed":
                raise ProviderFailure(idx.error_code or "index_failed")
            result = search(
                db,
                SearchCreate(
                    index_id=idx_id,
                    query=query,
                    kinds=kinds,
                    top_k=body["policy"]["top_k"],
                    threshold_bps=body["policy"]["threshold_bps"],
                    allow_external=body["allow_external"],
                ),
                settings,
                embedding,
            )
        return {
            "retrieval_ids": list(dict.fromkeys(state.get("retrieval_ids", []) + [result.id])),
            "issues": sorted(set(state.get("issues", []) + result.issues)),
            "branch": "continue",
        }

    def retrieve_catalog(state):
        first = audited(
            "retrieve_tender_evidence",
            lambda s: retrieve(s, ["tender", "requirement"], "platform range endurance payload"),
        )(state)
        if first.get("error_code"):
            return first
        merged = {**state, **first}
        second = audited(
            "retrieve_catalog_context",
            lambda s: retrieve(
                s, ["catalog", "compatibility"], "range payload battery motor camera inventory"
            ),
        )(merged)
        return {**first, **second}

    def solver(state):
        _, run, _, _ = context(state)
        if run.state == "queued":
            execute_analysis(run.id, sessions)
        _, run, _, _ = context(state)
        if run.state != "completed":
            raise ProviderFailure(run.error_code or "solver_not_completed")
        issues = list(state.get("issues", []))
        if run.status == "needs_review":
            issues.append("solver_needs_review")
        updated = {**state, "issues": sorted(set(issues))}
        return retrieve(
            updated, ["configuration", "rejection"], "status cost bom failed incompatibility"
        )

    def facts(state):
        _, _, _, configs = context(state)
        item_ids = {s["item"]["id"] for c in configs for s in c["selections"]}
        result = {}
        with sessions() as db:
            for rid in state.get("retrieval_ids", []):
                row = db.get(RetrievalRun, rid)
                for hit in row.result["hits"]:
                    fact = Fact.model_validate(hit["fact"])
                    anchor = fact.citation
                    if anchor.span_id:
                        span = db.get(SourceSpan, anchor.span_id)
                        if (
                            not span
                            or span.document_version_id != anchor.document_version_id
                            or anchor.start is None
                            or anchor.end is None
                            or span.text[anchor.start : anchor.end] != fact.text
                        ):
                            raise ProviderFailure("citation_source_changed")
                    if fact.kind == "catalog" and fact.citation.item_id not in item_ids:
                        continue
                    if fact.kind == "compatibility":
                        continue  # Pair rules remain retrieval context; C05 renders actual rejections.
                    result[fact.id] = fact
        return result

    def grounding(state):
        values = facts(state)
        issues = list(state.get("issues", []))
        if not values:
            issues.append("grounding_context_empty")
        return {"issues": sorted(set(issues)), "branch": "compose_report"}

    def compose(state):
        job, _, _, _ = context(state)
        policy = job.request["body"]["policy"]
        try:
            model = adapter or report_adapter(
                settings, job.config, job.request["body"]["allow_external"]
            )
            plan = model.compose(
                [f.model_dump(mode="json") for f in facts(state).values()],
                state.get("repair_code", ""),
            )
            return {"plan": plan.model_dump(), "branch": "apply_policy_guard", "error_code": None}
        except InvalidReport:
            if state.get("repairs", 0) < policy["max_repairs"]:
                return {
                    "repairs": state.get("repairs", 0) + 1,
                    "repair_code": "invalid_report_schema; expected ReportPlan",
                    "branch": "retry",
                }
            return {
                "plan": {"schema_version": "report-plan-v1", "sections": []},
                "branch": "apply_policy_guard",
                "issues": sorted(set(state.get("issues", []) + ["report_repair_exhausted"])),
            }
        except ProviderFailure as exc:
            if exc.recoverable and state.get("transient_retries", 0) < policy["transient_retries"]:
                return {
                    "transient_retries": state.get("transient_retries", 0) + 1,
                    "branch": "retry",
                }
            return {
                "plan": {"schema_version": "report-plan-v1", "sections": []},
                "branch": "apply_policy_guard",
                "issues": sorted(set(state.get("issues", []) + [exc.code])),
            }

    def guard(state):
        allowed = facts(state)
        plan = ReportPlan.model_validate(state["plan"])
        seen = set()
        section_kinds = {
            "evidence": {"tender", "requirement"},
            "catalog_context": {"catalog"},
            "solver": {"configuration"},
            "risks": {"rejection"},
        }
        valid = True
        for section in plan.sections:
            for fid in section.fact_ids:
                if (
                    fid not in allowed
                    or fid in seen
                    or allowed[fid].kind not in section_kinds[section.section]
                ):
                    valid = False
                seen.add(fid)
        if not valid:
            job, _, _, _ = context(state)
            if state.get("repairs", 0) < job.request["body"]["policy"]["max_repairs"]:
                return {
                    "repairs": state.get("repairs", 0) + 1,
                    "repair_code": "unsupported_fact_reference_or_section",
                    "branch": "retry",
                }
            return {
                "plan": {"schema_version": "report-plan-v1", "sections": []},
                "branch": "persist_result",
                "issues": sorted(set(state.get("issues", []) + ["unsupported_report_claim"])),
            }
        return {"branch": "persist_result"}

    def persist(state):
        job, solver, _, configs = context(state)
        allowed = facts(state)
        plan = ReportPlan.model_validate(state["plan"])
        issues = sorted(set(state.get("issues", [])))
        sections = [
            ReportSection(title=s.section, facts=[allowed[f] for f in s.fact_ids])
            for s in plan.sections
        ]
        with sessions() as db:
            solver_issues = [
                Issue.model_validate(x.payload)
                for x in db.scalars(
                    select(AnalysisIssue)
                    .where(AnalysisIssue.analysis_id == solver.id)
                    .order_by(AnalysisIssue.id)
                )
            ]
        report = GroundedReport(
            run_id=job.id,
            analysis_id=solver.id,
            solver_status=solver.status,
            solver_outcome=solver.outcome,
            status="needs_review" if issues else solver.status,
            outcome="needs_review" if issues else solver.outcome,
            sections=sections,
            configurations=[Configuration.model_validate(c) for c in configs],
            rejected=[Rejection.model_validate(r) for r in solver.summary.get("rejected", [])],
            solver_issues=solver_issues,
            review_blockers=issues,
            clarification_questions=[
                f"Resolve {code.replace('_', ' ')} before approval." for code in issues
            ],
            retrieval_ids=state.get("retrieval_ids", []),
            versions=public_versions(job.config),
        )
        with sessions() as db:
            row = db.get(RagJob, job.id)
            row.result = report.model_dump(mode="json")
            db.commit()
        return {"phase": "report", "branch": "finalize"}

    def finalize(state):
        return {
            "branch": "human_review"
            if state.get("issues") and not state.get("acknowledged")
            else "end"
        }

    report_graph = StateGraph(GraphState)
    report_nodes = {
        "validate_grounding": grounding,
        "compose_report": compose,
        "apply_policy_guard": guard,
        "persist_result": persist,
        "finalize": finalize,
    }
    for name, fn in report_nodes.items():
        report_graph.add_node(name, audited(name, fn))
    report_graph.add_edge(START, "validate_grounding")
    for name, next_name in (
        ("validate_grounding", "compose_report"),
        ("compose_report", "apply_policy_guard"),
        ("apply_policy_guard", "persist_result"),
        ("persist_result", "finalize"),
    ):
        report_graph.add_conditional_edges(
            name,
            lambda s, n=next_name: (
                END if s.get("error_code") else "compose_report" if s["branch"] == "retry" else n
            ),
            sorted({END, "compose_report", next_name}),
        )
    report_graph.add_edge("finalize", END)
    compiled_report = report_graph.compile()
    graph = StateGraph(GraphState)
    for name, fn in {
        "parse_document": parse,
        "extract_requirements": extraction,
        "validate_requirements": validate,
        "retrieve_catalog": retrieve_catalog,
        "solve_configuration": solver,
        "generate_grounded_report": lambda s: compiled_report.invoke(s),
        "human_review": human,
    }.items():
        graph.add_node(name, audited(name, fn))
    graph.add_edge(START, "parse_document")
    for a, b in (
        ("parse_document", "extract_requirements"),
        ("extract_requirements", "validate_requirements"),
        ("retrieve_catalog", "solve_configuration"),
        ("solve_configuration", "generate_grounded_report"),
    ):
        graph.add_conditional_edges(a, lambda s, n=b: END if s.get("error_code") else n, [b, END])
    graph.add_conditional_edges(
        "validate_requirements",
        lambda s: END if s.get("error_code") else s["branch"],
        ["retrieve_catalog", "human_review", END],
    )
    graph.add_conditional_edges(
        "generate_grounded_report",
        lambda s: "human_review" if s.get("branch") == "human_review" else END,
        ["human_review", END],
    )
    graph.add_conditional_edges(
        "human_review",
        lambda s: END if s.get("error_code") or s.get("phase") == "report" else "retrieve_catalog",
        ["retrieve_catalog", END],
    )
    return graph.compile(checkpointer=DatabaseSaver(sessions))


def execute_graph(
    job_id, session_factory=SessionLocal, settings=None, adapter=None, embedding=None
):
    settings = settings or get_settings()
    with session_factory() as db:
        row = db.get(RagJob, job_id)
        if not row or row.job_type != "graph":
            return
        claimed = db.execute(
            update(RagJob)
            .where(RagJob.id == job_id, RagJob.state == "queued")
            .values(state="running", attempts=RagJob.attempts + 1)
        )
        db.commit()
        if claimed.rowcount != 1:
            return
        decision = row.decision
    try:
        if row.config != configuration(settings):
            raise ProviderFailure("worker_config_mismatch")
        graph = build_graph(session_factory, settings, adapter, embedding)
        value = (
            Command(resume=decision["decision"])
            if decision
            else {
                "run_id": job_id,
                "issues": [],
                "retrieval_ids": [],
                "repairs": 0,
                "transient_retries": 0,
            }
        )
        with tracing_context(enabled=False):
            result = graph.invoke(
                value,
                {"configurable": {"thread_id": job_id}, "callbacks": [], "recursion_limit": 80},
            )
        with session_factory() as db:
            row = db.get(RagJob, job_id)
            if result.get("__interrupt__"):
                row.state = "awaiting_review"
            elif result.get("error_code"):
                row.state = "failed"
                row.error_code = result["error_code"]
            else:
                row.state = "completed"
            db.commit()
    except Exception as exc:  # noqa: BLE001 -- safe provider/worker boundary
        with session_factory() as db:
            row = db.get(RagJob, job_id)
            row.state = "failed"
            row.error_code = exc.code if isinstance(exc, ProviderFailure) else "graph_failure"
            db.commit()


if __name__ == "__main__":
    print(build_graph(SessionLocal, get_settings()).get_graph().draw_mermaid())
