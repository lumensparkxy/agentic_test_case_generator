import logging
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Optional
from uuid import uuid4

from ..models import AuthUser
from ..observability.metrics import (
    record_audit_dead_letter,
    record_audit_write_failure,
    record_audit_write_retry,
    record_workflow_completed,
    record_workflow_started,
)
from ..observability.tracing import get_current_trace_id
from .firebase_admin import get_firestore_client

WORKFLOW_RUNS_COLLECTION = "workflow_runs"
USAGE_EVENTS_COLLECTION = "usage_events"
DEFAULT_AUDIT_WRITE_RETRY_ATTEMPTS = 1
DEFAULT_AUDIT_WRITE_RETRY_DELAY_SECONDS = 0.05
DEFAULT_AUDIT_DEAD_LETTER_LIMIT = 100

_AUDIT_DEAD_LETTERS: list[Dict[str, Any]] = []
_AUDIT_DEAD_LETTER_LOCK = RLock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_non_negative_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        logging.warning("Invalid %s=%s. Falling back to %s.", name, raw_value, default)
        return default
    return max(0, parsed)


def _parse_non_negative_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        parsed = float(raw_value)
    except ValueError:
        logging.warning("Invalid %s=%s. Falling back to %s.", name, raw_value, default)
        return default
    return max(0.0, parsed)


def _audit_write_retry_attempts() -> int:
    return _parse_non_negative_int_env("AUDIT_WRITE_RETRY_ATTEMPTS", DEFAULT_AUDIT_WRITE_RETRY_ATTEMPTS)


def _audit_write_retry_delay_seconds() -> float:
    return _parse_non_negative_float_env("AUDIT_WRITE_RETRY_DELAY_SECONDS", DEFAULT_AUDIT_WRITE_RETRY_DELAY_SECONDS)


def _audit_dead_letter_limit() -> int:
    return _parse_non_negative_int_env("AUDIT_DEAD_LETTER_LIMIT", DEFAULT_AUDIT_DEAD_LETTER_LIMIT)


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


def _payload_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(_serialize_value(payload), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dead_letter_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "payload_hash": _payload_hash(payload),
        "event_id": payload.get("event_id"),
        "run_id": payload.get("run_id"),
        "request_id": payload.get("request_id"),
        "workflow_run_id": payload.get("workflow_run_id"),
        "actor_user_id": payload.get("actor_user_id"),
        "trace_id": payload.get("trace_id"),
        "payload_operation": payload.get("operation") or metadata.get("operation"),
        "status": payload.get("status"),
    }


def _record_dead_letter(
    *,
    collection_name: str,
    operation: str,
    payload: Dict[str, Any],
    error: Exception | str,
    attempts: int,
) -> None:
    limit = _audit_dead_letter_limit()
    if limit <= 0:
        return

    entry = {
        "collection_name": collection_name,
        "operation": operation,
        "failed_at": _utcnow().isoformat(),
        "attempts": attempts,
        "error_message": str(error),
        "payload": _dead_letter_summary(payload),
    }
    with _AUDIT_DEAD_LETTER_LOCK:
        _AUDIT_DEAD_LETTERS.append(entry)
        overflow = len(_AUDIT_DEAD_LETTERS) - limit
        if overflow > 0:
            del _AUDIT_DEAD_LETTERS[:overflow]
    record_audit_dead_letter(collection=collection_name, operation=operation)
    logging.error(
        "Audit write moved to local dead-letter buffer",
        extra={
            "event": "audit.write.dead_lettered",
            "collection_name": collection_name,
            "operation": operation,
            "attempts": attempts,
            "payload_hash": entry["payload"]["payload_hash"],
        },
    )


def get_audit_dead_letters() -> list[Dict[str, Any]]:
    with _AUDIT_DEAD_LETTER_LOCK:
        return [dict(item) for item in _AUDIT_DEAD_LETTERS]


def clear_audit_dead_letters() -> None:
    with _AUDIT_DEAD_LETTER_LOCK:
        _AUDIT_DEAD_LETTERS.clear()


def build_actor_snapshot(user: Optional[AuthUser]) -> Dict[str, Any]:
    if user is None:
        return {}

    return {
        "user_id": user.sub,
        "email": user.email,
        "name": user.name,
        "provider": user.provider,
        "email_verified": user.email_verified,
        "organization_domain": user.organization_domain,
        "tenant_id": user.tenant_id,
        "roles": list(user.roles or []),
        "is_org_admin": user.is_org_admin,
    }


def _attach_trace_id(payload: Dict[str, Any]) -> Dict[str, Any]:
    trace_id = get_current_trace_id()
    if trace_id:
        payload["trace_id"] = trace_id
    return payload


def _get_collection(collection_name: str):
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime config
        logging.warning("Firestore client unavailable for %s writes: %s", collection_name, exc)
        return None

    return client.collection(collection_name)


def _write_with_retries(*, write, payload: Dict[str, Any], collection_name: str, operation: str) -> None:
    max_attempts = _audit_write_retry_attempts() + 1
    retry_delay = _audit_write_retry_delay_seconds()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            write()
            if attempt > 1:
                record_audit_write_retry(collection=collection_name, operation=operation, outcome="success")
            return
        except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
            last_error = exc
            if attempt < max_attempts:
                record_audit_write_retry(collection=collection_name, operation=operation, outcome="scheduled")
                logging.warning(
                    "Firestore %s write attempt %s/%s failed; retrying: %s",
                    operation,
                    attempt,
                    max_attempts,
                    exc,
                )
                if retry_delay > 0:
                    time.sleep(retry_delay)
                continue

    if last_error is not None:
        record_audit_write_failure(collection=collection_name, operation=operation)
        _record_dead_letter(
            collection_name=collection_name,
            operation=operation,
            payload=payload,
            error=last_error,
            attempts=max_attempts,
        )
        logging.warning("Firestore %s skipped because write failed: %s", operation, last_error)


def _safe_set(document_ref, payload: Dict[str, Any], *, collection_name: str, operation: str) -> None:
    _write_with_retries(
        write=lambda: document_ref.set(payload),
        payload=payload,
        collection_name=collection_name,
        operation=operation,
    )


def _safe_update(document_ref, payload: Dict[str, Any], *, collection_name: str, operation: str) -> None:
    _write_with_retries(
        write=lambda: document_ref.update(payload),
        payload=payload,
        collection_name=collection_name,
        operation=operation,
    )


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
    record_workflow_started(run_id, operation, status=status)
    payload = _attach_trace_id(
        {
            "run_id": run_id,
            "operation": operation,
            "status": status,
            "request_id": request_id,
            "workspace_id": workspace_id,
            "tenant_id": actor.tenant_id if actor else None,
            "organization_domain": actor.organization_domain if actor else None,
            "actor": build_actor_snapshot(actor),
            "actor_user_id": actor.sub if actor else None,
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "metadata": _serialize_value(metadata or {}),
        }
    )
    collection = _get_collection(WORKFLOW_RUNS_COLLECTION)

    if collection is not None:
        _safe_set(
            collection.document(run_id),
            payload,
            collection_name=WORKFLOW_RUNS_COLLECTION,
            operation="workflow_run_start",
        )
    else:
        record_audit_write_failure(collection=WORKFLOW_RUNS_COLLECTION, operation="workflow_run_start")
        _record_dead_letter(
            collection_name=WORKFLOW_RUNS_COLLECTION,
            operation="workflow_run_start",
            payload=payload,
            error="collection_unavailable",
            attempts=0,
        )

    return run_id


def complete_workflow_run(
    run_id: str,
    *,
    status: str,
    metadata: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    record_workflow_completed(run_id, status)
    payload = _attach_trace_id(
        {
            "status": status,
            "completed_at": _utcnow(),
            "updated_at": _utcnow(),
            "error_message": error_message,
            "result": _serialize_value(metadata or {}),
        }
    )
    collection = _get_collection(WORKFLOW_RUNS_COLLECTION)
    if collection is None:
        record_audit_write_failure(collection=WORKFLOW_RUNS_COLLECTION, operation="workflow_run_complete")
        _record_dead_letter(
            collection_name=WORKFLOW_RUNS_COLLECTION,
            operation="workflow_run_complete",
            payload=payload,
            error="collection_unavailable",
            attempts=0,
        )
        return
    _safe_update(
        collection.document(run_id),
        payload,
        collection_name=WORKFLOW_RUNS_COLLECTION,
        operation="workflow_run_complete",
    )


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
    payload = _attach_trace_id(
        {
            "event_id": event_id,
            "event_type": event_type,
            "billing_key": billing_key,
            "quantity": int(quantity),
            "unit": unit,
            "occurred_at": _utcnow(),
            "tenant_id": actor.tenant_id if actor else None,
            "organization_domain": actor.organization_domain if actor else None,
            "actor": build_actor_snapshot(actor),
            "actor_user_id": actor.sub if actor else None,
            "workspace_id": workspace_id,
            "workflow_run_id": workflow_run_id,
            "request_id": request_id,
            "status": status,
            "metadata": _serialize_value(metadata or {}),
        }
    )
    collection = _get_collection(USAGE_EVENTS_COLLECTION)

    if collection is not None:
        _safe_set(
            collection.document(event_id),
            payload,
            collection_name=USAGE_EVENTS_COLLECTION,
            operation="usage_event_record",
        )
    else:
        record_audit_write_failure(collection=USAGE_EVENTS_COLLECTION, operation="usage_event_record")
        _record_dead_letter(
            collection_name=USAGE_EVENTS_COLLECTION,
            operation="usage_event_record",
            payload=payload,
            error="collection_unavailable",
            attempts=0,
        )

    return event_id
