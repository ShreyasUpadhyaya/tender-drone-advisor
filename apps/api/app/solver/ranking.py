from app.solver.scoring import score


def rank(configurations, policy):
    # Separate proven candidates from review/failed results before scoring.
    valid = [c for c in configurations if c.status in ("feasible", "conditionally_feasible")]
    provisional = [c for c in configurations if c.status == "needs_review"]
    failed = [c for c in configurations if c.status == "infeasible"]
    metrics = score(valid, policy.weights) if valid else []
    for i, c in enumerate(valid):
        c.pareto = not any(
            all(other[k] <= metrics[i][k] for k in metrics[i])
            and any(other[k] < metrics[i][k] for k in metrics[i])
            for j, other in enumerate(metrics)
            if i != j
        )
    for group in (provisional, failed):
        if group:
            score(group, policy.weights)
    key = lambda c: (c.objective_score, c.tie_break)
    ordered = sorted(valid, key=key) + sorted(provisional, key=key) + sorted(failed, key=key)
    options = {}
    if valid:
        for label, fn in (
            ("lowest_cost", lambda c: (c.cost.total_paise, c.tie_break)),
            ("lowest_weight", lambda c: (c.total_weight_g, c.tie_break)),
            ("fastest_available", lambda c: (c.lead_time_days, c.tie_break)),
            ("balanced_recommended", key),
        ):
            best = min(valid, key=fn)
            best.labels.append(label)
            options[label] = best.id
        min(valid, key=key).recommended = True
    # Keep strict top-K; options beyond it are documented in options but have persisted detail APIs.
    for index, c in enumerate(ordered, 1):
        c.rank = index
    return ordered, options
