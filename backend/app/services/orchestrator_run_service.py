from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import (
    AuthUser,
    OrchestratorActionId,
    OrchestratorBlocker,
    OrchestratorCheckpointRecord,
    OrchestratorRunEvent,
    OrchestratorRunEventType,
    OrchestratorRunListResponse,
    OrchestratorRunRecord,
    OrchestratorStageName,
)
from .firestore_repository import get_required_firestore_collection
from .workflow_project_service import ProjectConflictError, ProjectNotFoundError, ProjectPermissionError

QA_PROJECTS_COLLECTION = "qa_projects"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


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


def _collection():
    return get_required_firestore_collection(
        QA_PROJECTS_COLLECTION,
        unavailable_message=f"Firestore client unavailable for {QA_PROJECTS_COLLECTION} orchestrator persistence",
    )


def _project_doc(project_id: str):
    return _collection().document(project_id)


def _document_to_dict(document_snapshot: Any) -> Optional[dict[str, Any]]:
    if document_snapshot is None or not getattr(document_snapshot, "exists", False):
        return None
    return dict(document_snapshot.to_dict() or {})


def _project_payload(project_id: str) -> dict[str, Any]:
    payload = _document_to_dict(_project_doc(project_id).get())
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


def _run_doc(project_id: str, run_id: str):
    return _project_doc(project_id).collection("orchestrator_runs").document(run_id)


def _event_doc(project_id: str, event_id: str):
    return _project_doc(project_id).collection("orchestrator_events").document(event_id)


def _checkpoint_doc(project_id: str, checkpoint_id: str):
    return _project_doc(project_id).collection("orchestrator_checkpoints").document(checkpoint_id)


def _stream_collection(collection_ref: Any) -> list[dict[str, Any]]:
    try:
        return [dict(snapshot.to_dict() or {}) for snapshot in collection_ref.stream()]
    except Exception as exc:  # pragma: no cover - defensive around external store
        logging.warning("Orchestrator subcollection stream skipped: %s", exc)
        return []


def _load_run(project_id: str, run_id: str) -> OrchestratorRunRecord:
    payload = _document_to_dict(_run_doc(project_id, run_id).get())
    if payload is None:
        raise ProjectNotFoundError(run_id)
    return OrchestratorRunRecord.model_validate(payload)


def _append_event(
    *,
    project_id: str,
    run_id: str,
    event_type: OrchestratorRunEventType,
    summary: str,
    actor: AuthUser,
    request_id: str,
    project_revision: int,
    action: Optional[OrchestratorActionId] = None,
    stage: Optional[OrchestratorStageName] = None,
    checkpoint_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> OrchestratorRunEvent:
    event_id = (
        _stable_id("orchevent", project_id, run_id, event_type, idempotency_key)
        if idempotency_key
        else _stable_id("orchevent", project_id, run_id, event_type, _utcnow().isoformat())
    )
    existing = _document_to_dict(_event_doc(project_id, event_id).get())
    if existing is not None:
        return OrchestratorRunEvent.model_validate(existing)
    payload = {
        "event_id": event_id,
        "run_id": run_id,
        "project_id": project_id,
        "event_type": event_type,
        "summary": summary,
        "action": action,
        "stage": stage,
        "project_revision": project_revision,
        "checkpoint_id": checkpoint_id,
        "actor_user_id": actor.sub,
        "request_id": request_id,
        "metadata": _serialize_value(metadata or {}),
        "occurred_at": _utcnow(),
    }
    _event_doc(project_id, event_id).set(payload)
    return OrchestratorRunEvent.model_validate(payload)


def start_orchestrator_run(
    *,
    project_id: str,
    action: OrchestratorActionId,
    stage: OrchestratorStageName,
    actor: AuthUser,
    request_id: str,
    metadata: Optional[dict[str, Any]] = None,
    base_project_revision: Optional[int] = None,
) -> OrchestratorRunRecord:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    idempotency_key = f"{action}:{request_id}"
    run_id = _stable_id("orchestrator", project_id, idempotency_key)
    existing = _document_to_dict(_run_doc(project_id, run_id).get())
    if existing is not None:
        return OrchestratorRunRecord.model_validate(existing)
    _check_revision(project_payload, base_project_revision)

    now = _utcnow()
    project_revision = int(project_payload.get("current_revision") or 0)
    payload = {
        "run_id": run_id,
        "project_id": project_id,
        "action": action,
        "status": "running",
        "current_stage": stage,
        "current_action": action,
        "project_revision": project_revision,
        "request_id": request_id,
        "actor_user_id": actor.sub,
        "idempotency_key": idempotency_key,
        "current_checkpoint_id": None,
        "produced_snapshot_ids": {},
        "execution_run_ids": [],
        "blockers": [],
        "next_unblock_action": None,
        "metadata": _serialize_value(metadata or {}),
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    _run_doc(project_id, run_id).set(payload)
    _append_event(
        project_id=project_id,
        run_id=run_id,
        event_type="run_started",
        summary=f"Started orchestrator action {action}.",
        actor=actor,
        request_id=request_id,
        project_revision=project_revision,
        action=action,
        stage=stage,
        metadata=metadata,
        idempotency_key=f"{idempotency_key}:run_started",
    )
    return OrchestratorRunRecord.model_validate(payload)


def save_orchestrator_checkpoint(
    *,
    project_id: str,
    run_id: str,
    action: OrchestratorActionId,
    stage: OrchestratorStageName,
    actor: AuthUser,
    request_id: str,
    source_snapshot_ids: Optional[dict[str, Optional[str]]] = None,
    output_snapshot_ids: Optional[dict[str, Optional[str]]] = None,
    agent_output_refs: Optional[list[dict[str, Any]]] = None,
    execution_run_ids: Optional[list[str]] = None,
    blockers: Optional[list[OrchestratorBlocker]] = None,
    next_action: Optional[OrchestratorActionId] = None,
    metadata: Optional[dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> OrchestratorCheckpointRecord:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    run = _load_run(project_id, run_id)
    checkpoint_key = idempotency_key or f"{action}:{stage}:{request_id}"
    checkpoint_id = _stable_id("checkpoint", project_id, run_id, checkpoint_key)
    project_revision = int(project_payload.get("current_revision") or run.project_revision or 0)
    now = _utcnow()
    checkpoint_payload = {
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
        "project_id": project_id,
        "action": action,
        "stage": stage,
        "project_revision": project_revision,
        "request_id": request_id,
        "actor_user_id": actor.sub,
        "source_snapshot_ids": _serialize_value(source_snapshot_ids or {}),
        "output_snapshot_ids": _serialize_value(output_snapshot_ids or {}),
        "agent_output_refs": _serialize_value(agent_output_refs or []),
        "execution_run_ids": list(dict.fromkeys(execution_run_ids or [])),
        "blockers": _serialize_value(blockers or []),
        "next_action": next_action,
        "metadata": _serialize_value(metadata or {}),
        "updated_at": now,
    }
    _checkpoint_doc(project_id, checkpoint_id).set(checkpoint_payload)

    produced_snapshot_ids = {**run.produced_snapshot_ids, **(output_snapshot_ids or {})}
    merged_execution_run_ids = list(dict.fromkeys([*run.execution_run_ids, *(execution_run_ids or [])]))
    _run_doc(project_id, run_id).update(
        {
            "current_checkpoint_id": checkpoint_id,
            "project_revision": project_revision,
            "produced_snapshot_ids": _serialize_value(produced_snapshot_ids),
            "execution_run_ids": merged_execution_run_ids,
            "blockers": _serialize_value(blockers or run.blockers),
            "next_unblock_action": next_action if blockers else run.next_unblock_action,
            "updated_at": now,
        }
    )
    _append_event(
        project_id=project_id,
        run_id=run_id,
        event_type="checkpoint_saved",
        summary=f"Saved checkpoint for {action}.",
        actor=actor,
        request_id=request_id,
        project_revision=project_revision,
        action=action,
        stage=stage,
        checkpoint_id=checkpoint_id,
        metadata={"output_snapshot_ids": output_snapshot_ids or {}, "execution_run_ids": execution_run_ids or []},
        idempotency_key=f"{idempotency_key or request_id}:checkpoint_saved",
    )
    return OrchestratorCheckpointRecord.model_validate(checkpoint_payload)


def record_orchestrator_event(
    *,
    project_id: str,
    run_id: str,
    event_type: OrchestratorRunEventType,
    summary: str,
    actor: AuthUser,
    request_id: str,
    metadata: Optional[dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> OrchestratorRunEvent:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    run = _load_run(project_id, run_id)
    project_revision = int(project_payload.get("current_revision") or run.project_revision or 0)
    return _append_event(
        project_id=project_id,
        run_id=run_id,
        event_type=event_type,
        summary=summary,
        actor=actor,
        request_id=request_id,
        project_revision=project_revision,
        action=run.current_action,
        stage=run.current_stage,
        metadata=metadata,
        idempotency_key=idempotency_key or f"{run.idempotency_key}:{event_type}:{request_id}",
    )


def block_orchestrator_run(
    *,
    project_id: str,
    run_id: str,
    actor: AuthUser,
    request_id: str,
    blockers: list[OrchestratorBlocker],
    next_unblock_action: Optional[OrchestratorActionId],
    metadata: Optional[dict[str, Any]] = None,
) -> OrchestratorRunRecord:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    run = _load_run(project_id, run_id)
    now = _utcnow()
    update_payload = {
        "status": "blocked",
        "blockers": _serialize_value(blockers),
        "next_unblock_action": next_unblock_action,
        "metadata": {**run.metadata, **_serialize_value(metadata or {})},
        "updated_at": now,
    }
    _run_doc(project_id, run_id).update(update_payload)
    _append_event(
        project_id=project_id,
        run_id=run_id,
        event_type="blocked",
        summary="Orchestrator run is blocked.",
        actor=actor,
        request_id=request_id,
        project_revision=run.project_revision,
        action=run.current_action,
        stage=run.current_stage,
        metadata={"blockers": _serialize_value(blockers), "next_unblock_action": next_unblock_action},
        idempotency_key=f"{run.idempotency_key}:blocked",
    )
    return _load_run(project_id, run_id)


def complete_orchestrator_run(
    *,
    project_id: str,
    run_id: str,
    actor: AuthUser,
    request_id: str,
    produced_snapshot_ids: Optional[dict[str, Optional[str]]] = None,
    execution_run_ids: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> OrchestratorRunRecord:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    run = _load_run(project_id, run_id)
    now = _utcnow()
    merged_snapshots = {**run.produced_snapshot_ids, **(produced_snapshot_ids or {})}
    merged_execution_run_ids = list(dict.fromkeys([*run.execution_run_ids, *(execution_run_ids or [])]))
    _run_doc(project_id, run_id).update(
        {
            "status": "completed",
            "produced_snapshot_ids": _serialize_value(merged_snapshots),
            "execution_run_ids": merged_execution_run_ids,
            "blockers": [],
            "next_unblock_action": None,
            "metadata": {**run.metadata, **_serialize_value(metadata or {})},
            "updated_at": now,
            "completed_at": now,
        }
    )
    _append_event(
        project_id=project_id,
        run_id=run_id,
        event_type="action_completed",
        summary=f"Completed orchestrator action {run.current_action}.",
        actor=actor,
        request_id=request_id,
        project_revision=run.project_revision,
        action=run.current_action,
        stage=run.current_stage,
        metadata={"produced_snapshot_ids": merged_snapshots, "execution_run_ids": merged_execution_run_ids},
        idempotency_key=f"{run.idempotency_key}:completed",
    )
    return _load_run(project_id, run_id)


def fail_orchestrator_run(
    *,
    project_id: str,
    run_id: str,
    actor: AuthUser,
    request_id: str,
    error_message: str,
    retryable: bool = False,
) -> OrchestratorRunRecord:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    run = _load_run(project_id, run_id)
    now = _utcnow()
    _run_doc(project_id, run_id).update(
        {
            "status": "failed",
            "metadata": {**run.metadata, "error_message": error_message, "retryable": retryable},
            "updated_at": now,
            "completed_at": now,
        }
    )
    _append_event(
        project_id=project_id,
        run_id=run_id,
        event_type="run_failed",
        summary=error_message,
        actor=actor,
        request_id=request_id,
        project_revision=run.project_revision,
        action=run.current_action,
        stage=run.current_stage,
        metadata={"retryable": retryable},
        idempotency_key=f"{run.idempotency_key}:failed",
    )
    return _load_run(project_id, run_id)


def list_orchestrator_runs(
    *,
    project_id: str,
    actor: AuthUser,
    limit: int = 20,
) -> OrchestratorRunListResponse:
    project_payload = _project_payload(project_id)
    _require_owner(project_payload, actor)
    project_doc = _project_doc(project_id)
    runs = [OrchestratorRunRecord.model_validate(item) for item in _stream_collection(project_doc.collection("orchestrator_runs")) if item.get("run_id")]
    runs.sort(key=lambda item: item.updated_at, reverse=True)
    selected_run_ids = {run.run_id for run in runs[:limit]}
    events = [
        OrchestratorRunEvent.model_validate(item)
        for item in _stream_collection(project_doc.collection("orchestrator_events"))
        if item.get("event_id") and item.get("run_id") in selected_run_ids
    ]
    events.sort(key=lambda item: item.occurred_at, reverse=True)
    checkpoints = [
        OrchestratorCheckpointRecord.model_validate(item)
        for item in _stream_collection(project_doc.collection("orchestrator_checkpoints"))
        if item.get("checkpoint_id") and item.get("run_id") in selected_run_ids
    ]
    checkpoints.sort(key=lambda item: item.updated_at, reverse=True)
    return OrchestratorRunListResponse(
        runs=runs[:limit],
        events=events[: limit * 10],
        checkpoints=checkpoints[:limit],
    )
