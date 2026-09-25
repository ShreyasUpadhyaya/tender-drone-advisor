from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.solver.contracts import (
    BOMLine,
    Configuration,
    Contract,
    CostBreakdown,
    Evaluation,
    ISODate,
    Issue,
    Outcome,
    Rejection,
    SolverPolicy,
    Status,
)


class AnalysisCreate(Contract):
    extraction_run_id: UUID = Field(strict=False)
    catalog_version_id: UUID = Field(strict=False)
    analysis_date: ISODate
    policy: SolverPolicy = Field(default_factory=SolverPolicy)


class AnalysisSummary(Contract):
    examined_combinations: int = 0
    configuration_count: int = 0
    valid_count: int = 0
    provisional_count: int = 0
    top_k: int = 0
    truncated_top_k: bool = False
    options: dict[str, str] = Field(default_factory=dict)
    rejected: list[Rejection] = Field(default_factory=list)


class AnalysisResponse(Contract):
    id: str
    identity: str
    extraction_run_id: str
    catalog_version_id: str
    solver_version: str
    state: Literal["queued", "running", "completed", "failed"]
    status: Status | None
    outcome: Outcome | None
    error_code: str | None
    attempts: int
    summary: AnalysisSummary
    created_at: datetime
    finished_at: datetime | None
    idempotent: bool = False


class ConfigurationList(Contract):
    analysis_id: str
    total: int
    offset: int
    limit: int
    configurations: list[Configuration]


class BOMResponse(Contract):
    configuration_id: str
    lines: list[BOMLine]
    cost: CostBreakdown


class EvaluationResponse(Contract):
    configuration_id: str
    evaluations: list[Evaluation]


class AnalysisIssuesResponse(Contract):
    analysis_id: str
    issues: list[Issue]
    rejected: list[Rejection]
