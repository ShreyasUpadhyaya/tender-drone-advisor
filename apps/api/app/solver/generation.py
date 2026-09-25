from pydantic import ValidationError

from app.solver.compatibility import check_compatibility
from app.solver.contracts import Assembly, Rejection, Selection, Snapshot
from app.solver.explanations import issue
from app.solver.numbers import SolverError


def generate(snapshot: Snapshot):
    items = sorted(snapshot.items, key=lambda x: (x.sku, x.item_version, x.id))
    if len(items) > snapshot.policy.max_items:
        raise SolverError("catalog_item_limit_exceeded")
    platforms = [x for x in items if x.category in ("platform", "airframe")]
    lookup = {(x.sku, x.item_version): x for x in items}
    ready, rejected, global_issues, budget = [], [], [], 0
    if not platforms:
        global_issues.append(issue("platform_catalog_missing", "review"))

    def visit(platform, assembly, index, selected):
        nonlocal budget
        budget += 1  # Nodes, not just leaves, bound expensive rejected prefixes too.
        if budget > snapshot.policy.max_combinations:
            raise SolverError("combination_limit_exceeded")
        hard = [x for x in check_compatibility(selected, snapshot.rules) if x.severity == "hard"]
        if hard:
            rejected.append(
                Rejection(
                    platform_id=platform.id, item_ids=[s.item.id for s in selected], issues=hard
                )
            )
            return
        if index == len(assembly.slots):
            ready.append((platform, assembly, selected))
            return
        slot = assembly.slots[index]
        for ref in sorted(slot.options, key=lambda x: (x.sku, x.item_version)):
            item = lookup.get((ref.sku, ref.item_version))
            if item is None or item.category != slot.category:
                global_issues.append(issue("assembly_reference_missing", "review", platform.id))
                continue
            visit(
                platform,
                assembly,
                index + 1,
                selected + [Selection(item=item, quantity=slot.quantity)],
            )

    for platform in platforms:
        try:
            assembly = Assembly.model_validate(platform.specs.get("assembly"))
        except ValidationError:
            global_issues.append(
                issue("assembly_metadata_missing_or_invalid", "review", platform.id)
            )
            continue
        visit(platform, assembly, 0, [Selection(item=platform, quantity=1)])
    return ready, rejected, global_issues, budget
