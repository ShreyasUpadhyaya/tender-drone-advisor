import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "requirements-v2"
PROMPT_VERSION = "extract-v2"
NORMALIZER_VERSION = "si-v1"

Category = Literal[
    "mission",
    "procurement",
    "platform",
    "range",
    "endurance",
    "payload",
    "mtow",
    "altitude",
    "speed",
    "propulsion",
    "battery",
    "environment",
    "wind_tolerance",
    "temperature",
    "ingress_protection",
    "sensors",
    "communication",
    "navigation",
    "integration",
    "compliance",
    "testing",
    "quantity",
    "delivery",
    "warranty",
    "commercial",
]
Semantics = Literal["mandatory", "preferred", "informational", "ambiguous"]
Operator = Literal["minimum", "maximum", "exact", "range", "boolean", "enum", "text"]
Scalar = float | int | bool | str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CitationCandidate(StrictModel):
    span_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=2000)


def numeric(value: object) -> int | float:
    """Only an unambiguous decimal literal; never parse units, commas or inequalities."""
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        value = float(value) if "." in value else int(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("numeric operator requires a finite number or decimal literal")
    return value


DecimalLiteral = Annotated[str, Field(pattern=r"^[+-]?\d+(?:\.\d+)?$")]


class ScalarValue(StrictModel):
    kind: Literal["scalar"]
    value: int | float | DecimalLiteral


class RangeValue(StrictModel):
    kind: Literal["range"]
    lower: int | float | DecimalLiteral
    upper: int | float | DecimalLiteral


class TextValue(StrictModel):
    kind: Literal["text"]
    value: str = Field(min_length=1)


class BooleanValue(StrictModel):
    kind: Literal["boolean"]
    value: bool


class UnknownValue(StrictModel):
    kind: Literal["unknown"]
    value: None


RawValue = ScalarValue | RangeValue | TextValue | BooleanValue | UnknownValue


class RequirementCandidate(StrictModel):
    category: Category
    attribute: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    semantics: Semantics
    operator: Operator
    original_value: Scalar | list[int | float | str]
    original_unit: str = Field(max_length=32)
    # Optional only for reading historical v1 records; v2 wire fields are all required.
    raw_value: RawValue | None = None
    raw_unit: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[CitationCandidate] = Field(max_length=8)

    @model_validator(mode="after")
    def value_shape(self) -> "RequirementCandidate":
        value = self.original_value
        if value == "unknown":
            return self
        if self.operator in ("minimum", "maximum", "exact"):
            numeric(value)
        elif self.operator == "range":
            if (
                not isinstance(value, list)
                or len(value) != 2
                or numeric(value[0]) > numeric(value[1])
            ):
                raise ValueError("range requires ordered lower and upper bounds")
        elif self.operator == "boolean":
            if type(value) is not bool:
                raise ValueError("boolean operator requires a boolean")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError("text/enum operator requires nonempty text")
        return self


class ExtractionBatch(StrictModel):
    schema_version: Literal["requirements-v2"]
    requirements: list[RequirementCandidate] = Field(max_length=100)


class WireRequirement(StrictModel):
    category: Category
    attribute: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    semantics: Semantics
    operator: Operator
    raw_value: RawValue
    original_unit: str | None = Field(max_length=32)
    confidence: float = Field(ge=0, le=1)
    evidence: list[CitationCandidate] = Field(max_length=8)

    def candidate(self) -> RequirementCandidate:
        raw = self.raw_value
        expected = {
            "minimum": "scalar",
            "maximum": "scalar",
            "exact": "scalar",
            "range": "range",
            "text": "text",
            "enum": "text",
            "boolean": "boolean",
        }[self.operator]
        if raw.kind not in (expected, "unknown"):
            raise ValueError("operator_value_kind_mismatch")
        value = (
            "unknown"
            if isinstance(raw, UnknownValue)
            else [raw.lower, raw.upper]
            if isinstance(raw, RangeValue)
            else raw.value
        )
        return RequirementCandidate(
            **self.model_dump(exclude={"raw_value", "original_unit"}),
            original_value=value,
            original_unit=self.original_unit if self.original_unit is not None else "unknown",
            raw_value=raw,
            raw_unit=self.original_unit,
        )


class NumericRequirement(WireRequirement):
    operator: Literal["minimum", "maximum", "exact"]
    raw_value: ScalarValue | UnknownValue


class RangeRequirement(WireRequirement):
    operator: Literal["range"]
    raw_value: RangeValue | UnknownValue


class TextRequirement(WireRequirement):
    operator: Literal["text", "enum"]
    raw_value: TextValue | UnknownValue


class BooleanRequirement(WireRequirement):
    operator: Literal["boolean"]
    raw_value: BooleanValue | UnknownValue


class WireBatch(StrictModel):
    """OpenAI schema: object root, nested anyOf, all keys required, no extra keys."""

    schema_version: Literal["requirements-v2"]
    requirements: list[
        NumericRequirement | RangeRequirement | TextRequirement | BooleanRequirement
    ] = Field(max_length=100)


class Evidence(StrictModel):
    document_id: str
    document_version_id: str
    span_id: str
    page_number: int | None
    section_name: str | None
    chunk_index: int
    quote: str
    # Offsets are relative to SourceSpan.text; end is exclusive.
    quote_start: int
    quote_end: int


class ValidatedRequirement(RequirementCandidate):
    normalized_value: Scalar | list[int] | list[float]
    normalized_unit: str
    normalization_version: Literal["si-v1"] = NORMALIZER_VERSION
    validated_evidence: list[Evidence]

    @model_validator(mode="after")
    def integer_money(self) -> "ValidatedRequirement":
        if self.normalized_unit == "paise":
            values = (
                self.normalized_value
                if isinstance(self.normalized_value, list)
                else [self.normalized_value]
            )
            if any(type(value) is not int for value in values):
                raise ValueError("money must be integer paise")
        return self


class ValidationIssue(StrictModel):
    code: str
    requirement_index: int | None = None
    blocking: bool = True
    detail: str


class ReviewDecision(StrictModel):
    decision: Literal["acknowledge", "request_reextraction"]
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=500)
