from app.solver.contracts import BOMLine
from app.solver.explanations import issue
from app.solver.numbers import checked


def construct_bom(snapshot, selected):
    lines, issues = [], []
    for s in sorted(selected, key=lambda x: (x.item.sku, x.item.item_version)):
        item = s.item
        quantity = checked(s.quantity * snapshot.policy.fleet_quantity)
        valid = [
            p
            for p in item.prices
            if p.effective_from <= snapshot.analysis_date
            and (p.effective_to is None or snapshot.analysis_date <= p.effective_to)
        ]
        # Overlapping prices (including currencies) are an ambiguity, never cheapest-price shopping.
        price = valid[0] if len(valid) == 1 else None
        if price is None:
            issues.append(
                issue(
                    "ambiguous_effective_price" if valid else "missing_or_expired_price",
                    "review",
                    item.id,
                )
            )
        elif price.currency != snapshot.policy.cost.currency:
            issues.append(issue("currency_conversion_required", "review", item.id))
        amount = price.amount_paise if price else None
        subtotal = checked(quantity * amount) if amount is not None else None
        lines.append(
            BOMLine(
                item_id=item.id,
                catalog_version_id=item.catalog_version_id,
                item_version=item.item_version,
                sku=item.sku,
                name=item.name,
                quantity_per_drone=s.quantity,
                quantity=quantity,
                unit_weight_g=item.weight_g,
                unit_cost_paise=amount,
                currency=price.currency if price else None,
                price_id=price.id if price else None,
                price_effective_from=price.effective_from if price else None,
                price_effective_to=price.effective_to if price else None,
                subtotal_paise=subtotal,
                inventory_qty=item.inventory_qty,
                lead_time_days=item.lead_time_days,
            )
        )
    return lines, issues
