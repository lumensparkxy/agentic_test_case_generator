"""Reconstruct background Test Cases inputs from an exact, approved source plan."""

from dataclasses import dataclass

from fastapi import HTTPException
from pydantic import ValidationError

from ..contracts.grounding import EnrichInput
from ..contracts.projects import ProjectStageName, QaProjectDetail, QaProjectStageSnapshot
from ..models import AuthUser, GenerateTestCasesInput
from .orchestrator_service import _matching_use_case_human_review, _use_cases_human_approved
from .requirement_scope import approved_generation_requirements
from .use_case_generation_service import _validate_output
from .use_case_review_service import recorded_use_case_context_matches
from .workflow_project_service import ProjectConflictError, ProjectPermissionError, get_project


@dataclass(frozen=True)
class ApprovedTestCaseSource:
    requirements_snapshot_id: str
    context_snapshot_id: str | None
    use_case_snapshot_id: str
    scenario_review_id: str
    requirement_ids: tuple[str, ...]


def _current_snapshot(project: QaProjectDetail, stage: ProjectStageName, expected_id: str | None) -> QaProjectStageSnapshot | None:
    state = project.stage_state.get(stage)
    snapshot = project.current_snapshots.get(stage)
    if expected_id is None:
        if snapshot is None and (state is None or (state.current_snapshot_id is None and not state.stale)):
            return None
    elif (
        state is not None
        and not state.stale
        and state.current_snapshot_id == expected_id
        and snapshot is not None
        and snapshot.snapshot_id == expected_id
        and snapshot.project_id == project.project_id
        and snapshot.stage == stage
    ):
        return snapshot
    raise HTTPException(409, f"Saved {stage.replace('_', ' ').title()} does not match this approval. Reload the current project.")


def _generation_context(context_snapshot, requirements, project):
    if context_snapshot is None:
        return None
    saved = context_snapshot.payload
    # Match the workbench Context shape used by saved-suite review to reconstruct the original input.
    context = EnrichInput.model_validate(
        {
            **saved,
            "requirements": requirements,
            "diagram_links": saved.get("diagram_links") or None,
            "image_links": saved.get("image_links") or None,
            "project_id": project.project_id,
            "base_project_revision": project.current_revision,
        }
    )
    grounded = context.grounded_context
    if grounded is not None:
        source_ids = {source.id for source in grounded.artifact_sources}
        context.grounded_context = grounded.model_copy(
            update={"ui_elements": [element for element in grounded.ui_elements if not element.source_id or element.source_id in source_ids]}
        )
    return context


def prepare_approved_test_case_payload(
    payload: GenerateTestCasesInput,
    actor: AuthUser,
    source: ApprovedTestCaseSource,
    *,
    project: QaProjectDetail | None = None,
) -> GenerateTestCasesInput:
    """Admit a worker's pinned project, revision, template and settings using saved facts."""
    if not payload.project_id or payload.base_project_revision is None:
        raise HTTPException(409, "Background Test Cases generation requires a project and the approved revision.")
    if project is None:
        project = get_project(payload.project_id, actor=actor)
    if project.owner_user_id != actor.sub or project.project_id != payload.project_id:
        raise ProjectPermissionError(payload.project_id)
    if project.current_revision != payload.base_project_revision:
        raise ProjectConflictError(project.current_revision)
    if project.status != "active":
        raise HTTPException(409, "Restore the project before generating Test Cases.")

    baseline = _current_snapshot(project, "requirements", source.requirements_snapshot_id)
    context_snapshot = _current_snapshot(project, "context", source.context_snapshot_id)
    plan = _current_snapshot(project, "use_cases", source.use_case_snapshot_id)
    requirements = approved_generation_requirements(project)
    if (
        not baseline
        or baseline.operation != "requirements.review"
        or baseline.actor_user_id != actor.sub
        or not requirements
        or tuple(row.id for row in requirements) != source.requirement_ids
    ):
        raise HTTPException(409, "The exact approved requirement scope is unavailable. Review the current requirements.")

    state = project.stage_state.get("use_cases")
    review = _matching_use_case_human_review(state)
    if (
        not plan
        or not _use_cases_human_approved(state)
        or review.get("review_id") != source.scenario_review_id
        or review.get("reviewer_user_id") != actor.sub
        or review.get("resulting_project_revision") != project.current_revision
    ):
        raise HTTPException(409, "Approve the exact current scenario plan before generating Test Cases.")
    if (
        plan.operation != "use_cases.generate"
        or plan.source_snapshot_id != baseline.snapshot_id
        or plan.metadata.get("source_requirements_snapshot_id") != baseline.snapshot_id
        or not recorded_use_case_context_matches(plan.metadata, source.context_snapshot_id)
    ):
        raise HTTPException(409, "The approved scenario plan does not record the current Requirements and Context as its sources.")

    try:
        output = _validate_output(plan.payload, requirements)
    except HTTPException as exc:
        raise HTTPException(409, "The approved scenario plan could not be verified against the current requirements. Review the saved plan.") from exc
    try:
        context = _generation_context(context_snapshot, requirements, project)
    except ValidationError as exc:
        raise HTTPException(409, "Saved Context could not be verified. Review it before generating Test Cases.") from exc
    return GenerateTestCasesInput(
        project_id=project.project_id,
        base_project_revision=project.current_revision,
        requirements=requirements,
        context=context,
        coverage_plan=output.coverage_plan,
        requirement_analysis=output.requirement_analysis,
        template=payload.template,
        workflow_settings=payload.workflow_settings,
    )
