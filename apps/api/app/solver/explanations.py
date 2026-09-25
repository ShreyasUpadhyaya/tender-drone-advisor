from app.solver.contracts import Issue


def issue(code: str, severity: str, *item_ids: str, requirement_id: str | None = None) -> Issue:
    # Only structured codes/identifiers; never free-form model-generated engineering claims.
    return Issue(
        code=code,
        severity=severity,
        detail=code.replace("_", " "),
        item_ids=list(item_ids),
        requirement_id=requirement_id,
        question=f"Provide verified data to resolve {code.replace('_', ' ')}."
        if severity == "review"
        else None,
    )
