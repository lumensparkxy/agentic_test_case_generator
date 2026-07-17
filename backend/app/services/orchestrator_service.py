from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..models import (
    AuthUser,
    OrchestratorActionId,
    OrchestratorActionRecommendation,
    OrchestratorBlocker,
    OrchestratorStageName,
    OrchestratorStageState,
    OrchestratorStageStatus,
    OrchestratorStatusResponse,
    ProjectStageName,
    QaProjectDetail,
    QaProjectStageState,
)
from ..agents.specialist_registry import agent_contract_metadata_for_action
from .workflow_project_service import get_project

ORCHESTRATOR_STAGES: tuple[OrchestratorStageName, ...] = (
    "requirements",
    "context",
    "use_cases",
    "impact_analysis",
    "test_cases",
    "automation",
    "execution",
    "review",
    "reports",
)

PROJECT_STAGE_NAMES: set[str] = {
    "requirements",
    "context",
    "use_cases",
    "impact_analysis",
    "test_cases",
    "execution",
    "reports",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _project_state(project: QaProjectDetail, stage: ProjectStageName) -> Optional[QaProjectStageState]:
    return project.stage_state.get(stage)


def _blocker(
    code: str,
    message: str,
    *,
    stage: Optional[OrchestratorStageName] = None,
    action: Optional[OrchestratorActionId] = None,
    source_stage: Optional[OrchestratorStageName] = None,
    severity: str = "blocking",
) -> OrchestratorBlocker:
    return OrchestratorBlocker(
        code=code,
        message=message,
        stage=stage,
        action=action,
        source_stage=source_stage,
        severity=severity,
    )


def _approval_blocker(
    stage: OrchestratorStageName,
    *,
    action: Optional[OrchestratorActionId] = None,
    message: Optional[str] = None,
) -> OrchestratorBlocker:
    return _blocker(
        "missing_approval",
        message or f"{stage.replace('_', ' ').title()} must be approved before this action can run.",
        stage=stage,
        action=action,
        source_stage=stage,
    )


def _action(
    action: OrchestratorActionId,
    label: str,
    stage: OrchestratorStageName,
    *,
    reason: str,
    primary: bool = False,
    secondary: bool = False,
    blockers: Optional[list[OrchestratorBlocker]] = None,
) -> OrchestratorActionRecommendation:
    action_blockers = list(blockers or [])
    agent_metadata = agent_contract_metadata_for_action(action)
    return OrchestratorActionRecommendation(
        action=action,
        label=label,
        stage=stage,
        enabled=not action_blockers,
        primary=primary,
        secondary=secondary,
        reason=reason,
        blockers=action_blockers,
        **agent_metadata,
    )


def _snapshot_payload(project: QaProjectDetail, stage: ProjectStageName) -> dict[str, Any]:
    snapshot = project.current_snapshots.get(stage)
    return dict(snapshot.payload or {}) if snapshot else {}


def _review_blockers(project: QaProjectDetail) -> list[OrchestratorBlocker]:
    blockers: list[OrchestratorBlocker] = []
    for stage in ("requirements", "use_cases", "test_cases"):
        state = _project_state(project, stage)
        payload = _snapshot_payload(project, stage)
        review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
        blocking_issues = [str(item) for item in review.get("blocking_issues") or []]
        unmet_criteria = [str(item) for item in review.get("unmet_criteria") or []]
        review_approved = review.get("approved")
        latest_human_review = state.metadata.get("latest_human_review") if state else None
        if not isinstance(latest_human_review, dict):
            latest_human_review = {}
        latest_human_decision = str(latest_human_review.get("decision") or "")
        latest_human_comment = str(latest_human_review.get("comment") or "")
        human_review_matches = bool(stage == "use_cases" and state and latest_human_review.get("snapshot_id") == state.current_snapshot_id)
        human_approved = bool(human_review_matches and state and state.approved and latest_human_decision == "approve")
        human_changes_requested = human_review_matches and latest_human_decision == "request_changes"
        machine_review_unresolved = review_approved is False or blocking_issues or unmet_criteria
        if (human_changes_requested or (machine_review_unresolved and not human_approved)) and state and not state.stale:
            detail = (
                latest_human_comment
                if human_changes_requested and latest_human_comment
                else "; ".join([*blocking_issues, *unmet_criteria]) or f"{stage.replace('_', ' ').title()} review is unresolved."
            )
            blockers.append(
                _blocker(
                    "unresolved_review",
                    detail,
                    stage="review",
                    source_stage=stage,
                )
            )
    return blockers


def _impact_apply_blockers(project: QaProjectDetail) -> list[OrchestratorBlocker]:
    blockers: list[OrchestratorBlocker] = []
    impact_payload = _snapshot_payload(project, "impact_analysis")
    changed_items = impact_payload.get("changed_items") if isinstance(impact_payload, dict) else []
    changed_stages: set[OrchestratorStageName] = set()
    for item in changed_items or []:
        if not isinstance(item, dict) or item.get("change_type") not in {"added", "modified"}:
            continue
        if item.get("kind") == "requirement" or item.get("requirement_id"):
            changed_stages.add("requirements")
        if item.get("kind") == "use_case" or item.get("use_case_id") or item.get("scenario_ids"):
            changed_stages.add("use_cases")

    for stage in ("requirements", "use_cases"):
        if stage not in changed_stages:
            continue
        state = _project_state(project, stage)
        if not state or not state.approved or state.stale:
            blockers.append(
                _approval_blocker(
                    stage,
                    action="apply_update",
                    message=f"Approve changed {stage.replace('_', ' ')} before applying impact updates.",
                )
            )

    unapproved = [
        str(item.get("item_id") or item.get("requirement_id") or item.get("use_case_id"))
        for item in changed_items or []
        if isinstance(item, dict) and item.get("change_type") in {"added", "modified"} and not item.get("approved")
    ]
    if unapproved:
        blockers.append(
            _blocker(
                "missing_approval",
                f"Approve changed requirements/use cases before applying impact updates: {', '.join(unapproved)}.",
                stage="impact_analysis",
                action="apply_update",
                source_stage="requirements",
            )
        )
    return blockers


def _latest_execution_status(project: QaProjectDetail) -> Optional[str]:
    if project.execution_runs:
        return str(project.execution_runs[0].status or "").lower()
    execution_state = _project_state(project, "execution")
    if execution_state and execution_state.metadata.get("status"):
        return str(execution_state.metadata.get("status") or "").lower()
    payload = _snapshot_payload(project, "execution")
    if payload.get("status"):
        return str(payload.get("status") or "").lower()
    return None


def _execution_operation(project: QaProjectDetail) -> Optional[str]:
    execution_state = _project_state(project, "execution")
    if execution_state and execution_state.operation:
        return execution_state.operation
    snapshot = project.current_snapshots.get("execution")
    return snapshot.operation if snapshot else None


def _changed_upstream_stages(project: QaProjectDetail) -> list[OrchestratorStageName]:
    test_case_state = _project_state(project, "test_cases")
    if not test_case_state or not test_case_state.current_snapshot_id or not test_case_state.stale:
        return []
    stale_reason = str(test_case_state.stale_reason or "") if test_case_state and test_case_state.stale else ""
    changed: list[OrchestratorStageName] = []
    for stage in ("requirements", "context", "use_cases"):
        if f"{stage} changed" in stale_reason:
            changed.append(stage)
    if changed:
        return changed
    if "impact_analysis changed" not in stale_reason:
        return changed
    impact_state = _project_state(project, "impact_analysis")
    if not impact_state or not impact_state.current_snapshot_id or impact_state.stale:
        return changed
    impact_payload = _snapshot_payload(project, "impact_analysis")
    changed_items = impact_payload.get("changed_items") if isinstance(impact_payload, dict) else []
    requirement_changed = any(isinstance(item, dict) and item.get("requirement_id") for item in changed_items or [])
    use_case_changed = any(isinstance(item, dict) and (item.get("use_case_id") or item.get("scenario_ids")) for item in changed_items or [])
    if requirement_changed:
        changed.append("requirements")
    if use_case_changed:
        changed.append("use_cases")
    return changed


def _real_stage_status(stage: ProjectStageName, state: Optional[QaProjectStageState]) -> OrchestratorStageStatus:
    if state is None or not state.current_snapshot_id:
        return "not_started"
    if state.stale:
        return "stale"
    if stage == "impact_analysis":
        return "completed"
    if stage == "execution":
        status_value = str(state.metadata.get("status") or "").lower()
        if status_value == "failed":
            return "failed"
        if state.operation == "automation.execution.preview":
            return "ready"
    if stage in {"requirements", "use_cases", "test_cases", "reports"} and not state.approved:
        return "attention_required"
    return "completed"


def _real_stage(project: QaProjectDetail, stage: ProjectStageName) -> OrchestratorStageState:
    state = _project_state(project, stage)
    status = _real_stage_status(stage, state)
    blockers: list[OrchestratorBlocker] = []
    if state and state.stale:
        blockers.append(
            _blocker(
                "stale_downstream_stage",
                state.stale_reason or f"{stage.replace('_', ' ').title()} is stale.",
                stage=stage,
                source_stage=stage,
            )
        )
    elif status == "attention_required" and stage in {"requirements", "use_cases", "test_cases", "reports"}:
        blockers.append(_approval_blocker(stage))
    summary = dict(state.metadata or {}) if state else {}
    if state and stage == "impact_analysis":
        payload_summary = _snapshot_payload(project, "impact_analysis").get("summary")
        if isinstance(payload_summary, dict):
            summary = {**payload_summary, **summary}
    return OrchestratorStageState(
        stage=stage,
        status=status,
        current_snapshot_id=state.current_snapshot_id if state else None,
        version=state.version if state else 0,
        approved=state.approved if state else False,
        stale=state.stale if state else False,
        stale_reason=state.stale_reason if state else None,
        operation=state.operation if state else None,
        updated_at=state.updated_at if state else None,
        summary=summary,
        blockers=blockers,
    )


def _automation_stage(project: QaProjectDetail) -> OrchestratorStageState:
    test_case_state = _project_state(project, "test_cases")
    operation = _execution_operation(project)
    blockers: list[OrchestratorBlocker] = []
    test_case_metadata = dict(test_case_state.metadata or {}) if test_case_state else {}
    if operation in {"automation.execution.preview", "automation.execution.run"}:
        status: OrchestratorStageStatus = "completed"
        approved = True
    elif test_case_state and test_case_state.current_snapshot_id and test_case_state.approved and not test_case_state.stale:
        status = "ready"
        approved = False
    elif test_case_state and test_case_state.current_snapshot_id:
        status = "blocked"
        approved = False
        if test_case_state.stale:
            blockers.append(
                _blocker(
                    "stale_downstream_stage",
                    test_case_state.stale_reason or "Test cases are stale.",
                    stage="automation",
                    source_stage="test_cases",
                )
            )
        else:
            blockers.append(_approval_blocker("test_cases", action="automate"))
    else:
        status = "not_started"
        approved = False
    return OrchestratorStageState(
        stage="automation",
        status=status,
        approved=approved,
        blockers=blockers,
        summary={
            "source": "execution_preview"
            if operation == "automation.execution.preview"
            else "execution_run"
            if operation == "automation.execution.run"
            else "test_cases"
            if test_case_state and test_case_state.current_snapshot_id
            else None,
            "source_snapshot_id": test_case_state.current_snapshot_id if test_case_state else None,
            "test_case_count": test_case_metadata.get("test_case_count"),
        },
    )


def _review_stage(project: QaProjectDetail) -> OrchestratorStageState:
    blockers = _review_blockers(project)
    execution_status = _latest_execution_status(project)
    if execution_status == "failed":
        blockers.append(
            _blocker(
                "failed_execution",
                "Latest execution failed and requires review before reporting.",
                stage="review",
                source_stage="execution",
            )
        )
    test_case_state = _project_state(project, "test_cases")
    if blockers:
        status: OrchestratorStageStatus = "attention_required"
    elif test_case_state and test_case_state.current_snapshot_id and test_case_state.approved and not test_case_state.stale:
        status = "completed"
    elif test_case_state and test_case_state.current_snapshot_id:
        status = "ready"
    else:
        status = "not_started"
    return OrchestratorStageState(
        stage="review",
        status=status,
        approved=not blockers and status == "completed",
        blockers=blockers,
        summary={"latest_execution_status": execution_status} if execution_status else {},
    )


def _build_stages(project: QaProjectDetail) -> dict[OrchestratorStageName, OrchestratorStageState]:
    stages: dict[OrchestratorStageName, OrchestratorStageState] = {}
    for stage in ORCHESTRATOR_STAGES:
        if stage == "automation":
            stages[stage] = _automation_stage(project)
        elif stage == "review":
            stages[stage] = _review_stage(project)
        elif stage in PROJECT_STAGE_NAMES:
            stages[stage] = _real_stage(project, stage)
    return stages


def _first_non_complete_stage(stages: dict[OrchestratorStageName, OrchestratorStageState]) -> OrchestratorStageName:
    for stage in ORCHESTRATOR_STAGES:
        state = stages.get(stage)
        if state and state.status not in {"completed"}:
            return stage
    return "reports"


def _current_stage(
    stages: dict[OrchestratorStageName, OrchestratorStageState],
    actions: list[OrchestratorActionRecommendation],
) -> OrchestratorStageName:
    primary = next((action for action in actions if action.primary), None)
    if primary is not None:
        return primary.stage
    return _first_non_complete_stage(stages)


def _full_regenerate_action(blockers: list[OrchestratorBlocker]) -> OrchestratorActionRecommendation:
    return _action(
        "full_regenerate",
        "Full Regenerate",
        "test_cases",
        reason="Explicit escape hatch to rebuild the suite instead of preserving unchanged coverage.",
        secondary=True,
        blockers=blockers,
    )


def _approval_blockers_for_generation(
    project: QaProjectDetail,
    *,
    require_approved_use_cases: bool = True,
) -> list[OrchestratorBlocker]:
    blockers: list[OrchestratorBlocker] = []
    requirements_state = _project_state(project, "requirements")
    use_cases_state = _project_state(project, "use_cases")
    if not requirements_state or not requirements_state.current_snapshot_id:
        blockers.append(
            _blocker(
                "missing_requirements",
                "Requirements must exist before generating test cases.",
                stage="requirements",
                action="generate",
            )
        )
    elif not requirements_state.approved or requirements_state.stale:
        blockers.append(_approval_blocker("requirements", action="generate"))
    if require_approved_use_cases and use_cases_state and use_cases_state.current_snapshot_id and (not use_cases_state.approved or use_cases_state.stale):
        blockers.append(_approval_blocker("use_cases", action="generate"))
    return blockers


def _build_actions(project: QaProjectDetail, *, has_baseline_test_suite: bool, upstream_changed: bool) -> list[OrchestratorActionRecommendation]:
    actions: list[OrchestratorActionRecommendation] = []
    requirements_state = _project_state(project, "requirements")
    use_cases_state = _project_state(project, "use_cases")
    impact_state = _project_state(project, "impact_analysis")
    test_case_state = _project_state(project, "test_cases")
    execution_state = _project_state(project, "execution")
    reports_state = _project_state(project, "reports")
    generation_blockers = _approval_blockers_for_generation(project)
    full_regenerate_blockers = _approval_blockers_for_generation(project, require_approved_use_cases=False)

    if not requirements_state or not requirements_state.current_snapshot_id:
        return [
            _action(
                "refine",
                "Refine Requirements",
                "requirements",
                reason="No requirement snapshot exists for this project.",
                primary=True,
            )
        ]

    if has_baseline_test_suite and upstream_changed:
        if impact_state and impact_state.current_snapshot_id and not impact_state.stale:
            apply_blockers = _impact_apply_blockers(project)
            actions.append(
                _action(
                    "apply_update",
                    "Apply Accepted Updates",
                    "test_cases",
                    reason="Impact analysis is current; apply accepted update/add/deprecate recommendations to produce the next test-case snapshot.",
                    primary=True,
                    blockers=apply_blockers,
                )
            )
        else:
            actions.append(
                _action(
                    "analyze_impact",
                    "Analyze Impact",
                    "impact_analysis",
                    reason="Upstream requirements/use cases changed after the baseline suite was generated.",
                    primary=True,
                )
            )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    if not requirements_state.approved or requirements_state.stale:
        actions.append(
            _action(
                "approve",
                "Approve Requirements",
                "requirements",
                reason="Requirements must be approved before downstream updates are applied or exported.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "refine",
                "Refine Requirements",
                "requirements",
                reason="Requirement refinement can continue until approval is reached.",
                secondary=True,
            )
        )
        return actions

    if not use_cases_state or not use_cases_state.current_snapshot_id:
        actions.append(
            _action(
                "generate",
                "Generate First Test Suite",
                "test_cases",
                reason="No baseline test suite exists, so the next path is first-time generation.",
                primary=True,
                blockers=generation_blockers,
            )
        )
        return actions

    if not use_cases_state.approved or use_cases_state.stale:
        latest_human_review = use_cases_state.metadata.get("latest_human_review")
        latest_review_comment = (
            str(latest_human_review.get("comment") or "").strip()
            if isinstance(latest_human_review, dict) and latest_human_review.get("decision") == "request_changes"
            else ""
        )
        actions.append(
            _action(
                "approve",
                "Approve Use Cases",
                "use_cases",
                reason=(
                    f"Changes were requested: {latest_review_comment}"
                    if latest_review_comment
                    else "Use cases must be approved before a test-suite update or export."
                ),
                primary=True,
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    if not has_baseline_test_suite:
        actions.append(
            _action(
                "generate",
                "Generate First Test Suite",
                "test_cases",
                reason="No baseline test suite exists, so the current approved use cases should generate v1 coverage.",
                primary=True,
                blockers=generation_blockers,
            )
        )
        return actions

    if test_case_state and test_case_state.current_snapshot_id and (not test_case_state.approved or test_case_state.stale):
        test_case_blocker = (
            _blocker(
                "stale_downstream_stage",
                test_case_state.stale_reason or "Test cases are stale.",
                stage="test_cases",
                source_stage="test_cases",
            )
            if test_case_state.stale
            else _approval_blocker("test_cases", action="report")
        )
        actions.append(
            _action(
                "approve",
                "Review and Approve Test Cases",
                "test_cases",
                reason="Test cases need approval before automation, execution, or export/reporting.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "report",
                "Export Report",
                "reports",
                reason="Exports and reports require an approved test-case suite.",
                secondary=True,
                blockers=[test_case_blocker],
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    execution_status = _latest_execution_status(project)
    execution_operation = _execution_operation(project)
    report_stale = bool(reports_state and reports_state.current_snapshot_id and reports_state.stale)
    if execution_status == "failed":
        actions.append(
            _action(
                "review",
                "Review Failed Execution",
                "review",
                reason="The latest execution failed and should be reviewed before reporting.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "execute",
                "Rerun Execution",
                "execution",
                reason="Execution can be rerun after triage or environment fixes.",
                secondary=True,
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    if report_stale:
        actions.append(
            _action(
                "report",
                "Regenerate Evidence Report",
                "reports",
                reason="The latest report is stale because upstream project evidence changed.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "review",
                "Review Evidence",
                "review",
                reason="Review current requirements, coverage, automation readiness, and execution evidence before regenerating the report.",
                secondary=True,
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    if execution_operation == "automation.execution.preview":
        actions.append(
            _action(
                "execute",
                "Execute Approved Suite",
                "execution",
                reason="Automation preview is ready; execute the selected cases in the target environment.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "review",
                "Review Evidence",
                "review",
                reason="Review automation readiness and current coverage before execution.",
                secondary=True,
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    if execution_status == "passed" and (not reports_state or not reports_state.current_snapshot_id or reports_state.stale):
        actions.append(
            _action(
                "report",
                "Create Evidence Report",
                "reports",
                reason="Execution evidence is ready for a durable report/export snapshot.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "review",
                "Review Evidence",
                "review",
                reason="Review requirements, coverage, automation readiness, and execution outcomes before reporting.",
                secondary=True,
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    if not execution_state or not execution_state.current_snapshot_id:
        actions.append(
            _action(
                "automate",
                "Preview Automation",
                "automation",
                reason="Approved test cases are ready for automation preview.",
                primary=True,
            )
        )
        actions.append(
            _action(
                "report",
                "Create Test Case Report",
                "reports",
                reason="Approved test-case evidence can be reported before environment execution.",
                secondary=True,
            )
        )
        actions.append(
            _action(
                "review",
                "Review Evidence",
                "review",
                reason="Review generated coverage and traceability before automation or reporting.",
                secondary=True,
            )
        )
        actions.append(_full_regenerate_action(full_regenerate_blockers))
        return actions

    actions.append(
        _action(
            "review",
            "Review Evidence",
            "review",
            reason="Review current requirements, coverage, execution outcomes, and report evidence.",
            secondary=True,
        )
    )
    actions.append(
        _action(
            "report",
            "Review Reports",
            "reports",
            reason="The project has current execution/report evidence.",
            secondary=True,
        )
    )
    actions.append(_full_regenerate_action(full_regenerate_blockers))
    return actions


def build_orchestrator_status(project: Optional[QaProjectDetail]) -> OrchestratorStatusResponse:
    if project is None:
        missing_project = _blocker(
            "missing_project",
            "Open or create a QA project before orchestrator decisions can be derived.",
            stage="requirements",
        )
        stages = {
            stage: OrchestratorStageState(stage=stage, status="not_started", blockers=[missing_project] if stage == "requirements" else [])
            for stage in ORCHESTRATOR_STAGES
        }
        return OrchestratorStatusResponse(
            stages=stages,
            current_stage="requirements",
            next_actions=[
                _action(
                    "refine",
                    "Open QA Project",
                    "requirements",
                    reason="Orchestrator status is project-scoped.",
                    primary=True,
                    blockers=[missing_project],
                )
            ],
            blockers=[missing_project],
            generated_at=_utcnow(),
        )

    stages = _build_stages(project)
    has_baseline_test_suite = bool(_project_state(project, "test_cases") and _project_state(project, "test_cases").current_snapshot_id)
    changed_upstream_stages = _changed_upstream_stages(project)
    upstream_changed = has_baseline_test_suite and bool(changed_upstream_stages)
    actions = _build_actions(project, has_baseline_test_suite=has_baseline_test_suite, upstream_changed=upstream_changed)
    blockers: list[OrchestratorBlocker] = []
    for stage in stages.values():
        blockers.extend(stage.blockers)
    for action in actions:
        blockers.extend(action.blockers)

    return OrchestratorStatusResponse(
        project_id=project.project_id,
        project_revision=project.current_revision,
        current_stage=_current_stage(stages, actions),
        stages=stages,
        next_actions=actions,
        blockers=blockers,
        has_baseline_test_suite=has_baseline_test_suite,
        upstream_changed=upstream_changed,
        changed_upstream_stages=changed_upstream_stages,
        generated_at=_utcnow(),
    )


def get_project_orchestrator_status(project_id: str, *, actor: AuthUser) -> OrchestratorStatusResponse:
    return build_orchestrator_status(get_project(project_id, actor=actor))
