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
    record_audit_dead_letter_sink_write,
    record_workflow_completed,
    record_workflow_started,
)
from ..observability.tracing import get_current_trace_id
from .audit_repository import (
    AuditDeadLetterSink,
    AuditRepository,
    AuditWriteFailure,
    FirestoreAuditRepository,
    build_audit_dead_letter_sink_from_env,
)

WORKFLOW_RUNS_COLLECTION = "workflow_runs"
USAGE_EVENTS_COLLECTION = "usage_events"
DEFAULT_AUDIT_DEAD_LETTER_LIMIT = 100

_AUDIT_DEAD_LETTERS: list[Dict[str, Any]] = []
_AUDIT_DEAD_LETTER_LOCK = RLock()
_AUDIT_REPOSITORY: AuditRepository = FirestoreAuditRepository(
    workflow_runs_collection=WORKFLOW_RUNS_COLLECTION,
    usage_events_collection=USAGE_EVENTS_COLLECTION,
)
_AUDIT_DEAD_LETTER_SINK: AuditDeadLetterSink | None = build_audit_dead_letter_sink_from_env()


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


def _safe_error_type(error: Exception | str) -> str:
    if isinstance(error, Exception):
        return error.__class__.__name__

    value = str(error).strip()
    if value and all(character.isalnum() or character in "_.:-" for character in value) and len(value) <= 80:
        return value
    return "error"


def _record_durable_dead_letter(entry: Dict[str, Any]) -> None:
    sink = get_audit_dead_letter_sink()
    if sink is None:
        return

    started_at = time.perf_counter()
    try:
        sink.record_dead_letter(entry["dead_letter_id"], entry)
    except Exception as exc:  # pragma: no cover - defensive around external sinks
        duration_seconds = time.perf_counter() - started_at
        record_audit_dead_letter_sink_write(
            backend=sink.backend,
            collection=entry["collection_name"],
            operation=entry["operation"],
            status="failure",
            duration_seconds=duration_seconds,
        )
        logging.error(
            "Audit dead-letter durable sink write failed",
            extra={
                "event": "audit.dead_letter_sink.write_failed",
                "sink_backend": sink.backend,
                "collection_name": entry["collection_name"],
                "operation": entry["operation"],
                "payload_hash": entry["payload"]["payload_hash"],
                "error_type": _safe_error_type(exc),
                "duration_ms": round(duration_seconds * 1000, 3),
            },
        )
        return

    duration_seconds = time.perf_counter() - started_at
    record_audit_dead_letter_sink_write(
        backend=sink.backend,
        collection=entry["collection_name"],
        operation=entry["operation"],
        status="success",
        duration_seconds=duration_seconds,
    )
    logging.info(
        "Audit dead-letter durable sink write completed",
        extra={
            "event": "audit.dead_letter_sink.write_completed",
            "sink_backend": sink.backend,
            "collection_name": entry["collection_name"],
            "operation": entry["operation"],
            "payload_hash": entry["payload"]["payload_hash"],
            "duration_ms": round(duration_seconds * 1000, 3),
        },
    )


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
        "dead_letter_id": str(uuid4()),
        "collection_name": collection_name,
        "operation": operation,
        "failed_at": _utcnow().isoformat(),
        "attempts": attempts,
        "error_type": _safe_error_type(error),
        "payload": _dead_letter_summary(payload),
    }
    with _AUDIT_DEAD_LETTER_LOCK:
        _AUDIT_DEAD_LETTERS.append(entry)
        overflow = len(_AUDIT_DEAD_LETTERS) - limit
        if overflow > 0:
            del _AUDIT_DEAD_LETTERS[:overflow]
    record_audit_dead_letter(collection=collection_name, operation=operation)
    _record_durable_dead_letter(entry)
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


def get_audit_repository() -> AuditRepository:
    return _AUDIT_REPOSITORY


def get_audit_dead_letter_sink() -> AuditDeadLetterSink | None:
    return _AUDIT_DEAD_LETTER_SINK


def set_audit_repository_for_testing(repository: AuditRepository) -> None:
    global _AUDIT_REPOSITORY
    _AUDIT_REPOSITORY = repository


def set_audit_dead_letter_sink_for_testing(sink: AuditDeadLetterSink | None) -> None:
    global _AUDIT_DEAD_LETTER_SINK
    _AUDIT_DEAD_LETTER_SINK = sink


def reset_audit_repository_for_testing() -> None:
    set_audit_repository_for_testing(
        FirestoreAuditRepository(
            workflow_runs_collection=WORKFLOW_RUNS_COLLECTION,
            usage_events_collection=USAGE_EVENTS_COLLECTION,
        )
    )


def reset_audit_dead_letter_sink_for_testing() -> None:
    set_audit_dead_letter_sink_for_testing(build_audit_dead_letter_sink_from_env())


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


def _record_repository_failure(failure: AuditWriteFailure | None) -> None:
    if failure is None:
        return
    _record_dead_letter(
        collection_name=failure.collection_name,
        operation=failure.operation,
        payload=failure.payload,
        error=failure.error,
        attempts=failure.attempts,
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
    _record_repository_failure(get_audit_repository().record_workflow_run_start(run_id, payload))

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
    _record_repository_failure(get_audit_repository().record_workflow_run_complete(run_id, payload))


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
    _record_repository_failure(get_audit_repository().record_usage_event(event_id, payload))

    return event_id
