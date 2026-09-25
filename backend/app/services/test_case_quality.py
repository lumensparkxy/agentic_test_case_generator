"""Keep unfinished generation instructions out of deliverable test suites."""

import re


_GENERIC_ACTION = re.compile(
    r"^(navigate to the feature area that implements |execute the .+ scenario for REQ-|"
    r"review the implemented behavior for |execute the primary user path and affected |"
    r"observe system behavior and capture the outcome)",
    re.I,
)
_GENERIC_EXPECTED = re.compile(
    r"^(the behavior reflects the approved changed requirement\.?|"
    r"the changed behavior works without regressing preserved coverage\.?|"
    r"requirement .+ is satisfied for the planned .+ scenario\.?)$",
    re.I,
)


def placeholder_reason(case, *, strict=True):
    row = case.model_dump(mode="json") if hasattr(case, "model_dump") else case
    if row.get("status") == "Deprecated":
        return None
    steps = row.get("steps") or []
    if strict and (not steps or any(not str(s.get("action", "")).strip() or not str(s.get("expected", "")).strip() for s in steps)):
        return "Concrete test actions and observable expected results are missing."
    if any(_GENERIC_ACTION.search(str(s.get("action", "")).strip()) for s in steps):
        return "This row contains generation instructions instead of concrete test actions."
    if steps and all(_GENERIC_EXPECTED.fullmatch(str(s.get("expected", "")).strip()) for s in steps):
        return "The expected results do not specify an observable outcome."
    return None


def separate_generation_work(cases, coverage_plan=(), *, strict=True, pending_tasks=()):
    delivered, tasks = [], [dict(task) for task in pending_tasks]
    for case in cases:
        row = case.model_dump(mode="json") if hasattr(case, "model_dump") else case
        reason = placeholder_reason(row, strict=strict)
        if reason:
            tasks.append(
                {
                    "source_test_case_id": row.get("id"),
                    "requirement_ids": row.get("linked_requirement_ids") or [],
                    "scenario_refs": row.get("scenario_refs") or [],
                    "reason": reason,
                }
            )
        else:
            delivered.append(case)
    covered = {ref for c in delivered for ref in (c.model_dump() if hasattr(c, "model_dump") else c).get("scenario_refs", [])}
    pending = {ref for task in tasks for ref in task["scenario_refs"]}
    for raw in coverage_plan:
        group = raw.model_dump() if hasattr(raw, "model_dump") else raw
        for scenario in group.get("scenarios") or []:
            sid = scenario.get("id")
            if sid and sid not in covered and sid not in pending:
                tasks.append(
                    {
                        "source_test_case_id": None,
                        "requirement_ids": [group["requirement_id"]],
                        "scenario_refs": [sid],
                        "reason": "Generate concrete steps and observable expected results for this uncovered scenario.",
                    }
                )
    return delivered, tasks


def project_test_case_view(payload):
    """A read-only projection; immutable saved snapshots are never rewritten."""
    rows, tasks = separate_generation_work(payload.get("test_cases") or [], strict=False)
    tasks = tasks + [t for t in payload.get("generation_tasks", []) if t not in tasks]
    if not tasks:
        return payload
    return {
        **payload,
        "test_cases": rows,
        "generation_tasks": tasks,
        "approved": False,
        "review": {
            **(payload.get("review") or {}),
            "approved": False,
            "score": 0,
            "summary": "Unfinished generation work must be repaired and reviewed.",
            "blocking_issues": [f"{len(tasks)} generation task(s) remain unresolved."],
        },
        "coverage_metrics": {**delivered_coverage(rows, payload.get("coverage_plan") or []), "unresolved_generation_task_count": len(tasks)},
    }


def delivered_coverage(cases, plans):
    from ..agents.test_case_coverage import _compute_test_case_coverage_metrics, _compute_planned_scenario_metrics
    from ..contracts.requirements import Requirement

    rows = [c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in cases]
    groups = [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in plans]
    requirements = [Requirement(id=p["requirement_id"], text=p.get("requirement_text") or p["requirement_id"]) for p in groups]
    return {**_compute_test_case_coverage_metrics(rows, requirements), **_compute_planned_scenario_metrics(groups, rows, requirements)}
