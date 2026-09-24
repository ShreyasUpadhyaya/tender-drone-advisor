from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CATALOG_SCHEMA_VERSION = "catalog-v1"
CATEGORIES = Literal[
    "platform",
    "airframe",
    "motor",
    "propeller",
    "esc",
    "battery",
    "flight_controller",
    "communication",
    "navigation",
    "payload",
    "camera",
    "accessory",
    "gcs_software",
    "certification",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CatalogVersionCreate(Strict):
    version: str = Field(min_length=1, max_length=40)
    status: Literal["draft", "current", "retired"] = "draft"
    source: str = "synthetic-demo"
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self


class SupplierCreate(Strict):
    supplier_code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CatalogItemCreate(Strict):
    catalog_version: str = Field(min_length=1, max_length=40)
    supplier_code: str | None = None
    sku: str = Field(min_length=1, max_length=120)
    item_version: int = Field(default=1, ge=1)
    manufacturer: str = Field(min_length=1, max_length=160)
    category: CATEGORIES
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    lifecycle_status: Literal["active", "deprecated", "inactive"] = "active"
    weight_grams: int = Field(ge=0)
    cost_paise: int = Field(ge=0)
    currency: Literal["INR", "USD", "EUR"] = "INR"
    inventory_qty: int = Field(ge=0)
    lead_time_days: int = Field(ge=0)
    availability: Literal["in_stock", "backorder", "unavailable"] = "in_stock"
    specs: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=lambda: {"source": "synthetic-demo"})


class CatalogItemResponse(CatalogItemCreate):
    id: UUID
    catalog_version_id: UUID
    created_at: datetime
    updated_at: datetime


class CompatibilityRuleCreate(Strict):
    from_sku: str = Field(min_length=1)
    from_version: int = Field(ge=1)
    to_sku: str = Field(min_length=1)
    to_version: int = Field(ge=1)
    rule_type: Literal["compatible", "incompatible"]
    reason: str = Field(min_length=1, max_length=500)
    constraints: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=lambda: {"source": "synthetic-demo"})


class CatalogImportRequest(Strict):
    idempotency_key: str = Field(min_length=8, max_length=128)
    version: CatalogVersionCreate
    suppliers: list[SupplierCreate] = Field(default_factory=list)
    items: list[CatalogItemCreate] = Field(min_length=1, max_length=500)
    compatibility_rules: list[CompatibilityRuleCreate] = Field(default_factory=list)
    prices: list["PriceCreate"] = Field(default_factory=list)


class PriceCreate(Strict):
    sku: str = Field(min_length=1)
    item_version: int = Field(ge=1)
    amount_paise: int = Field(ge=0)
    currency: Literal["INR", "USD", "EUR"] = "INR"
    effective_from: date = Field(strict=False)
    effective_to: date | None = Field(default=None, strict=False)

    @model_validator(mode="after")
    def date_order(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self


class CatalogImportResponse(Strict):
    catalog_version: str
    catalog_version_id: UUID
    item_ids: list[UUID]
    rule_count: int
    idempotent: bool


class CandidateRequirement(Strict):
    requirement_id: str | None = None
    category: str
    attribute: str
    operator: str
    normalized_value: Any
    normalized_unit: str
    semantics: str = "mandatory"
    evidence_ids: list[str] = Field(default_factory=list)


class RetrievalRequest(Strict):
    catalog_version: str | None = None
    requirements: list[CandidateRequirement] = Field(min_length=1)
    category: CATEGORIES | None = None
    include_unavailable: bool = False
    context_skus: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=20, ge=1, le=100)
