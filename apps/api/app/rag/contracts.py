from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.solver.contracts import Configuration, Contract, Issue, Outcome, Rejection, Status

GRAPH_VERSION = "rag-graph-v1.0.0"
PROMPT_VERSION = "grounded-report-v1"
CHUNK_VERSION = "span-chars-v1.0.0"
RETRIEVAL_VERSION = "hybrid-v1"
Kind = Literal["tender", "requirement", "catalog", "compatibility", "configuration", "rejection"]
KINDS = ["tender", "requirement", "catalog", "compatibility", "configuration", "rejection"]


class RetrievalPolicy(Contract):
    top_k: int = Field(default=10, ge=1, le=50)
    threshold_bps: int = Field(default=1000, ge=0, le=10000)
    vector_weight_bps: int = Field(default=6000, ge=0, le=10000)
    chunk_chars: int = Field(default=1600, ge=200, le=4000)
    max_chunks: int = Field(default=2000, ge=1, le=5000)
    max_repairs: int = Field(default=1, ge=0, le=1)
    transient_retries: int = Field(default=1, ge=0, le=3)


class IndexCreate(Contract):
    analysis_id: UUID = Field(strict=False)
    kinds: list[Kind] = Field(default_factory=lambda: list(KINDS), min_length=1)
    policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    allow_external: bool = False


class SearchCreate(Contract):
    index_id: UUID = Field(strict=False)
    document_version_id: UUID | None = Field(default=None, strict=False)
    catalog_version_id: UUID | None = Field(default=None, strict=False)
    query: str = Field(min_length=1, max_length=1000)
    kinds: list[Kind] = Field(default_factory=lambda: list(KINDS), min_length=1)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    item_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=10, ge=1, le=50)
    threshold_bps: int = Field(default=1000, ge=0, le=10000)
    allow_external: bool = False


class GroundedCreate(Contract):
    analysis_id: UUID = Field(strict=False)
    question: str = Field(default="summarize", min_length=1, max_length=1000)
    policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    allow_external: bool = False


class ResumeRequest(Contract):
    decision: Literal["continue_provisional"]
    reviewer: str = Field(min_length=1, max_length=100)


class Citation(Contract):
    document_id: str | None = None
    document_version_id: str | None = None
    span_id: str | None = None
    page_number: int | None = None
    section_name: str | None = None
    start: int | None = None
    end: int | None = None
    requirement_id: str | None = None
    catalog_version_id: str | None = None
    item_id: str | None = None
    item_version: int | None = None
    configuration_id: str | None = None
    analysis_id: str


class Fact(Contract):
    id: str
    kind: Kind
    text: str
    citation: Citation


class Hit(Contract):
    fact: Fact
    semantic_bps: int
    lexical_bps: int
    final_bps: int
    candidate_only: bool = True


class SearchResponse(Contract):
    id: str
    index_id: str
    state: str
    hits: list[Hit]
    issues: list[str]
    idempotent: bool = False


class JobResponse(Contract):
    id: str
    identity: str
    analysis_id: str
    state: str
    error_code: str | None
    attempts: int
    count: int = 0
    versions: dict[str, str | int]
    idempotent: bool = False


class ReportSelection(Contract):
    section: Literal["evidence", "catalog_context", "solver", "risks"]
    fact_ids: list[str]


class ReportPlan(Contract):
    # No free-form factual prose: model chooses organization, never facts or numbers.
    schema_version: Literal["report-plan-v1"]
    sections: list[ReportSelection]


class ReportSection(Contract):
    title: str
    facts: list[Fact]


class GroundedReport(Contract):
    schema_version: Literal["grounded-report-v1"] = "grounded-report-v1"
    run_id: str
    analysis_id: str
    solver_status: Status
    solver_outcome: Outcome
    status: Status
    outcome: Outcome
    approval: Literal["not_granted"] = "not_granted"
    sections: list[ReportSection]
    configurations: list[Configuration]
    rejected: list[Rejection]
    solver_issues: list[Issue]
    review_blockers: list[str]
    clarification_questions: list[str]
    retrieval_ids: list[str]
    versions: dict[str, str | int]


class AuditResponse(Contract):
    node: str
    attempt: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    elapsed_ms: int | None
    branch: str | None
    error_code: str | None
    summary: dict[str, int | str | list[str]]


class CitationsResponse(Contract):
    citations: list[Citation]


class IssuesResponse(Contract):
    review_blockers: list[str]
    clarification_questions: list[str]


class AuditList(Contract):
    run_id: str
    nodes: list[AuditResponse]
