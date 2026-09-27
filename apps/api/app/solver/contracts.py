from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, model_validator

from app.catalog.schemas import canonical_json_date
from app.extraction.contracts import ValidatedRequirement

SOLVER_VERSION = "solver-v1.0.1"
MAX_INTEGER = 2**63 - 1


def parse_date(value: object) -> date | None:
    value = canonical_json_date(value)
    return date.fromisoformat(value) if isinstance(value, str) else value


ISODate = Annotated[date, BeforeValidator(parse_date)]
NonNegative = Annotated[int, Field(ge=0, le=MAX_INTEGER)]
Status = Literal["feasible", "conditionally_feasible", "infeasible", "needs_review"]
Outcome = Literal["feasible", "feasible_with_changes", "not_feasible", "needs_review"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Weights(Contract):
    cost: int = Field(default=4, ge=0, le=100)
    weight: int = Field(default=2, ge=0, le=100)
    overspec: int = Field(default=1, ge=0, le=100)
    lead_time: int = Field(default=1, ge=0, le=100)
    inventory_risk: int = Field(default=1, ge=0, le=100)
    coverage: int = Field(default=3, ge=0, le=100)


class CostPolicy(Contract):
    version: Literal["cost-v1"] = "cost-v1"
    currency: Literal["INR"] = "INR"
    engineering_integration_paise: NonNegative = 0
    labour_paise_per_drone: NonNegative = 0
    overhead_bps: int = Field(default=0, ge=0, le=10000)
    contingency_bps: int = Field(default=1000, ge=0, le=10000)
    tax_bps: int = Field(default=0, ge=0, le=10000)
    margin_bps: int = Field(default=0, ge=0, le=10000)


class SolverPolicy(Contract):
    version: Literal["policy-v1"] = "policy-v1"
    fleet_quantity: int = Field(default=1, ge=1, le=100000)
    top_k: int = Field(default=5, ge=1, le=50)
    max_combinations: int = Field(default=2000, ge=1, le=10000)
    max_items: int = Field(default=500, ge=1, le=2000)
    max_requirements: int = Field(default=500, ge=1, le=1000)
    max_evaluations: int = Field(default=50000, ge=1, le=100000)
    range_margin_bps: int = Field(default=1000, ge=0, le=5000)
    endurance_margin_bps: int = Field(default=1000, ge=0, le=5000)
    mass_margin_bps: int = Field(default=500, ge=0, le=5000)
    battery_reserve_bps: int = Field(default=2000, ge=0, le=5000)
    integration_days: int = Field(default=0, ge=0, le=3650)
    nonblocking_assumptions: list[str] = Field(default_factory=list, max_length=10)
    weights: Weights = Field(default_factory=Weights)
    cost: CostPolicy = Field(default_factory=CostPolicy)


class ItemRef(Contract):
    sku: str
    item_version: int = Field(ge=1)


class Slot(Contract):
    category: Literal[
        "motor",
        "esc",
        "propeller",
        "battery",
        "flight_controller",
        "navigation",
        "communication",
        "camera",
        "payload",
        "accessory",
        "gcs_software",
        "certification",
    ]
    quantity: int = Field(ge=1, le=32)
    options: list[ItemRef] = Field(min_length=1, max_length=30)
    baseline: ItemRef | None = None

    @model_validator(mode="after")
    def refs(self):
        keys = [(x.sku, x.item_version) for x in self.options]
        if len(keys) != len(set(keys)) or (self.baseline and self.baseline not in self.options):
            raise ValueError("slot references must be unique; baseline must be an option")
        return self


class Assembly(Contract):
    # An explicitly declared assembly, never an inferred collection of unrelated parts.
    slots: list[Slot] = Field(min_length=1, max_length=12)
    platform_type: Literal[
        "multirotor"
    ]  # Other architectures require explicit new engineering rules.
    rotor_count: int = Field(ge=2, le=16)
    provenance: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def structure(self):
        by_category = {s.category: s for s in self.slots}
        required = {
            "motor",
            "esc",
            "propeller",
            "battery",
            "flight_controller",
            "navigation",
            "communication",
        }
        if len(by_category) != len(self.slots) or not required <= by_category.keys():
            raise ValueError("assembly requires unique slots and all flight-critical categories")
        if any(by_category[c].quantity != self.rotor_count for c in ("motor", "esc", "propeller")):
            raise ValueError("motor/ESC/propeller counts must match rotor_count")
        if any(by_category[c].quantity != 1 for c in ("battery", "flight_controller")):
            raise ValueError("v1 supports one battery bus and one flight controller")
        return self


class Envelope(Contract):
    # Declared applicable to ALL listed assembly options, within these mass/load conditions.
    max_mass_g: NonNegative
    max_payload_g: NonNegative
    range_m: NonNegative
    endurance_s: NonNegative
    average_power_w: int = Field(gt=0, le=MAX_INTEGER)
    provenance: str = Field(min_length=1, max_length=500)


class Price(Contract):
    id: str
    amount_paise: NonNegative
    currency: str
    effective_from: ISODate
    effective_to: ISODate | None = None


class Item(Contract):
    id: str
    catalog_version_id: str
    sku: str
    item_version: int
    category: str
    name: str
    manufacturer: str
    weight_g: NonNegative
    lifecycle_status: str
    availability: str
    inventory_qty: NonNegative
    lead_time_days: NonNegative
    supplier_id: str | None = None
    supplier_active: bool | None = None
    specs: dict[str, JsonValue]
    provenance: dict[str, JsonValue]
    prices: list[Price]


class Rule(Contract):
    id: str
    from_item_id: str
    to_item_id: str
    rule_type: str
    constraints: dict[str, JsonValue]
    reason: str


class Requirement(Contract):
    id: str
    extraction_run_id: str
    requirement: ValidatedRequirement


class Issue(Contract):
    code: str
    severity: Literal["hard", "review", "assumption"]
    detail: str
    item_ids: list[str] = Field(default_factory=list)
    requirement_id: str | None = None
    question: str | None = None


class Snapshot(Contract):
    schema_version: Literal["analysis-input-v1"] = "analysis-input-v1"
    extraction_run_id: str
    document_version_id: str
    extraction_schema_version: str
    prompt_version: str
    model_identifier: str
    extraction_state: str
    extraction_review_state: str
    catalog_version_id: str
    catalog_version: str
    catalog_effective_from: ISODate | None = None
    catalog_effective_to: ISODate | None = None
    analysis_date: ISODate
    solver_version: Literal["solver-v1.0.1"] = SOLVER_VERSION
    policy: SolverPolicy
    requirements: list[Requirement]
    items: list[Item]
    rules: list[Rule]
    source_issues: list[Issue] = Field(default_factory=list)


class Selection(Contract):
    item: Item
    quantity: int


class Capability(Contract):
    value: int | str | bool | list[int] | list[str]
    unit: str
    item_ids: list[str]


class Evaluation(Contract):
    extraction_run_id: str
    requirement_id: str
    requirement: ValidatedRequirement
    result: Literal["satisfied", "exceeded", "failed", "unknown", "not_applicable"]
    actual: Capability | None
    margin: str | None
    reason_code: str
    explanation: str


class BOMLine(Contract):
    item_id: str
    catalog_version_id: str
    item_version: int
    sku: str
    name: str
    quantity_per_drone: int
    quantity: int
    unit_weight_g: int
    unit_cost_paise: int | None
    currency: str | None
    price_id: str | None
    price_effective_from: ISODate | None
    price_effective_to: ISODate | None
    subtotal_paise: int | None
    inventory_qty: int
    lead_time_days: int


class CostBreakdown(Contract):
    policy: CostPolicy
    currency: Literal["INR"] = "INR"
    material_paise: int | None
    engineering_integration_paise: int
    labour_paise: int
    overhead_paise: int | None
    contingency_paise: int | None
    tax_paise: int | None
    margin_paise: int | None
    total_paise: int | None


class Configuration(Contract):
    id: str
    platform_id: str
    selections: list[Selection]
    status: Status
    outcome: Outcome
    modified: bool
    recommended: bool = False
    rank: int = 0
    pareto: bool = False
    labels: list[str] = Field(default_factory=list)
    tie_break: str
    bom: list[BOMLine]
    cost: CostBreakdown
    total_weight_g: int
    payload_g: int
    lead_time_days: int
    immediately_buildable: bool
    compatibility: Literal["passed", "failed", "unverified"]
    capabilities: dict[str, Capability]
    requirement_coverage_bps: int
    evaluations: list[Evaluation]
    issues: list[Issue]
    objective_breakdown: dict[str, int] = Field(default_factory=dict)
    objective_score: int = 0


class Rejection(Contract):
    platform_id: str
    item_ids: list[str]
    issues: list[Issue]


class SolveResult(Contract):
    schema_version: Literal["analysis-result-v1"] = "analysis-result-v1"
    status: Status
    outcome: Outcome
    configurations: list[Configuration]
    rejected: list[Rejection]
    issues: list[Issue]
    examined_combinations: int
    valid_count: int
    provisional_count: int
    truncated_top_k: bool
    # All bounded results are retained; list APIs paginate with a default top-K page.
    options: dict[str, str] = Field(default_factory=dict)
