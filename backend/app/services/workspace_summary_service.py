from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import (
    AuthUser,
    OrchestratorActionRecommendation,
    OrchestratorStageName,
    OrchestratorStageState,
    OrchestratorStatusResponse,
    QaProjectDetail,
    QaProjectExecutionRun,
    QaProjectStageSnapshot,
    WorkspaceProjectSummary,
    WorkspaceReportSummary,
    WorkspaceRunSummary,
    WorkspaceSummaryResponse,
    WorkspaceWorkItem,
)
from .orchestrator_service import build_orchestrator_status
from .workflow_project_service import list_workspace_projects


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_work_item_id(project_id: str, stage: str, snapshot_id: Optional[str]) -> str:
    identity = f"{project_id}::{stage}::{snapshot_id or 'none'}"
    return f"work_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


def _first_count(mapping: dict[str, Any], keys: tuple[str, ...]) -> Optional[int]:
    for key in keys:
        count = _nonnegative_int(mapping.get(key))
        if count is not None:
            return count
    return None


def _snapshot_count(snapshot: Optional[QaProjectStageSnapshot], stage: OrchestratorStageName) -> Optional[int]:
    if snapshot is None:
        return None
    payload = dict(snapshot.payload or {})
    if stage == "requirements" and isinstance(payload.get("requirements"), list):
        return len(payload["requirements"])
    if stage == "use_cases" and isinstance(payload.get("coverage_plan"), list):
        scenario_count = sum(
            len(item.get("scenarios") or []) for item in payload["coverage_plan"] if isinstance(item, dict) and isinstance(item.get("scenarios") or [], list)
        )
        return scenario_count
    if stage == "impact_analysis" and isinstance(payload.get("changed_items"), list):
        return len(payload["changed_items"])
    if stage == "test_cases" and isinstance(payload.get("test_cases"), list):
        return len(payload["test_cases"])
    if stage == "reports":
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        if isinstance(evidence.get("evidence_refs"), list):
            return len(evidence["evidence_refs"])
    return None


def _work_item_count(
    project: QaProjectDetail,
    stage_state: OrchestratorStageState,
) -> Optional[int]:
    snapshot = project.current_snapshots.get(stage_state.stage) if stage_state.stage in project.current_snapshots else None
    snapshot_count = _snapshot_count(snapshot, stage_state.stage)
    if snapshot_count is not None:
        return snapshot_count
    return _first_count(
        dict(stage_state.summary or {}),
        (
            "requirements_total",
            "requirement_count",
            "coverage_plan_count",
            "requirement_analysis_count",
            "changed_item_count",
            "test_case_count",
            "executable_count",
            "evidence_count",
        ),
    )


def _work_item_kind(action: OrchestratorActionRecommendation) -> str:
    if not action.enabled:
        return "information"
    if action.action in {"approve", "review"}:
        return "review"
    return "action"


def _work_item_for(
    project: QaProjectDetail,
    status: OrchestratorStatusResponse,
    action: OrchestratorActionRecommendation,
) -> WorkspaceWorkItem:
    stage_state = status.stages[action.stage]
    snapshot_id = stage_state.current_snapshot_id
    updated_at = stage_state.updated_at or project.updated_at
    reason = action.reason
    if not action.enabled and action.blockers:
        reason = action.blockers[0].message
    return WorkspaceWorkItem(
        work_item_id=_stable_work_item_id(project.project_id, action.stage, snapshot_id),
        kind=_work_item_kind(action),
        project_id=project.project_id,
        project_name=project.name,
        project_revision=project.current_revision,
        stage=action.stage,
        status=stage_state.status,
        action=action.action,
        enabled=action.enabled,
        primary=action.primary,
        count=_work_item_count(project, stage_state),
        reason=reason,
        current_snapshot_id=snapshot_id,
        updated_at=updated_at,
    )


def _informational_work_item_for(
    project: QaProjectDetail,
    stage_state: OrchestratorStageState,
) -> WorkspaceWorkItem:
    reason = stage_state.stale_reason
    if stage_state.blockers:
        reason = stage_state.blockers[0].message
    if not reason:
        reason = f"{stage_state.stage.replace('_', ' ').title()} requires attention."
    return WorkspaceWorkItem(
        work_item_id=_stable_work_item_id(project.project_id, stage_state.stage, stage_state.current_snapshot_id),
        kind="information",
        project_id=project.project_id,
        project_name=project.name,
        project_revision=project.current_revision,
        stage=stage_state.stage,
        status=stage_state.status,
        enabled=False,
        primary=False,
        count=_work_item_count(project, stage_state),
        reason=reason,
        current_snapshot_id=stage_state.current_snapshot_id,
        updated_at=stage_state.updated_at or project.updated_at,
    )


def _deduplicate_work_items(items: list[WorkspaceWorkItem]) -> list[WorkspaceWorkItem]:
    selected: dict[tuple[str, str, Optional[str]], WorkspaceWorkItem] = {}
    for item in items:
        key = (item.project_id, item.stage, item.current_snapshot_id)
        existing = selected.get(key)
        if existing is None:
            selected[key] = item
            continue
        candidate_rank = (
            0 if item.action is not None and item.enabled else 1 if item.action is not None else 2,
            not item.primary,
            str(item.action or ""),
        )
        existing_rank = (
            0 if existing.action is not None and existing.enabled else 1 if existing.action is not None else 2,
            not existing.primary,
            str(existing.action or ""),
        )
        if candidate_rank < existing_rank:
            selected[key] = item
    return list(selected.values())


def _sort_work_items(items: list[WorkspaceWorkItem]) -> list[WorkspaceWorkItem]:
    items.sort(key=lambda item: (item.project_id, item.stage, item.current_snapshot_id or ""))
    items.sort(key=lambda item: item.updated_at, reverse=True)
    items.sort(key=lambda item: not item.enabled)
    return items


def _project_summary(project: QaProjectDetail, status: OrchestratorStatusResponse) -> WorkspaceProjectSummary:
    current_stage_state = status.stages[status.current_stage]
    primary_action = next((action for action in status.next_actions if action.primary), None)
    reason = primary_action.reason if primary_action is not None else (status.next_actions[0].reason if status.next_actions else None)
    return WorkspaceProjectSummary(
        project_id=project.project_id,
        name=project.name,
        project_revision=project.current_revision,
        project_status=project.status,
        current_stage=status.current_stage,
        current_status=current_stage_state.status,
        current_snapshot_id=current_stage_state.current_snapshot_id,
        completed_stage_count=sum(stage.status == "completed" for stage in status.stages.values()),
        total_stage_count=len(status.stages),
        reason=reason,
        updated_at=project.updated_at,
    )


def _run_count(summary: dict[str, Any], key: str) -> int:
    return _nonnegative_int(summary.get(key)) or 0


def _run_summary(project: QaProjectDetail, run: QaProjectExecutionRun) -> WorkspaceRunSummary:
    summary = dict(run.summary or {})
    passed_count = _run_count(summary, "passed")
    failed_count = _run_count(summary, "failed")
    invalid_count = _run_count(summary, "invalid")
    skipped_count = _run_count(summary, "skipped")
    executed_count = _first_count(summary, ("total", "executed"))
    if executed_count is None:
        executed_count = passed_count + failed_count + invalid_count + skipped_count
    return WorkspaceRunSummary(
        run_record_id=run.run_record_id,
        run_id=run.run_id,
        project_id=project.project_id,
        project_name=project.name,
        project_revision=run.project_revision,
        status=run.status,
        target_environment=run.target_environment,
        selected_count=len(run.selected_test_case_ids),
        executed_count=executed_count,
        passed_count=passed_count,
        failed_count=failed_count,
        invalid_count=invalid_count,
        skipped_count=skipped_count,
        snapshot_id=run.snapshot_id,
        source_snapshot_id=run.source_snapshot_id,
        updated_at=run.created_at,
    )


def _report_summary(project: QaProjectDetail) -> Optional[WorkspaceReportSummary]:
    state = project.stage_state.get("reports")
    snapshot = project.current_snapshots.get("reports")
    if state is None or snapshot is None:
        return None
    payload = dict(snapshot.payload or {})
    metadata = dict(snapshot.metadata or {})
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    execution_run_ids = metadata.get("execution_run_ids") or evidence.get("execution_run_ids") or []
    if not isinstance(execution_run_ids, list):
        execution_run_ids = []
    if not execution_run_ids and payload.get("run_id"):
        execution_run_ids = [payload["run_id"]]
    report_status = "stale" if state.stale else "approved" if state.approved else "draft"
    report_count = _snapshot_count(snapshot, "reports")
    return WorkspaceReportSummary(
        report_id=snapshot.snapshot_id,
        project_id=project.project_id,
        project_name=project.name,
        project_revision=snapshot.project_revision,
        status=report_status,
        report_type=str(payload.get("source") or snapshot.operation or "report"),
        format=str(payload.get("format") or metadata.get("format")) if payload.get("format") or metadata.get("format") else None,
        operation=snapshot.operation,
        approved=state.approved,
        stale=state.stale,
        count=report_count if report_count is not None else _first_count(metadata, ("evidence_count", "test_case_count")),
        source_snapshot_id=snapshot.source_snapshot_id,
        execution_run_ids=[str(item) for item in execution_run_ids[:20]],
        updated_at=state.updated_at or snapshot.created_at,
    )


def build_workspace_summary(
    projects: list[QaProjectDetail],
    *,
    work_items_limit: int,
    runs_limit: int,
    reports_limit: int,
    generated_at: Optional[datetime] = None,
) -> WorkspaceSummaryResponse:
    project_rows: list[WorkspaceProjectSummary] = []
    work_items: list[WorkspaceWorkItem] = []
    runs: list[WorkspaceRunSummary] = []
    reports: list[WorkspaceReportSummary] = []

    for project in projects:
        status = build_orchestrator_status(project)
        project_rows.append(_project_summary(project, status))
        work_items.extend(_work_item_for(project, status, action) for action in status.next_actions if action.action != "full_regenerate")
        work_items.extend(
            _informational_work_item_for(project, stage_state)
            for stage_state in status.stages.values()
            if stage_state.status in {"attention_required", "failed", "stale"}
        )
        runs.extend(_run_summary(project, run) for run in project.execution_runs)
        report = _report_summary(project)
        if report is not None:
            reports.append(report)

    project_rows.sort(key=lambda item: item.project_id)
    project_rows.sort(key=lambda item: item.updated_at, reverse=True)
    ranked_items = _sort_work_items(_deduplicate_work_items(work_items))[:work_items_limit]
    runs.sort(key=lambda item: item.run_record_id)
    runs.sort(key=lambda item: item.updated_at, reverse=True)
    reports.sort(key=lambda item: item.report_id)
    reports.sort(key=lambda item: item.updated_at, reverse=True)

    return WorkspaceSummaryResponse(
        continue_working=ranked_items[0] if ranked_items else None,
        projects=project_rows,
        work_items=ranked_items,
        recent_runs=runs[:runs_limit],
        recent_reports=reports[:reports_limit],
        generated_at=generated_at or _utcnow(),
    )


def get_workspace_summary(
    *,
    actor: AuthUser,
    include_archived: bool,
    projects_limit: int,
    work_items_limit: int,
    runs_limit: int,
    reports_limit: int,
) -> WorkspaceSummaryResponse:
    projects = list_workspace_projects(
        actor=actor,
        include_archived=include_archived,
        project_limit=projects_limit,
        execution_run_limit=runs_limit,
    )
    return build_workspace_summary(
        projects,
        work_items_limit=work_items_limit,
        runs_limit=runs_limit,
        reports_limit=reports_limit,
    )


__all__ = ["build_workspace_summary", "get_workspace_summary"]
