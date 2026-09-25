from app.solver.contracts import CostBreakdown
from app.solver.numbers import bps, checked


def cost_bom(lines, policy, quantity):
    material = (
        checked(sum(l.subtotal_paise for l in lines))
        if all(l.subtotal_paise is not None and l.currency == policy.currency for l in lines)
        else None
    )
    labour = checked(policy.labour_paise_per_drone * quantity)
    overhead = contingency = tax = margin = total = None
    if material is not None:
        base = checked(material + policy.engineering_integration_paise + labour)
        overhead = bps(base, policy.overhead_bps)
        contingency = bps(base, policy.contingency_bps)
        taxable = checked(base + overhead + contingency)
        tax, margin = bps(taxable, policy.tax_bps), bps(taxable, policy.margin_bps)
        total = checked(taxable + tax + margin)
    return CostBreakdown(
        policy=policy,
        material_paise=material,
        engineering_integration_paise=policy.engineering_integration_paise,
        labour_paise=labour,
        overhead_paise=overhead,
        contingency_paise=contingency,
        tax_paise=tax,
        margin_paise=margin,
        total_paise=total,
    )
