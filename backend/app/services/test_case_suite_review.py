"""Assess immutable saved tests after targeted changes, without a generation pass."""

from fastapi import HTTPException
from pydantic import ValidationError

from ..agents.substantive_review import apply_assessment, assess_delivered_suite
from ..agents.test_case_review import _heuristic_test_case_review, _resolve_test_case_workflow_settings
from ..config import get_settings
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectDetail
from ..contracts.requirements import RequirementCoveragePlan, WorkflowSettings
from ..contracts.test_cases import TestCase
from ..models import AuthUser
from .audit_service import complete_workflow_run, start_workflow_run
from .orchestrator_service import _use_cases_human_approved
from .requirement_scope import approved_generation_requirements
from .test_case_quality import delivered_coverage, separate_generation_work
from .workflow_project_service import ProjectConflictError, append_stage_snapshot, get_project, get_project_stage_snapshot

REVIEW_OPERATION = "test_cases.review"
REVIEWABLE_OPERATIONS = {"impact.update.apply", "testcases.refine", "quality.feedback.apply"}


def _current_review_inputs(project, suite):
    requirements = approved_generation_requirements(project)
    use_cases = project.current_snapshots.get("use_cases")
    state = project.stage_state.get("use_cases")
    if (
        not requirements
        or not use_cases
        or not state
        or state.stale
        or not _use_cases_human_approved(state)
        or state.current_snapshot_id != use_cases.snapshot_id
    ):
        raise HTTPException(409, "Approve current Requirements and Use Cases before reviewing the saved suite.")

    sources = {stage: project.current_snapshots[stage].snapshot_id for stage in ("requirements", "context", "use_cases") if stage in project.current_snapshots}
    context = project.current_snapshots.get("context")
    context_state = project.stage_state.get("context")
    if context and (not context_state or context_state.stale or context_state.current_snapshot_id != context.snapshot_id):
        raise HTTPException(409, "Refresh Context before reviewing the saved suite.")
    if suite.operation == "quality.feedback.apply":
        expected_requirements = (suite.metadata.get("source_snapshot_ids") or {}).get("requirements")
        if (
            not sources.get("context")
            or sources["context"] != suite.metadata.get("source_context_snapshot_id")
            or project.current_snapshots["requirements"].project_revision > suite.project_revision
            or (expected_requirements and sources["requirements"] != expected_requirements)
        ):
            raise HTTPException(409, "Source inputs changed after prerequisite correction. Analyze their impact before reviewing.")

    required_ids = {row.id for row in requirements}
    try:
        plans = [RequirementCoveragePlan.model_validate(row) for row in use_cases.payload.get("coverage_plan", [])]
        plans = [plan.model_dump(mode="json") for plan in plans if plan.requirement_id in required_ids]
        enriched = EnrichInput.model_validate({**context.payload, "requirements": requirements}) if context else None
    except (TypeError, ValidationError) as exc:
        raise HTTPException(409, "The current source plan or Context needs review before these tests can be assessed.") from exc
    if len(plans) != len(required_ids) or {plan["requirement_id"] for plan in plans if plan["scenarios"]} != required_ids:
        raise HTTPException(409, "The current approved Use Cases plan does not cover the requirement scope.")
    analysis = [row for row in use_cases.payload.get("requirement_analysis", []) if row.get("requirement_id") in required_ids]
    return requirements, plans, analysis, enriched, sources


def _review_payload(payload, requirements, plans, analysis, context):
    try:
        cases = [TestCase.model_validate(case) for case in payload.get("test_cases", [])]
        workflow_settings = WorkflowSettings.model_validate(payload.get("workflow_settings") or {})
    except (TypeError, ValidationError) as exc:
        raise HTTPException(409, "The saved suite contains invalid test data. Repair it before review.") from exc
    # Historical cases stay in the immutable suite and cannot count as current coverage.
    active = [case.model_dump(mode="json") for case in cases if case.status != "Deprecated"]
    if not active or len({case["id"] for case in active}) != len(active):
        raise HTTPException(409, "Review requires concrete saved test cases with distinct IDs.")
    pending = [task for task in payload.get("generation_tasks", []) if task.get("kind") != "substantive_coverage"]
    concrete, tasks = separate_generation_work(active, plans, pending_tasks=pending)
    if not concrete:
        raise HTTPException(409, "Repair unfinished generation before reviewing the saved suite.")
    review = _heuristic_test_case_review(
        concrete, requirements, _resolve_test_case_workflow_settings(workflow_settings)["approval_threshold"], plans, analysis, context
    )
    refs = {plan["requirement_id"]: {scenario["id"] for scenario in plan["scenarios"]} for plan in plans}
    invalid = [
        case["id"]
        for case in concrete
        if not case["linked_requirement_ids"]
        or not set(case["linked_requirement_ids"]) <= refs.keys()
        or not case["scenario_refs"]
        or not set(case["scenario_refs"]) <= set().union(*(refs.get(req_id, set()) for req_id in case["linked_requirement_ids"]))
    ]
    if invalid:
        review["approved"] = False
        review["blocking_issues"].append(f"These tests need exact current requirement and scenario links: {', '.join(invalid[:10])}.")
        review["summary"] = "Review current scenario links before approving the saved suite."

    try:
        settings = get_settings()
    except RuntimeError as exc:
        raise HTTPException(503, "Independent review is unavailable. The saved suite is unchanged.") from exc
    assessment = assess_delivered_suite(concrete, requirements, context, settings=settings)
    if assessment.get("model_status", assessment["status"]) == "unknown":
        raise HTTPException(503, "Independent review is unavailable. The saved suite is unchanged. Try review again.")
    reviewed = apply_assessment(
        {
            **payload,
            "test_cases": concrete,
            "approved": review["approved"],
            "review": review,
            "coverage_plan": plans,
            "requirement_analysis": analysis,
            "generation_tasks": tasks,
            "coverage_metrics": delivered_coverage(concrete, plans),
            "workflow_diagnostics": dict(payload.get("workflow_diagnostics") or {}),
            "generation_evidence": dict(payload.get("generation_evidence") or {}),
        },
        assessment,
    )
    return {**reviewed, "test_cases": payload["test_cases"]}


def review_saved_test_cases(*, project_id: str, snapshot_id: str, base_project_revision: int, actor: AuthUser, request_id: str) -> QaProjectDetail:
    project = get_project(project_id, actor=actor)
    current = project.current_snapshots.get("test_cases")
    if project.status != "active":
        raise HTTPException(409, "Restore the project before reviewing its test cases.")
    if (
        current
        and current.operation == REVIEW_OPERATION
        and current.source_snapshot_id == snapshot_id
        and current.project_revision == project.current_revision == base_project_revision + 1
    ):
        return project  # The exact request already committed, for example after a lost response.
    if project.current_revision != base_project_revision:
        raise ProjectConflictError(project.current_revision)
    if not current or current.snapshot_id != snapshot_id:
        raise HTTPException(409, "The saved test suite changed. Reload the current suite before reviewing.")
    if current.operation not in REVIEWABLE_OPERATIONS or (current.payload.get("substantive_assessment") or {}).get("status") != "unknown":
        raise HTTPException(409, "The current suite has no pending post-change assessment.")
    if project.stage_state["test_cases"].stale and current.operation != "quality.feedback.apply":
        raise HTTPException(409, "Upstream inputs changed. Analyze their impact before reviewing the saved suite.")

    requirements, plans, analysis, context, sources = _current_review_inputs(project, current)
    original = get_project_stage_snapshot(project_id, snapshot_id, actor=actor)
    run_id = start_workflow_run(operation=REVIEW_OPERATION, actor=actor, request_id=request_id, metadata={"source_snapshot_id": snapshot_id})
    try:
        reviewed = _review_payload(original.payload, requirements, plans, analysis, context)
        append_stage_snapshot(
            project_id=project_id,
            stage="test_cases",
            payload=reviewed,
            operation=REVIEW_OPERATION,
            actor=actor,
            request_id=request_id,
            workflow_run_id=run_id,
            approved=reviewed["approved"],
            source_snapshot_id=snapshot_id,
            title="Saved test suite reviewed",
            metadata={
                "source_snapshot_ids": sources,
                "source_requirements_snapshot_id": sources["requirements"],
                "source_context_snapshot_id": sources.get("context"),
                "source_use_case_snapshot_id": sources["use_cases"],
                "approved": reviewed["approved"],
            },
            base_project_revision=base_project_revision,
        )
    except Exception:
        complete_workflow_run(run_id, status="failed", error_message="Saved suite review was not published")
        raise
    complete_workflow_run(run_id, status="completed", metadata={"approved": reviewed["approved"]})
    return get_project(project_id, actor=actor)
