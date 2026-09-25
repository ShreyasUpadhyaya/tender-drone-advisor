from decimal import Decimal

from app.solver.contracts import Capability, Evaluation, Requirement
from app.solver.explanations import issue

# No fuzzy attribute matching: unknown wording needs an explicit reviewed mapping.
MAPPINGS = {
    "platform": ({"platform", "platform_type"}, "platform_type", "none", 1),
    "range": ({"range", "range_m"}, "range_m", "m", 1),
    "endurance": ({"endurance", "endurance_s"}, "endurance_s", "s", 1),
    "payload": ({"payload", "payload_kg"}, "payload_g", "kg", 1000),
    "mtow": ({"mtow", "mtow_kg"}, "mtow_g", "kg", 1000),
    "altitude": ({"altitude", "altitude_m"}, "altitude_m", "m", 1),
    "speed": ({"speed", "speed_m_s"}, "speed_mm_s", "m/s", 1000),
    "communication": ({"range", "range_m", "communication_range"}, "communication_range_m", "m", 1),
    "navigation": ({"navigation", "constellation"}, "navigation", "none", 1),
    "sensors": ({"sensor", "sensors", "camera_type"}, "sensor", "none", 1),
    "wind_tolerance": ({"wind_tolerance", "wind_speed"}, "wind_mm_s", "m/s", 1000),
    "temperature": ({"temperature", "operating_temperature"}, "temperature_mk", "K", 1000),
    "ingress_protection": ({"ingress_protection", "ip_rating"}, "ip_rating", "none", 1),
    "compliance": ({"compliance", "standard"}, "compliance", "none", 1),
    "testing": ({"testing", "standard"}, "testing", "none", 1),
    "quantity": ({"quantity", "units"}, "quantity", "1", 1),
    "delivery": ({"delivery", "lead_time"}, "delivery_s", "s", 1),
    "warranty": ({"warranty", "warranty_duration"}, "warranty_s", "s", 1),
    "commercial": ({"commercial", "budget", "total_cost"}, "total_cost_paise", "paise", 1),
}


def mapping(requirement):
    entry = MAPPINGS.get(requirement.category)
    return entry if entry and requirement.attribute in entry[0] else None


def evaluate(record: Requirement, capabilities: dict[str, Capability]) -> Evaluation:
    req = record.requirement
    result, reason, actual, margin = "unknown", "unsupported_requirement", None, None
    entry = mapping(req)
    if req.semantics == "informational":
        result, reason = "not_applicable", "informational_only"
    elif req.semantics == "ambiguous" or req.normalized_value == "unknown":
        reason = "ambiguous_requirement"
    elif entry and req.normalized_unit == entry[2]:
        actual = capabilities.get(entry[1])
        if actual is None:
            reason = "capability_unverified"
        else:
            value, target, op = actual.value, req.normalized_value, req.operator
            match = None
            if op in ("enum", "text") and isinstance(target, str):
                if op == "enum":
                    match = (
                        target.casefold() in [str(v).casefold() for v in value]
                        if isinstance(value, list)
                        else str(value).casefold() == target.casefold()
                    )
                else:
                    reason = "text_clause_requires_review"
            elif op == "boolean" and type(value) is bool and type(target) is bool:
                match = value == target
            elif op in ("minimum", "maximum", "exact", "range"):
                if isinstance(value, list) and op == "range" and isinstance(target, list):
                    if (
                        len(value) == len(target) == 2
                        and all(type(x) in (int, float) for x in target)
                        and all(type(x) is int for x in value)
                    ):
                        lo, hi = [Decimal(str(x)) * entry[3] for x in target]
                        match = value[0] <= lo and value[1] >= hi
                        margin = str(min(lo - value[0], value[1] - hi))
                elif type(value) is int:
                    if op == "range" and isinstance(target, list) and len(target) == 2:
                        lo, hi = [Decimal(str(x)) * entry[3] for x in target]
                        match = lo <= value <= hi
                        margin = str(min(Decimal(value) - lo, hi - value))
                    elif type(target) in (int, float):
                        expected = Decimal(str(target)) * entry[3]
                        delta = Decimal(value) - expected
                        match = {
                            "minimum": delta >= 0,
                            "maximum": delta <= 0,
                            "exact": delta == 0,
                        }.get(op)
                        margin = str(-delta if op == "maximum" else delta)
            if match is not None:
                result = "satisfied" if match else "failed"
                if match and op == "minimum" and margin is not None and Decimal(margin) > 0:
                    result = "exceeded"
                reason = (
                    "constraint_passed"
                    if match
                    else "mandatory_constraint_failed"
                    if req.semantics == "mandatory"
                    else "preference_not_met"
                )
    elif entry:
        reason = "requirement_unit_mismatch"
    return Evaluation(
        extraction_run_id=record.extraction_run_id,
        requirement_id=record.id,
        requirement=req,
        result=result,
        actual=actual,
        margin=margin,
        reason_code=reason,
        explanation=f"{req.category}.{req.attribute}: {reason.replace('_', ' ')}; "
        f"result={result}; actual={actual.value if actual else 'unverified'} "
        f"{actual.unit if actual else ''}; margin={margin if margin is not None else 'unverified'}.",
    )


def requirement_issues(evaluations):
    issues = []
    for e in evaluations:
        if e.result == "failed" and e.requirement.semantics == "mandatory":
            issues.append(
                issue("mandatory_constraint_failed", "hard", requirement_id=e.requirement_id)
            )
        elif e.result == "unknown" and e.requirement.semantics in ("mandatory", "ambiguous"):
            issues.append(issue(e.reason_code, "review", requirement_id=e.requirement_id))
    return issues
