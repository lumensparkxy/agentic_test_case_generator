import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException, status
from google.cloud.firestore_v1 import FieldFilter, Query

from ..models import (
    AuthUser,
    ProjectStageName,
    QaProjectDetail,
    QaProjectExecutionRun,
    QaProjectStageSnapshot,
    QaProjectStageState,
    QaProjectSummary,
    QaProjectTimelineEvent,
)
from .audit_service import build_actor_snapshot
from .firestore_repository import get_required_firestore_collection

QA_PROJECTS_COLLECTION = "qa_projects"
PROJECT_STAGES: tuple[ProjectStageName, ...] = (
    "requirements",
    "context",
    "use_cases",
    "impact_analysis",
    "test_cases",
    "execution",
    "reports",
)
DOWNSTREAM_STAGES: dict[ProjectStageName, tuple[ProjectStageName, ...]] = {
    "requirements": ("context", "use_cases", "impact_analysis", "test_cases", "execution", "reports"),
    "context": ("use_cases", "impact_analysis", "test_cases", "execution", "reports"),
    "use_cases": ("impact_analysis", "test_cases", "execution", "reports"),
    "impact_analysis": ("test_cases", "execution", "reports"),
    "test_cases": ("execution", "reports"),
    "execution": ("reports",),
    "reports": (),
}
WORKSPACE_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "snapshot_id",
    "project_id",
    "stage",
    "version",
    "project_revision",
    "operation",
    "approved",
    "source_snapshot_id",
    "title",
    "metadata",
    "created_at",
    "payload.review",
    "payload.summary",
    "payload.changed_items",
    "payload.status",
    "payload.source",
    "payload.format",
    "payload.run_id",
    "payload.evidence.execution_run_ids",
)


class ProjectConflictError(RuntimeError):
    def __init__(self, latest_revision: int) -> None:
        super().__init__("Project has changed since the client loaded it")
        self.latest_revision = latest_revision


class ProjectNotFoundError(RuntimeError):
    pass


class ProjectPermissionError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_document_id(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _collection():
    return get_required_firestore_collection(
        QA_PROJECTS_COLLECTION,
        unavailable_message=f"Firestore client unavailable for {QA_PROJECTS_COLLECTION} persistence",
    )


def _document_to_dict(document_snapshot: Any) -> Optional[dict[str, Any]]:
    if document_snapshot is None or not getattr(document_snapshot, "exists", False):
        return None
    data = document_snapshot.to_dict() or {}
    return dict(data)


def _get_project_doc(project_id: str):
    return _collection().document(project_id)


def _get_project_payload(project_id: str) -> dict[str, Any]:
    payload = _document_to_dict(_get_project_doc(project_id).get())
    if payload is None:
        raise ProjectNotFoundError(project_id)
    return payload


def _require_owner(payload: dict[str, Any], actor: AuthUser) -> None:
    if payload.get("owner_user_id") != actor.sub:
        raise ProjectPermissionError(str(payload.get("project_id") or "project"))


def _check_revision(payload: dict[str, Any], base_project_revision: Optional[int]) -> None:
    if base_project_revision is None:
        return
    current_revision = int(payload.get("current_revision") or 0)
    if int(base_project_revision) != current_revision:
        raise ProjectConflictError(current_revision)


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _serialize_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value if item is not None]
    return str(value)


def _stage_state_from_payload(payload: dict[str, Any]) -> dict[ProjectStageName, dict[str, Any]]:
    state = dict(payload.get("stage_state") or {})
    return {stage: dict(state.get(stage) or {}) for stage in PROJECT_STAGES if state.get(stage)}


def _stage_state_model(payload: dict[str, Any]) -> dict[ProjectStageName, QaProjectStageState]:
    return {stage: QaProjectStageState.model_validate(value) for stage, value in _stage_state_from_payload(payload).items()}


def _summary_from_payload(payload: dict[str, Any]) -> QaProjectSummary:
    return QaProjectSummary(
        project_id=str(payload["project_id"]),
        name=str(payload["name"]),
        description=payload.get("description"),
        status=payload.get("status") or "active",
        owner_user_id=str(payload["owner_user_id"]),
        current_revision=int(payload.get("current_revision") or 0),
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
        stage_state=_stage_state_model(payload),
    )


def _stream_collection(collection_ref: Any) -> list[dict[str, Any]]:
    try:
        return [dict(snapshot.to_dict() or {}) for snapshot in collection_ref.stream()]
    except Exception as exc:  # pragma: no cover - defensive around external store
        logging.warning("Project subcollection stream skipped: %s", exc)
        return []


def _snapshot_for(project_id: str, snapshot_id: str) -> Optional[QaProjectStageSnapshot]:
    try:
        payload = _document_to_dict(_get_project_doc(project_id).collection("snapshots").document(snapshot_id).get())
    except Exception as exc:  # pragma: no cover - defensive around external store
        logging.warning("Project snapshot load skipped for %s: %s", snapshot_id, exc)
        return None
    if not payload:
        return None
    return QaProjectStageSnapshot.model_validate(payload)


def get_project(project_id: str, *, actor: AuthUser) -> QaProjectDetail:
    payload = _get_project_payload(project_id)
    _require_owner(payload, actor)
    summary = _summary_from_payload(payload)
    current_snapshots: dict[ProjectStageName, QaProjectStageSnapshot] = {}
    for stage, state in summary.stage_state.items():
        if state.current_snapshot_id:
            snapshot = _snapshot_for(project_id, state.current_snapshot_id)
            if snapshot is not None:
                current_snapshots[stage] = snapshot

    project_doc = _get_project_doc(project_id)
    timeline = [QaProjectTimelineEvent.model_validate(item) for item in _stream_collection(project_doc.collection("timeline")) if item.get("event_id")]
    timeline.sort(key=lambda item: item.occurred_at, reverse=True)
    execution_runs = [
        QaProjectExecutionRun.model_validate(item) for item in _stream_collection(project_doc.collection("execution_runs")) if item.get("run_record_id")
    ]
    execution_runs.sort(key=lambda item: item.created_at, reverse=True)
    return QaProjectDetail(
        **summary.model_dump(),
        current_snapshots=current_snapshots,
        timeline=timeline[:100],
        execution_runs=execution_runs[:100],
    )


def get_project_stage_snapshot(project_id: str, snapshot_id: str, *, actor: AuthUser) -> QaProjectStageSnapshot:
    payload = _get_project_payload(project_id)
    _require_owner(payload, actor)
    snapshot = _snapshot_for(project_id, snapshot_id)
    if snapshot is None:
        raise ProjectNotFoundError(snapshot_id)
    return snapshot


def list_projects(*, actor: AuthUser, include_archived: bool = False) -> list[QaProjectSummary]:
    projects: list[QaProjectSummary] = []
    for item in _stream_collection(_collection()):
        if item.get("owner_user_id") != actor.sub:
            continue
        if not include_archived and item.get("status") == "archived":
            continue
        projects.append(_summary_from_payload(item))
    projects.sort(key=lambda item: item.updated_at, reverse=True)
    return projects


def _workspace_snapshot_for(project_id: str, snapshot_id: str) -> Optional[QaProjectStageSnapshot]:
    payload = _document_to_dict(_get_project_doc(project_id).collection("snapshots").document(snapshot_id).get(field_paths=WORKSPACE_SNAPSHOT_FIELDS))
    if not payload:
        return None
    snapshot = QaProjectStageSnapshot.model_validate(payload)
    if snapshot.project_id != project_id or snapshot.snapshot_id != snapshot_id:
        return None
    return snapshot


def _workspace_execution_runs(
    project_doc: Any,
    *,
    actor: AuthUser,
    project_id: str,
    limit: int,
) -> list[QaProjectExecutionRun]:
    query = (
        project_doc.collection("execution_runs")
        .where(filter=FieldFilter("actor_user_id", "==", actor.sub))
        .where(filter=FieldFilter("project_id", "==", project_id))
        .order_by("created_at", direction=Query.DESCENDING)
        .order_by("run_record_id", direction=Query.ASCENDING)
        .select(
            (
                "run_record_id",
                "project_id",
                "run_id",
                "target_environment",
                "project_revision",
                "test_case_count",
                "status",
                "summary",
                "snapshot_id",
                "source_snapshot_id",
                "selected_test_case_ids",
                "actor_user_id",
                "created_at",
            )
        )
        .limit(limit)
    )
    runs = [
        QaProjectExecutionRun.model_validate(payload)
        for snapshot in query.stream()
        if (payload := dict(snapshot.to_dict() or {})).get("run_record_id")
        and payload.get("actor_user_id") == actor.sub
        and payload.get("project_id") == project_id
    ]
    runs.sort(key=lambda item: item.run_record_id)
    runs.sort(key=lambda item: item.created_at, reverse=True)
    return runs


def list_workspace_projects(
    *,
    actor: AuthUser,
    include_archived: bool,
    project_limit: int,
    execution_run_limit: int,
) -> list[QaProjectDetail]:
    """Load a bounded, workspace-safe project projection.

    Production requires a composite ``qa_projects`` index over
    ``owner_user_id``, ``status`` (for the default view), ``updated_at``
    descending, and ``project_id`` ascending. The query intentionally fails
    closed when that index is unavailable instead of falling back to an
    unbounded collection scan.
    """

    query = _collection().where(filter=FieldFilter("owner_user_id", "==", actor.sub))
    if not include_archived:
        query = query.where(filter=FieldFilter("status", "==", "active"))
    query = (
        query.order_by("updated_at", direction=Query.DESCENDING)
        .order_by("project_id", direction=Query.ASCENDING)
        .select(
            (
                "project_id",
                "name",
                "status",
                "owner_user_id",
                "current_revision",
                "created_at",
                "updated_at",
                "stage_state",
            )
        )
        .limit(project_limit)
    )

    projects: list[QaProjectDetail] = []
    for snapshot in query.stream():
        payload = dict(snapshot.to_dict() or {})
        if not payload or payload.get("owner_user_id") != actor.sub:
            continue
        if not include_archived and payload.get("status") == "archived":
            continue

        summary = _summary_from_payload(payload)
        current_snapshots: dict[ProjectStageName, QaProjectStageSnapshot] = {}
        for stage, state in summary.stage_state.items():
            if not state.current_snapshot_id:
                continue
            current_snapshot = _workspace_snapshot_for(summary.project_id, state.current_snapshot_id)
            if current_snapshot is not None and current_snapshot.stage == stage:
                current_snapshots[stage] = current_snapshot

        project_doc = _get_project_doc(summary.project_id)
        execution_runs = _workspace_execution_runs(
            project_doc,
            actor=actor,
            project_id=summary.project_id,
            limit=execution_run_limit,
        )
        projects.append(
            QaProjectDetail(
                **summary.model_dump(),
                current_snapshots=current_snapshots,
                timeline=[],
                execution_runs=execution_runs,
            )
        )

    projects.sort(key=lambda item: item.project_id)
    projects.sort(key=lambda item: item.updated_at, reverse=True)
    return projects


def _record_timeline_event(project_doc: Any, payload: dict[str, Any]) -> None:
    event_id = payload["event_id"]
    project_doc.collection("timeline").document(event_id).set(payload)


def create_project(*, name: str, description: Optional[str], actor: AuthUser, request_id: str) -> QaProjectDetail:
    now = _utcnow()
    project_id = str(uuid4())
    project_doc = _collection().document(project_id)
    project_payload = {
        "project_id": project_id,
        "name": name.strip(),
        "description": description.strip() if description else None,
        "status": "active",
        "owner_user_id": actor.sub,
        "actor": build_actor_snapshot(actor),
        "created_at": now,
        "updated_at": now,
        "current_revision": 1,
        "stage_state": {},
        "request_id": request_id,
    }
    project_doc.set(project_payload)
    _record_timeline_event(
        project_doc,
        {
            "event_id": str(uuid4()),
            "project_id": project_id,
            "event_type": "project.created",
            "summary": f"Project created: {name.strip()}",
            "project_revision": 1,
            "actor_user_id": actor.sub,
            "metadata": {},
            "occurred_at": now,
        },
    )
    return get_project(project_id, actor=actor)


def update_project(
    *,
    project_id: str,
    actor: AuthUser,
    request_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    status_value: Optional[str] = None,
    base_project_revision: Optional[int] = None,
) -> QaProjectDetail:
    payload = _get_project_payload(project_id)
    _require_owner(payload, actor)
    _check_revision(payload, base_project_revision)
    now = _utcnow()
    next_revision = int(payload.get("current_revision") or 0) + 1
    update_payload: dict[str, Any] = {
        "updated_at": now,
        "current_revision": next_revision,
        "request_id": request_id,
    }
    if name is not None:
        update_payload["name"] = name.strip()
    if description is not None:
        update_payload["description"] = description.strip() or None
    if status_value is not None:
        update_payload["status"] = status_value
    project_doc = _get_project_doc(project_id)
    project_doc.update(update_payload)
    _record_timeline_event(
        project_doc,
        {
            "event_id": str(uuid4()),
            "project_id": project_id,
            "event_type": "project.updated",
            "summary": "Project updated",
            "project_revision": next_revision,
            "actor_user_id": actor.sub,
            "metadata": {"status": status_value} if status_value else {},
            "occurred_at": now,
        },
    )
    return get_project(project_id, actor=actor)


def append_stage_snapshot(
    *,
    project_id: str,
    stage: ProjectStageName,
    payload: dict[str, Any],
    operation: str,
    actor: AuthUser,
    request_id: str,
    workflow_run_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    approved: bool = False,
    source_snapshot_id: Optional[str] = None,
    title: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    base_project_revision: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> QaProjectStageSnapshot:
    project_payload = _get_project_payload(project_id)
    _require_owner(project_payload, actor)
    snapshot_id = _stable_document_id("snapshot", project_id, stage, operation, idempotency_key) if idempotency_key else str(uuid4())
    if idempotency_key:
        existing_snapshot = _snapshot_for(project_id, snapshot_id)
        if existing_snapshot is not None:
            return existing_snapshot
    _check_revision(project_payload, base_project_revision)

    now = _utcnow()
    project_doc = _get_project_doc(project_id)
    current_revision = int(project_payload.get("current_revision") or 0)
    next_revision = current_revision + 1
    stage_state = _stage_state_from_payload(project_payload)
    previous_state = stage_state.get(stage) or {}
    version = int(previous_state.get("version") or 0) + 1
    snapshot_payload = {
        "snapshot_id": snapshot_id,
        "project_id": project_id,
        "stage": stage,
        "version": version,
        "project_revision": next_revision,
        "operation": operation,
        "approved": bool(approved),
        "source_snapshot_id": source_snapshot_id,
        "workflow_run_id": workflow_run_id,
        "source_event_id": source_event_id,
        "request_id": request_id,
        "actor_user_id": actor.sub,
        "title": title,
        "metadata": _serialize_value(metadata or {}),
        "payload": _serialize_value(payload),
        "idempotency_key": idempotency_key,
        "created_at": now,
    }
    project_doc.collection("snapshots").document(snapshot_id).set(snapshot_payload)

    stage_state[stage] = {
        "current_snapshot_id": snapshot_id,
        "version": version,
        "approved": bool(approved),
        "stale": False,
        "stale_reason": None,
        "updated_at": now,
        "operation": operation,
        "source_snapshot_id": source_snapshot_id,
        "metadata": _serialize_value(metadata or {}),
    }
    for downstream_stage in DOWNSTREAM_STAGES[stage]:
        if downstream_stage not in stage_state:
            continue
        downstream = dict(stage_state[downstream_stage])
        downstream["stale"] = True
        downstream["stale_reason"] = f"{stage} changed in project revision {next_revision}"
        stage_state[downstream_stage] = downstream

    project_doc.update(
        {
            "updated_at": now,
            "current_revision": next_revision,
            "stage_state": stage_state,
            "latest_stage": stage,
        }
    )
    _record_timeline_event(
        project_doc,
        {
            "event_id": str(uuid4()),
            "project_id": project_id,
            "event_type": "stage.snapshot_created",
            "stage": stage,
            "summary": title or f"{stage.replace('_', ' ').title()} v{version} saved",
            "project_revision": next_revision,
            "snapshot_id": snapshot_id,
            "actor_user_id": actor.sub,
            "metadata": {"operation": operation, **_serialize_value(metadata or {})},
            "occurred_at": now,
        },
    )
    return QaProjectStageSnapshot.model_validate(snapshot_payload)


def record_execution_run(
    *,
    project_id: str,
    actor: AuthUser,
    request_id: str,
    run_id: str,
    target_environment: str,
    status_value: str,
    summary: dict[str, Any],
    test_case_count: int,
    snapshot_id: Optional[str],
    workflow_run_id: Optional[str],
    source_event_id: Optional[str],
    project_revision: int,
    target_base_url: Optional[str] = None,
    source_snapshot_id: Optional[str] = None,
    selected_test_case_ids: Optional[list[str]] = None,
    artifacts_root: Optional[str] = None,
    playwright_report_paths: Optional[list[str]] = None,
    idempotency_key: Optional[str] = None,
) -> QaProjectExecutionRun:
    now = _utcnow()
    run_record_id = _stable_document_id("execution", project_id, idempotency_key) if idempotency_key else str(uuid4())
    if idempotency_key:
        existing = _document_to_dict(_get_project_doc(project_id).collection("execution_runs").document(run_record_id).get())
        if existing is not None:
            return QaProjectExecutionRun.model_validate(existing)
    payload = {
        "run_record_id": run_record_id,
        "project_id": project_id,
        "run_id": run_id,
        "target_environment": target_environment,
        "target_base_url": target_base_url,
        "artifacts_root": artifacts_root,
        "playwright_report_paths": _serialize_value(playwright_report_paths or []),
        "project_revision": project_revision,
        "test_case_count": test_case_count,
        "status": status_value,
        "summary": _serialize_value(summary),
        "snapshot_id": snapshot_id,
        "source_snapshot_id": source_snapshot_id,
        "selected_test_case_ids": _serialize_value(selected_test_case_ids or []),
        "workflow_run_id": workflow_run_id,
        "source_event_id": source_event_id,
        "request_id": request_id,
        "actor_user_id": actor.sub,
        "idempotency_key": idempotency_key,
        "created_at": now,
    }
    project_doc = _get_project_doc(project_id)
    project_doc.collection("execution_runs").document(run_record_id).set(payload)
    _record_timeline_event(
        project_doc,
        {
            "event_id": str(uuid4()),
            "project_id": project_id,
            "event_type": "execution.run_recorded",
            "stage": "execution",
            "summary": f"{target_environment} execution {status_value}",
            "project_revision": project_revision,
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "actor_user_id": actor.sub,
            "metadata": {
                "summary": _serialize_value(summary),
                "target_environment": target_environment,
                "target_base_url": target_base_url,
                "artifacts_root": artifacts_root,
                "playwright_report_paths": _serialize_value(playwright_report_paths or []),
                "source_snapshot_id": source_snapshot_id,
                "selected_test_case_ids": _serialize_value(selected_test_case_ids or []),
            },
            "occurred_at": now,
        },
    )
    return QaProjectExecutionRun.model_validate(payload)


def project_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if isinstance(exc, ProjectPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project access denied")
    if isinstance(exc, ProjectConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Project has changed since this page loaded.", "latest_revision": exc.latest_revision},
        )
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Project persistence is unavailable")
