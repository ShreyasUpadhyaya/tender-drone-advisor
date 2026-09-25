from decimal import Decimal

from app.solver.classification import mapping


def objective_metrics(config):
    evaluated = [e for e in config.evaluations if e.result != "not_applicable"]
    satisfied = sum(e.result in ("satisfied", "exceeded") for e in evaluated)
    coverage = 10000 * satisfied // max(1, len(evaluated))
    config.requirement_coverage_bps = coverage
    overspec = 0
    for e in evaluated:
        if (
            e.result == "exceeded"
            and e.margin
            and type(e.requirement.normalized_value) in (int, float)
        ):
            # Ratio in basis points with a cap; physical costs/weights remain separate metrics.
            target = Decimal(str(e.requirement.normalized_value))
            entry = mapping(e.requirement)
            if entry:
                target *= entry[3]
            if target > 0:
                overspec += min(100000, int(Decimal(e.margin) * 10000 / target))
    risk = max(
        (10000 * line.quantity // max(1, line.inventory_qty) for line in config.bom), default=10000
    )
    return {
        "cost": config.cost.total_paise if config.cost.total_paise is not None else 2**63 - 1,
        "weight": config.total_weight_g,
        "overspec": overspec,
        "lead_time": config.lead_time_days,
        "inventory_risk": risk,
        "coverage": 10000 - coverage,
    }


def score(configurations, weights):
    metrics = [objective_metrics(c) for c in configurations]
    maxima = {key: max(1, *(m[key] for m in metrics)) for key in weights.model_dump()}
    for config, row in zip(configurations, metrics, strict=True):
        config.objective_breakdown = {
            key: row[key] * 10000 // maxima[key] * weight
            for key, weight in weights.model_dump().items()
        }
        config.objective_score = sum(config.objective_breakdown.values())
    return metrics
