from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.analysis.schemas import AnalysisResponse
from app.extraction.contracts import Category, Operator, Scalar
from app.solver.contracts import Contract, ISODate, SolverPolicy

ExternalUUID = Annotated[UUID, Field(strict=False)]


class AssumptionInput(Contract):
    category: Category
    attribute: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    operator: Operator
    value: Scalar | list[int | float | str]
    unit: str = Field(max_length=32)
    rationale: str = Field(min_length=4, max_length=1000)
    provenance: Literal["internal_assumption", "catalog_limit", "procurement_record"] = (
        "internal_assumption"
    )

    @model_validator(mode="after")
    def explicit_unit(self):
        if self.operator in ("minimum", "maximum", "exact", "range") and not self.unit.strip():
            raise ValueError("A numeric scenario assumption requires an explicit unit.")
        return self


class ScenarioCreate(Contract):
    extraction_run_id: UUID = Field(strict=False)
    catalog_version_id: UUID = Field(strict=False)
    analysis_date: ISODate
    name: str = Field(min_length=3, max_length=160)
    intent: Literal["baseline", "cost_optimized", "performance_oriented"] = "baseline"
    reviewer: str | None = Field(default=None, min_length=1, max_length=80)
    rationale: str = Field(min_length=4, max_length=1000)
    assumptions: list[AssumptionInput] = Field(default_factory=list, max_length=30)
    component_skus: list[str] = Field(default_factory=list, max_length=12)
    inventory_record_ids: list[ExternalUUID] = Field(default_factory=list, max_length=50)
    policy: SolverPolicy = Field(default_factory=SolverPolicy)


class ScenarioAssumptionResponse(Contract):
    id: str
    category: str
    attribute: str
    operator: str
    original_value: Scalar | list[int | float | str]
    original_unit: str
    normalized_value: Scalar | list[int | float | str]
    normalized_unit: str
    rationale: str
    provenance: str


class ScenarioResponse(Contract):
    id: str
    scenario_id: str
    version_number: int
    intent: str
    rationale: str
    reviewer: str
    component_preferences: list[str]
    inventory_version_ids: list[str]
    inventory_overlays: list[dict]
    source_extraction_run_id: str
    scenario_extraction_run_id: str
    analysis: AnalysisResponse
    tender_requirements_verified: bool
    engineering_catalog_feasibility: str
    assumptions_outstanding: bool
    bid_compliance_review_required: bool = True
    assumptions: list[ScenarioAssumptionResponse]
