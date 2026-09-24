"""Pure deterministic validation. Model output never supplies normalized values or anchors."""

import math
import re
from itertools import combinations

from app.extraction.contracts import Evidence, RequirementCandidate, ValidationIssue, numeric

# unit -> canonical unit, scale, offset. Time is seconds, temperature is kelvin.
UNITS = {
    "m": ("m", 1, 0),
    "km": ("m", 1000, 0),
    "ft": ("m", 0.3048, 0),
    "s": ("s", 1, 0),
    "sec": ("s", 1, 0),
    "min": ("s", 60, 0),
    "h": ("s", 3600, 0),
    "hours": ("s", 3600, 0),
    "days": ("s", 86400, 0),
    "kg": ("kg", 1, 0),
    "g": ("kg", 0.001, 0),
    "m/s": ("m/s", 1, 0),
    "km/h": ("m/s", 1 / 3.6, 0),
    "k": ("K", 1, 0),
    "c": ("K", 1, 273.15),
    "°c": ("K", 1, 273.15),
    "w": ("W", 1, 0),
    "kw": ("W", 1000, 0),
    "j": ("J", 1, 0),
    "wh": ("J", 3600, 0),
    "ah": ("C", 3600, 0),
    "mah": ("C", 3.6, 0),
    "v": ("V", 1, 0),
    "count": ("1", 1, 0),
    "1": ("1", 1, 0),
    "inr": ("paise", 100, 0),
    "paise": ("paise", 1, 0),
}
DIMENSIONS = {
    "range": {"m"},
    "altitude": {"m"},
    "endurance": {"s"},
    "delivery": {"s"},
    "warranty": {"s"},
    "mtow": {"kg"},
    "payload": {"kg"},
    "speed": {"m/s"},
    "wind_tolerance": {"m/s"},
    "temperature": {"K"},
    "battery": {"J", "C", "V"},
    "propulsion": {"W"},
    "quantity": {"1"},
    "commercial": {"paise"},
}
REQUIRED_CATEGORIES = {"platform", "range", "endurance", "payload"}


def issue(code: str, index: int | None, detail: str, blocking: bool = True) -> ValidationIssue:
    return ValidationIssue(code=code, requirement_index=index, detail=detail, blocking=blocking)


def normalize(candidate: RequirementCandidate) -> tuple[object, str]:
    value = candidate.original_value
    if value == "unknown":
        return "unknown", "unknown"
    if candidate.operator in ("text", "enum", "boolean"):
        if candidate.original_unit not in ("", "none", "unknown"):
            raise ValueError("unexpected_unit")
        return value, "1" if candidate.operator == "boolean" else "none"
    unit = candidate.original_unit.strip().lower()
    if unit not in UNITS:
        raise ValueError("unsupported_unit")
    canonical, scale, offset = UNITS[unit]
    if canonical not in DIMENSIONS.get(candidate.category, set()):
        raise ValueError("unit_dimension_mismatch")
    values = value if isinstance(value, list) else [value]
    normalized = [numeric(number) * scale + offset for number in values]
    if any(not math.isfinite(number) or number < 0 for number in normalized):
        raise ValueError("invalid_numeric_value")
    if canonical in ("paise", "1"):
        if any(
            not math.isclose(number, round(number), abs_tol=1e-8, rel_tol=0)
            for number in normalized
        ):
            raise ValueError("noninteger_count_or_money")
        normalized = [round(number) for number in normalized]
    return (normalized if isinstance(value, list) else normalized[0]), canonical


def validate_evidence(candidate: RequirementCandidate, spans: dict[str, dict]) -> list[Evidence]:
    if not candidate.evidence:
        raise ValueError("missing_evidence")
    result = []
    for citation in candidate.evidence:
        span = spans.get(citation.span_id)
        if span is None or citation.quote not in span["text"] or not citation.quote.strip():
            raise ValueError("invalid_evidence")
        start = span["text"].index(citation.quote)
        result.append(
            Evidence(
                document_id=span["document_id"],
                document_version_id=span["document_version_id"],
                span_id=span["id"],
                page_number=span["page_number"],
                section_name=span["section_name"],
                chunk_index=span["chunk_index"],
                quote=citation.quote,
                quote_start=start,
                quote_end=start + len(citation.quote),
            )
        )
    # An existing quote alone does not substantiate a made-up measurement.
    if (
        candidate.operator in ("minimum", "maximum", "exact", "range")
        and candidate.original_value != "unknown"
    ):
        numbers = [
            float(token)
            for quote in candidate.evidence
            for token in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\d.])", quote.quote)
        ]
        values = (
            candidate.original_value
            if isinstance(candidate.original_value, list)
            else [candidate.original_value]
        )
        if not all(
            any(math.isclose(float(value), number, rel_tol=0, abs_tol=1e-9) for number in numbers)
            for value in values
        ):
            raise ValueError("unsupported_numeric_claim")
        if not any(
            re.search(
                r"(?<![a-z°/])" + re.escape(candidate.original_unit.strip()) + r"(?![a-z/])",
                quote.quote,
                re.IGNORECASE,
            )
            for quote in candidate.evidence
        ):
            raise ValueError("unsupported_unit_claim")
    if (
        candidate.operator in ("text", "enum")
        and candidate.original_value != "unknown"
        and not any(
            candidate.original_value.casefold() in quote.quote.casefold()
            for quote in candidate.evidence
        )
    ):
        raise ValueError("unsupported_text_claim")
    return result


def detect_conflicts(requirements: list[dict]) -> list[ValidationIssue]:
    findings = []
    for left, right in combinations(requirements, 2):
        a, b = left["candidate"], right["candidate"]
        if (a.category, a.attribute) != (b.category, b.attribute):
            continue
        va, vb = left["value"], right["value"]
        if va == "unknown" or vb == "unknown":
            continue
        if (a.operator, a.semantics, va, left["unit"]) == (
            b.operator,
            b.semantics,
            vb,
            right["unit"],
        ):
            findings.append(
                issue(
                    "duplicate_requirement",
                    right["index"],
                    "Equivalent constraint repeated; both evidence links retained.",
                    False,
                )
            )
            continue
        if a.semantics != "mandatory" or b.semantics != "mandatory":
            continue
        if left["unit"] != right["unit"]:
            findings.append(
                issue(
                    "conflicting_dimensions",
                    right["index"],
                    "Same attribute has incompatible dimensions.",
                )
            )
            continue

        def interval(operator, value):
            if operator == "minimum":
                return value, math.inf
            if operator == "maximum":
                return -math.inf, value
            if operator == "exact":
                return value, value
            if operator == "range":
                return value[0], value[1]
            return None

        ia, ib = interval(a.operator, va), interval(b.operator, vb)
        conflict = max(ia[0], ib[0]) > min(ia[1], ib[1]) if ia and ib else va != vb
        if conflict:
            findings.append(
                issue(
                    "conflicting_requirement",
                    right["index"],
                    "Mandatory constraints for the same attribute disagree; review context.",
                )
            )
    return findings
