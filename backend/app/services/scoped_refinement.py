"""Repair selected cases against server-owned sources, preserving every other case."""

from fastapi import HTTPException
from ..contracts.test_cases import GenerateTestCasesResponse
from ..contracts.requirements import RequirementCoveragePlan, RequirementAnalysis, ReviewResult, Requirement
from ..contracts.grounding import EnrichInput
from .workflow_project_service import get_project
from .test_case_quality import delivered_coverage, placeholder_reason


def prepare_scoped_refinement(payload, source_inputs, actor):
    selected = set(payload.selected_test_case_ids)
    if not selected:
        return payload, source_inputs, None
    if not payload.project_id or len(selected) != len(payload.selected_test_case_ids):
        raise HTTPException(422, "Select unique test cases in a saved project")
    project = get_project(payload.project_id, actor=actor)
    snapshot = project.current_snapshots.get("test_cases")
    state = project.stage_state.get("test_cases")
    if project.current_revision != payload.base_project_revision or not snapshot or not state or state.stale:
        raise HTTPException(409, "The source suite changed. Reload before repairing selected tests.")
    baseline = GenerateTestCasesResponse(**snapshot.payload)
    cases = [case for case in baseline.test_cases if case.id in selected]
    if {case.id for case in cases} != selected:
        raise HTTPException(422, "Selected test cases are not in the current suite")
    requirement_ids = {rid for case in cases for rid in case.linked_requirement_ids}
    scenario_ids = {sid for case in cases for sid in case.scenario_refs}
    requirement_source = project.current_snapshots.get("requirements")
    requirement_state = project.stage_state.get("requirements")
    if not requirement_source or not requirement_state or requirement_state.stale or requirement_state.current_snapshot_id != requirement_source.snapshot_id:
        raise HTTPException(409, "Reload the current requirement source before repair")
    requirements = [Requirement.model_validate(r) for r in requirement_source.payload.get("requirements", []) if r["id"] in requirement_ids]
    if (
        not requirement_ids
        or {r.id for r in requirements} != requirement_ids
        or any(
            r.review_status != "Approved" or r.lifecycle_status != "active" or r.clarification_status in ("open", "deferred", "excluded") for r in requirements
        )
    ):
        raise HTTPException(409, "Approve the selected tests' current requirements before repair")
    plans = [
        {**group, "scenarios": [s for s in group.get("scenarios", []) if s["id"] in scenario_ids]}
        for group in source_inputs.get("source_coverage_plan", [])
        if group["requirement_id"] in requirement_ids
    ]
    sources = {
        "source_coverage_plan": plans,
        "source_requirement_analysis": [a for a in source_inputs.get("source_requirement_analysis", []) if a["requirement_id"] in requirement_ids],
    }
    context_source = project.current_snapshots.get("context")
    context = EnrichInput.model_validate(
        {
            **(context_source.payload if context_source else {}),
            "requirements": requirements,
            "project_id": payload.project_id,
            "base_project_revision": payload.base_project_revision,
        }
    )
    scoped = payload.model_copy(
        update={
            "requirements": requirements,
            "context": context,
            "test_cases": cases,
            "feedback": payload.feedback
            + "\nRepair only these existing test IDs, retaining their source links and IDs: "
            + ", ".join(payload.selected_test_case_ids),
        }
    )
    return scoped, sources, baseline


def merge_scoped_refinement(response, baseline, selected_ids, sources):
    if response.workflow_diagnostics.used_fallback:
        raise HTTPException(422, "The repair returned fallback output. The saved suite was preserved; retry the repair.")
    selected = set(selected_ids)
    repaired = {case.id: case for case in response.test_cases}
    if len(repaired) != len(response.test_cases) or set(repaired) != selected or response.generation_tasks:
        raise HTTPException(422, "Repair must return exactly the selected tests. The saved suite was preserved.")
    original = {case.id: case for case in baseline.test_cases}
    for identity, case in repaired.items():
        prior = original[identity]
        if (
            placeholder_reason(case)
            or set(case.linked_requirement_ids) != set(prior.linked_requirement_ids)
            or set(case.scenario_refs) != set(prior.scenario_refs)
        ):
            raise HTTPException(422, "Repair needs concrete steps and the selected tests' original source links. The saved suite was preserved.")
        case.status = "Draft"
    changed = [identity for identity, case in repaired.items() if case.model_dump() != original[identity].model_dump()]
    response.test_cases = [repaired.get(case.id, case) for case in baseline.test_cases]
    response.coverage_plan = [RequirementCoveragePlan.model_validate(p) for p in sources.get("source_coverage_plan", [])]
    response.requirement_analysis = [RequirementAnalysis.model_validate(a) for a in sources.get("source_requirement_analysis", [])]
    response.coverage_metrics = delivered_coverage(response.test_cases, response.coverage_plan)
    response.generation_tasks = baseline.generation_tasks
    response.approved = False
    response.review = ReviewResult(approved=False, summary="Selected tests repaired. Review the changed tests before accepting this suite.")
    response.substantive_assessment = {"status": "unknown", "reason": "The combined suite needs assessment after a scoped repair."}
    response.scoped_refinement = {"changed": len(changed), "added": 0, "preserved": len(baseline.test_cases) - len(changed), "changed_ids": changed}
    return response
