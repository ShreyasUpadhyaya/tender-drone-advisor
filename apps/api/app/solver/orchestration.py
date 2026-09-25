import hashlib
import json

from app.solver.bom import construct_bom
from app.solver.classification import evaluate, requirement_issues
from app.solver.compatibility import check_compatibility
from app.solver.constraints import availability
from app.solver.contracts import Capability, Configuration, Rejection, Snapshot, SolveResult
from app.solver.costing import cost_bom
from app.solver.envelope import engineering
from app.solver.explanations import issue
from app.solver.generation import generate
from app.solver.numbers import SolverError
from app.solver.ranking import rank


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def solve(snapshot: Snapshot) -> SolveResult:
    if len(snapshot.requirements) > snapshot.policy.max_requirements:
        raise SolverError("requirement_limit_exceeded")
    global_issues = list(snapshot.source_issues)
    if (
        snapshot.extraction_state != "completed"
        or snapshot.extraction_review_state != "not_required"
    ):
        global_issues.append(issue("extraction_unresolved", "review"))
    present = {
        r.requirement.category
        for r in snapshot.requirements
        if r.requirement.normalized_value != "unknown"
    }
    for missing in sorted({"platform", "payload", "range", "endurance"} - present):
        global_issues.append(issue("missing_critical_" + missing, "review"))
    if (
        snapshot.catalog_effective_from and snapshot.analysis_date < snapshot.catalog_effective_from
    ) or (snapshot.catalog_effective_to and snapshot.analysis_date > snapshot.catalog_effective_to):
        global_issues.append(issue("catalog_date_outside_effective_period", "review"))
    generated, rejected, gen_issues, examined = generate(snapshot)
    if len(generated) * len(snapshot.requirements) > snapshot.policy.max_evaluations:
        raise SolverError("evaluation_limit_exceeded")
    global_issues.extend(gen_issues)
    input_fingerprint = fingerprint(snapshot.model_dump(mode="json"))
    configs = []
    for platform, assembly, selected in generated:
        compatibility = check_compatibility(selected, snapshot.rules, complete=True)
        weight, payload, caps, engineering_issues = engineering(snapshot, platform, selected)
        lines, price_issues = construct_bom(snapshot, selected)
        costs = cost_bom(lines, snapshot.policy.cost, snapshot.policy.fleet_quantity)
        availability_issues, lead, availability_caps = availability(snapshot, selected)
        caps.update(availability_caps)
        if costs.total_paise is not None:
            caps["total_cost_paise"] = Capability(
                value=costs.total_paise, unit="paise", item_ids=[s.item.id for s in selected]
            )
        evaluations = [evaluate(r, caps) for r in snapshot.requirements]
        issues = [
            *global_issues,
            *compatibility,
            *engineering_issues,
            *requirement_issues(evaluations),
            *availability_issues,
            *price_issues,
        ]
        for assumption in snapshot.policy.nonblocking_assumptions:
            issues.append(issue("operator_nonblocking_assumption", "assumption"))
            issues[-1].detail = assumption
        hard = any(i.severity == "hard" for i in issues)
        review = any(i.severity == "review" for i in issues)
        status = (
            "infeasible"
            if hard
            else "needs_review"
            if review
            else "conditionally_feasible"
            if snapshot.policy.nonblocking_assumptions
            else "feasible"
        )
        by = {s.item.category: s.item for s in selected}
        modified = any(
            s.baseline is None
            or (by[s.category].sku, by[s.category].item_version)
            != (s.baseline.sku, s.baseline.item_version)
            for s in assembly.slots
        )
        outcome = (
            "not_feasible"
            if hard
            else "needs_review"
            if review
            else "feasible_with_changes"
            if modified
            else "feasible"
        )
        tie = "|".join(
            f"{s.item.sku}:{s.item.item_version}:{s.quantity}:{s.item.id}"
            for s in sorted(selected, key=lambda x: (x.item.sku, x.item.item_version))
        )
        configs.append(
            Configuration(
                id=fingerprint({"snapshot": input_fingerprint, "selection": tie})[:32],
                platform_id=platform.id,
                selections=selected,
                status=status,
                outcome=outcome,
                modified=modified,
                tie_break=tie,
                bom=lines,
                cost=costs,
                total_weight_g=weight,
                payload_g=payload,
                lead_time_days=lead,
                immediately_buildable=not hard and not review,
                compatibility="failed"
                if any(x.severity == "hard" for x in compatibility)
                else "unverified"
                if compatibility
                else "passed",
                capabilities=caps,
                requirement_coverage_bps=0,
                evaluations=evaluations,
                issues=issues,
            )
        )
    ordered, options = rank(configs, snapshot.policy)
    valid = [c for c in ordered if c.status in ("feasible", "conditionally_feasible")]
    provisional = [c for c in ordered if c.status == "needs_review"]
    if valid:
        status, outcome = valid[0].status, valid[0].outcome
    elif provisional or any(i.severity == "review" for i in global_issues):
        status, outcome = "needs_review", "needs_review"
    elif ordered or rejected:
        status, outcome = "infeasible", "not_feasible"
    else:
        status, outcome = "needs_review", "needs_review"
    for c in ordered:
        if c.status == "infeasible":
            rejected.append(
                Rejection(
                    platform_id=c.platform_id,
                    item_ids=[s.item.id for s in c.selections],
                    issues=[i for i in c.issues if i.severity == "hard"],
                )
            )
    return SolveResult(
        status=status,
        outcome=outcome,
        configurations=ordered,
        rejected=rejected,
        issues=global_issues,
        examined_combinations=examined,
        valid_count=len(valid),
        provisional_count=len(provisional),
        truncated_top_k=len(ordered) > snapshot.policy.top_k,
        options=options,
    )
