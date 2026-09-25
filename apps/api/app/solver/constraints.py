from app.solver.contracts import Capability
from app.solver.explanations import issue


def availability(snapshot, selected):
    issues, lead = [], 0
    for s in selected:
        item = s.item
        if item.lifecycle_status != "active":
            issues.append(issue("inactive_or_deprecated_component", "hard", item.id))
        if (
            item.availability != "in_stock"
            or item.inventory_qty < s.quantity * snapshot.policy.fleet_quantity
        ):
            issues.append(issue("inventory_unavailable", "hard", item.id))
        if item.supplier_active is False:
            issues.append(issue("supplier_unavailable", "hard", item.id))
        lead = max(lead, item.lead_time_days)
    lead += snapshot.policy.integration_days
    return (
        issues,
        lead,
        {
            "quantity": Capability(value=snapshot.policy.fleet_quantity, unit="1", item_ids=[]),
            "delivery_s": Capability(
                value=lead * 86400, unit="s", item_ids=[s.item.id for s in selected]
            ),
        },
    )
