"""One preflight policy for project preview explanations and runtime admission."""

from pydantic import HttpUrl, TypeAdapter, ValidationError

from ..config import ExecutionSettings
from ..contracts.execution import ExecutionPreviewInput, ExecutionPreviewResponse, ExecutionReadiness, ExecutionReadinessBlocker
from ..contracts.projects import QaProjectDetail
from ..contracts.test_cases import TestCase


def assess_execution(
    payload: ExecutionPreviewInput,
    *,
    project: QaProjectDetail | None,
    settings: ExecutionSettings,
    preview: ExecutionPreviewResponse | None = None,
) -> ExecutionReadiness:
    blockers: list[ExecutionReadinessBlocker] = []

    def block(code: str, message: str) -> None:
        blockers.append(ExecutionReadinessBlocker(code=code, message=message))

    target = str(payload.target_base_url) if payload.target_base_url else None
    target_source = "request" if target else "unspecified"
    if not target and settings.default_base_url_configured:
        target = settings.default_base_url
        target_source = "configured_default"
    if target:
        try:
            target = str(TypeAdapter(HttpUrl).validate_python(target))
        except ValidationError:
            target = None
            block("invalid_target", "Configure a valid HTTP or HTTPS execution target.")
    else:
        block("target_required", "Supply an application target before running. The implicit local default is not an execution target.")
    if not settings.enabled:
        block("execution_disabled", "Execution is disabled by backend configuration.")
    if not payload.test_cases:
        block("missing_cases", "Generate and approve concrete test cases before running.")

    snapshot = None
    if payload.project_id:
        if project is None:
            block("project_unavailable", "Current project evidence is unavailable.")
        else:
            if project.status != "active":
                block("project_archived", "Restore the archived project before running.")
            if payload.base_project_revision is None or payload.base_project_revision != project.current_revision:
                block("revision_conflict", "Reload the current project before previewing or running.")
            for stage in ("requirements", "use_cases", "test_cases"):
                state = project.stage_state.get(stage)
                if stage == "use_cases" and (not state or not state.current_snapshot_id):
                    continue
                if not state or not state.current_snapshot_id:
                    block(f"missing_{stage}", f"Current {stage.replace('_', ' ')} evidence is required before running.")
                    continue
                if state.stale:
                    block(f"stale_{stage}", f"Refresh and review the current {stage.replace('_', ' ')} before running.")
                approved = state.approved
                if stage == "use_cases":
                    review = state.metadata.get("latest_human_review")
                    review = review if isinstance(review, dict) else {}
                    approved = approved and review.get("snapshot_id") == state.current_snapshot_id and review.get("decision") == "approve"
                if not approved:
                    block(f"unapproved_{stage}", f"Approve the current {stage.replace('_', ' ')} before running.")
            snapshot = project.current_snapshots.get("test_cases")
            state = project.stage_state.get("test_cases")
            if not snapshot or not state or snapshot.snapshot_id != state.current_snapshot_id:
                block("snapshot_unavailable", "The current test-case snapshot cannot be verified.")
            else:
                saved = snapshot.payload
                if saved.get("generation_tasks"):
                    block("incomplete_suite", "Resolve unfinished generation tasks and review the completed suite before running.")
                try:
                    saved_cases = [TestCase.model_validate(case).model_dump(mode="json") for case in saved.get("test_cases", [])]
                except ValidationError, TypeError, ValueError:
                    saved_cases = None
                if not saved_cases or saved_cases != [case.model_dump(mode="json") for case in payload.test_cases]:
                    block("case_snapshot_mismatch", "Run only the exact concrete cases in the current saved test-case snapshot.")
                for stage, key in (
                    ("requirements", "source_requirements_snapshot_id"),
                    ("context", "source_context_snapshot_id"),
                    ("use_cases", "source_use_case_snapshot_id"),
                ):
                    source_id = snapshot.metadata.get(key)
                    current = project.stage_state.get(stage)
                    if source_id and (not current or source_id != current.current_snapshot_id or current.stale):
                        block("stale_source", f"The suite was generated from older {stage.replace('_', ' ')}. Refresh it before running.")
    if preview is not None and not preview.executable:
        block("no_executable_candidates", "No eligible browser candidates are available. Review manual, unsupported or invalid cases.")
    return ExecutionReadiness(
        run_allowed=not blockers,
        blockers=blockers,
        project_id=payload.project_id,
        project_revision=project.current_revision if project else None,
        source_snapshot_id=snapshot.snapshot_id if snapshot else None,
        target_base_url=target,
        target_source=target_source,
    )


def selected_candidates(preview: ExecutionPreviewResponse, selected_ids: list[str]) -> list[str]:
    """Resolve legacy source IDs only when unambiguous; never silently run all."""
    resolved: list[str] = []
    if not selected_ids:
        raise ValueError("Select at least one executable candidate before running.")
    for selected_id in selected_ids:
        exact = [candidate for candidate in preview.executable if candidate.id == selected_id]
        matches = exact or [candidate for candidate in preview.executable if candidate.source_test_case_id == selected_id]
        if len(matches) != 1:
            raise ValueError("The selection includes an unknown, unsupported or ambiguous candidate. Preview again.")
        if matches[0].id not in resolved:
            resolved.append(matches[0].id)
    return resolved
