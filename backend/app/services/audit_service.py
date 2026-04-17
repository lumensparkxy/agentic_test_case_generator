import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from ..models import AuthUser
from .firebase_admin import get_firestore_client

WORKFLOW_RUNS_COLLECTION = "workflow_runs"
USAGE_EVENTS_COLLECTION = "usage_events"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items() if item is not None}

    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value if item is not None]

    if hasattr(value, "model_dump"):
        return _serialize_value(value.model_dump())

    return str(value)


def build_actor_snapshot(user: Optional[AuthUser]) -> Dict[str, Any]:
    if user is None:
        return {}

    return {
        "user_id": user.sub,
        "email": user.email,
        "name": user.name,
        "provider": user.provider,
        "email_verified": user.email_verified,
    }


def _get_collection(collection_name: str):
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime config
        logging.warning("Firestore client unavailable for %s writes: %s", collection_name, exc)
        return None

    return client.collection(collection_name)


def _safe_set(document_ref, payload: Dict[str, Any], *, operation: str) -> None:
    try:
        document_ref.set(payload)
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore %s skipped because write failed: %s", operation, exc)


def _safe_update(document_ref, payload: Dict[str, Any], *, operation: str) -> None:
    try:
        document_ref.update(payload)
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore %s skipped because update failed: %s", operation, exc)


def start_workflow_run(
    *,
    operation: str,
    actor: Optional[AuthUser],
    request_id: str,
    status: str = "started",
    metadata: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[str] = None,
) -> str:
    run_id = str(uuid4())
    collection = _get_collection(WORKFLOW_RUNS_COLLECTION)
    payload = {
        "run_id": run_id,
        "operation": operation,
        "status": status,
        "request_id": request_id,
        "workspace_id": workspace_id,
        "actor": build_actor_snapshot(actor),
        "actor_user_id": actor.sub if actor else None,
        "started_at": _utcnow(),
        "updated_at": _utcnow(),
        "metadata": _serialize_value(metadata or {}),
    }

    if collection is not None:
                _safe_set(collection.document(run_id), payload, operation="workflow_run_start")

    return run_id


def complete_workflow_run(
    run_id: str,
    *,
    status: str,
    metadata: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    collection = _get_collection(WORKFLOW_RUNS_COLLECTION)
    if collection is None:
        return

    payload = {
        "status": status,
        "completed_at": _utcnow(),
        "updated_at": _utcnow(),
        "error_message": error_message,
        "result": _serialize_value(metadata or {}),
    }
    _safe_update(collection.document(run_id), payload, operation="workflow_run_complete")


def record_usage_event(
    *,
    event_type: str,
    billing_key: str,
    quantity: int,
    unit: str,
    actor: Optional[AuthUser],
    request_id: str,
    workflow_run_id: Optional[str],
    status: str,
    metadata: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[str] = None,
) -> str:
    event_id = str(uuid4())
    collection = _get_collection(USAGE_EVENTS_COLLECTION)
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "billing_key": billing_key,
        "quantity": int(quantity),
        "unit": unit,
        "occurred_at": _utcnow(),
        "actor": build_actor_snapshot(actor),
        "actor_user_id": actor.sub if actor else None,
        "workspace_id": workspace_id,
        "workflow_run_id": workflow_run_id,
        "request_id": request_id,
        "status": status,
        "metadata": _serialize_value(metadata or {}),
    }

    if collection is not None:
                _safe_set(collection.document(event_id), payload, operation="usage_event_record")

    return event_id