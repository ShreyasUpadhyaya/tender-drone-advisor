from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.catalog.schemas import CATEGORIES, canonical_json_date

ExternalUUID = Annotated[UUID, Field(strict=False)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InventoryRecordCreate(Strict):
    catalog_item_id: UUID | None = Field(default=None, strict=False)
    sku: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=200)
    category: CATEGORIES | None = None
    manufacturer: str | None = Field(default=None, max_length=160)
    on_hand_quantity: int = Field(ge=0)
    expected_quantity: int = Field(default=0, ge=0)
    expected_on: date | None = Field(default=None, strict=False)
    location: str = Field(min_length=2, max_length=160)
    rationale: str = Field(min_length=4, max_length=1000)
    extraction_run_id: UUID | None = Field(default=None, strict=False)
    requirement_ids: list[ExternalUUID] = Field(default_factory=list, max_length=100)
    requirement_categories: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("expected_on", mode="before")
    @classmethod
    def iso_date(cls, value: object) -> object:
        return canonical_json_date(value)

    @model_validator(mode="after")
    def valid_source(self):
        if self.expected_quantity and self.expected_on is None:
            raise ValueError("expected_on is required when expected_quantity is greater than zero")
        if self.expected_on is not None and self.expected_quantity == 0:
            raise ValueError("expected_quantity must be greater than zero when expected_on is set")
        if self.catalog_item_id is None and not all(
            value and str(value).strip()
            for value in (self.sku, self.name, self.category, self.manufacturer)
        ):
            raise ValueError("unlisted stock requires SKU, name, category and manufacturer")
        return self


class InventoryVersionCreate(Strict):
    on_hand_quantity: int = Field(ge=0)
    expected_quantity: int = Field(default=0, ge=0)
    expected_on: date | None = Field(default=None, strict=False)
    location: str = Field(min_length=2, max_length=160)
    rationale: str = Field(min_length=4, max_length=1000)
    extraction_run_id: UUID | None = Field(default=None, strict=False)
    requirement_ids: list[ExternalUUID] = Field(default_factory=list, max_length=100)
    requirement_categories: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("expected_on", mode="before")
    @classmethod
    def iso_date(cls, value: object) -> object:
        return canonical_json_date(value)

    @model_validator(mode="after")
    def valid_expected(self):
        if self.expected_quantity and self.expected_on is None:
            raise ValueError("expected_on is required when expected_quantity is greater than zero")
        if self.expected_on is not None and self.expected_quantity == 0:
            raise ValueError("expected_quantity must be greater than zero when expected_on is set")
        return self


class InventoryVersionResponse(Strict):
    id: str
    version_number: int
    on_hand_quantity: int
    expected_quantity: int
    expected_on: date | None
    location: str
    rationale: str
    extraction_run_id: str | None
    requirement_ids: list[str]
    requirement_categories: list[str]
    recorded_by_subject: str
    created_at: datetime


class InventoryRecordResponse(Strict):
    id: str
    catalog_item_id: str | None
    sku: str
    name: str
    category: str
    manufacturer: str
    source_status: Literal["catalog_linked", "pending_catalog_validation"]
    solver_eligible: bool
    owner_subject: str
    created_at: datetime
    current: InventoryVersionResponse
    history: list[InventoryVersionResponse]


class SessionResponse(Strict):
    subject: str
    workspace_id: str
    roles: list[str]
    demo: bool
