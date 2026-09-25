from decimal import ROUND_CEILING, Decimal

from pydantic import ValidationError

from app.solver.classification import mapping
from app.solver.contracts import Capability, Envelope
from app.solver.explanations import issue
from app.solver.numbers import checked, derate, scaled


def engineering(snapshot, platform, selected):
    issues, caps = [], {}
    by = {s.item.category: s for s in selected}
    payload_g = sum(
        s.item.weight_g * s.quantity for s in selected if s.item.category in ("camera", "payload")
    )
    demand = 0
    for record in snapshot.requirements:
        req = record.requirement
        if (
            req.category == "payload"
            and mapping(req)
            and req.normalized_unit == "kg"
            and req.semantics == "mandatory"
        ):
            target = req.normalized_value
            target = target[0] if isinstance(target, list) else target
            if type(target) in (int, float) and req.operator in ("minimum", "range", "exact"):
                demand = max(
                    demand,
                    int((Decimal(str(target)) * 1000).to_integral_value(rounding=ROUND_CEILING)),
                )
    carried = max(payload_g, demand)
    total = checked(
        sum(s.item.weight_g * s.quantity for s in selected) + max(0, demand - payload_g)
    )

    def cap(key, value, unit, ids=None):
        caps[key] = Capability(value=value, unit=unit, item_ids=ids or [platform.id])

    cap("total_weight_g", total, "g")
    cap("platform_type", platform.specs["assembly"]["platform_type"], "none")
    for key, measured, code in (
        ("mtow_kg", total, "mtow_exceeded"),
        ("payload_kg", carried, "payload_capacity_exceeded"),
    ):
        if key not in platform.specs:
            issues.append(issue(key + "_unverified", "review", platform.id))
        else:
            limit = scaled(platform.specs[key], 1000)
            if key == "mtow_kg":
                cap("mtow_g", limit, "g")
                limit = derate(limit, snapshot.policy.mass_margin_bps)
            else:
                cap("payload_g", limit, "g")
            if measured > limit:
                issues.append(issue(code, "hard", platform.id))
    try:
        envelope = Envelope.model_validate(platform.specs.get("envelope"))
    except ValidationError:
        envelope = None
        issues.append(issue("performance_envelope_unverified", "review", platform.id))
    if envelope:
        if total > envelope.max_mass_g or carried > envelope.max_payload_g:
            issues.append(issue("performance_envelope_conditions_exceeded", "review", platform.id))
        else:
            battery = by.get("battery")
            voltage = battery.item.specs.get("voltage_v") if battery else None
            capacity = battery.item.specs.get("capacity_c") if battery else None
            if voltage is None or capacity is None:
                issues.append(issue("battery_energy_unverified", "review", platform.id))
            else:
                # V * C = J; milli-V times milli-C = micro-J, integer floor is conservative.
                energy_j = scaled(voltage, 1000) * scaled(capacity, 1000) // 1000000
                usable = derate(energy_j, snapshot.policy.battery_reserve_bps)
                seconds = usable // envelope.average_power_w
                declared = derate(envelope.endurance_s, snapshot.policy.endurance_margin_bps)
                cap("endurance_s", min(seconds, declared), "s", [platform.id, battery.item.id])
                cap("usable_energy_j", usable, "J", [battery.item.id])
                cap("average_power_w", envelope.average_power_w, "W")
                max_current = battery.item.specs.get("max_current_a")
                if max_current is None:
                    issues.append(issue("power_budget_unverified", "review", battery.item.id))
                elif (
                    scaled(voltage, 1000) * scaled(max_current, 1000)
                    < envelope.average_power_w * 1000000
                ):
                    issues.append(issue("power_budget_exceeded", "hard", battery.item.id))
                # Range is usable only within the declared endurance/energy envelope.
                if seconds >= declared:
                    cap("range_m", derate(envelope.range_m, snapshot.policy.range_margin_bps), "m")
                else:
                    issues.append(issue("range_energy_envelope_unverified", "review", platform.id))
    for source, target, unit, scale in [
        ("altitude_m", "altitude_m", "m", 1),
        ("speed_m_s", "speed_mm_s", "mm/s", 1000),
        ("wind_m_s", "wind_mm_s", "mm/s", 1000),
        ("warranty_s", "warranty_s", "s", 1),
    ]:
        if source in platform.specs:
            cap(target, scaled(platform.specs[source], scale), unit)
    if "min_temperature_k" in platform.specs and "max_temperature_k" in platform.specs:
        cap(
            "temperature_mk",
            [scaled(platform.specs[x], 1000) for x in ("min_temperature_k", "max_temperature_k")],
            "mK",
        )
    for field in ("ip_rating", "compliance", "testing"):
        if field in platform.specs:
            cap(field, platform.specs[field], "none")
    for category, source, target, unit in [
        ("communication", "range_m", "communication_range_m", "m"),
        ("navigation", "constellation", "navigation", "none"),
        ("camera", "sensor", "sensor", "none"),
        ("payload", "sensor", "sensor", "none"),
    ]:
        if category in by and source in by[category].item.specs:
            value = by[category].item.specs[source]
            cap(target, scaled(value) if unit == "m" else value, unit, [by[category].item.id])
    return total, carried, caps, issues
