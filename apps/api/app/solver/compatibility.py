from itertools import combinations

from app.solver.contracts import Issue, Rule, Selection
from app.solver.explanations import issue
from app.solver.numbers import scaled


def check_compatibility(
    selected: list[Selection], rules: list[Rule], complete=False
) -> list[Issue]:
    issues = []
    ids = {s.item.id for s in selected}
    for rule in rules:
        if {rule.from_item_id, rule.to_item_id} <= ids:
            if rule.rule_type == "incompatible":
                issues.append(
                    issue("explicit_incompatibility", "hard", rule.from_item_id, rule.to_item_id)
                )
            elif rule.constraints:
                issues.append(
                    issue(
                        "compatibility_rule_predicate_unverified",
                        "review",
                        rule.from_item_id,
                        rule.to_item_id,
                    )
                )
    by = {s.item.category: s for s in selected}
    motor, esc, battery = (by.get(c) for c in ("motor", "esc", "battery"))
    for consumer in (motor, esc):
        if not consumer or not battery:
            continue
        lo = consumer.item.specs.get("min_voltage_v", consumer.item.specs.get("voltage_v"))
        hi = consumer.item.specs.get("max_voltage_v", consumer.item.specs.get("voltage_v"))
        voltage = battery.item.specs.get("voltage_v")
        if None in (lo, hi, voltage):
            issues.append(issue("voltage_unverified", "review", consumer.item.id, battery.item.id))
        elif not scaled(lo, 1000) <= scaled(voltage, 1000) <= scaled(hi, 1000):
            issues.append(
                issue("battery_voltage_incompatible", "hard", consumer.item.id, battery.item.id)
            )
    if motor and esc:
        a, b = motor.item.specs.get("max_current_a"), esc.item.specs.get("max_current_a")
        if a is None or b is None:
            issues.append(
                issue("motor_esc_current_unverified", "review", motor.item.id, esc.item.id)
            )
        elif scaled(a, 1000) > scaled(b, 1000):
            issues.append(issue("esc_current_below_motor", "hard", motor.item.id, esc.item.id))
    if motor and battery:
        a, b = motor.item.specs.get("max_current_a"), battery.item.specs.get("max_current_a")
        if a is None or b is None:
            issues.append(issue("battery_current_unverified", "review", battery.item.id))
        elif scaled(a, 1000) * motor.quantity > scaled(b, 1000):
            issues.append(
                issue("battery_current_insufficient", "hard", battery.item.id, motor.item.id)
            )
        cells, voltage = battery.item.specs.get("cells"), battery.item.specs.get("voltage_v")
        if cells is None or voltage is None:
            issues.append(issue("battery_cells_unverified", "review", battery.item.id))
        elif not scaled(cells) * 3000 <= scaled(voltage, 1000) <= scaled(cells) * 4300:
            issues.append(issue("battery_cell_voltage_inconsistent", "hard", battery.item.id))
    platform = next((s.item for s in selected if s.item.category in ("platform", "airframe")), None)
    if platform:
        interfaces = platform.specs.get("interfaces")
        for s in selected:
            if s.item == platform:
                continue
            interface = s.item.specs.get("interface")
            if not interface or not interfaces:
                if complete:
                    issues.append(issue("interface_unverified", "review", platform.id, s.item.id))
            elif interface not in interfaces:
                issues.append(issue("interface_incompatible", "hard", platform.id, s.item.id))
    # Duplicate selections would hide quantities and invalidate pair checks.
    if any(a.item.id == b.item.id for a, b in combinations(selected, 2)):
        issues.append(issue("duplicate_component_selection", "hard"))
    return issues
