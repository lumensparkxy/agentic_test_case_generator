"""Repository boundary for workflow audit and usage event persistence."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Protocol

from ..observability.metrics import record_audit_write_failure, record_audit_write_retry
from .firestore_repository import get_optional_firestore_collection

DEFAULT_AUDIT_WRITE_RETRY_ATTEMPTS = 1
DEFAULT_AUDIT_WRITE_RETRY_DELAY_SECONDS = 0.05
DEFAULT_AUDIT_DEAD_LETTER_COLLECTION = "audit_dead_letters"


@dataclass(frozen=True)
class AuditWriteFailure:
    collection_name: str
    operation: str
    payload: Dict[str, Any]
    error: Exception | str
    attempts: int


class AuditRepository(Protocol):
    def record_workflow_run_start(self, run_id: str, payload: Dict[str, Any]) -> AuditWriteFailure | None: ...

    def record_workflow_run_complete(self, run_id: str, payload: Dict[str, Any]) -> AuditWriteFailure | None: ...

    def record_usage_event(self, event_id: str, payload: Dict[str, Any]) -> AuditWriteFailure | None: ...


class AuditDeadLetterSink(Protocol):
    backend: str

    def record_dead_letter(self, dead_letter_id: str, payload: Dict[str, Any]) -> None: ...


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


def _audit_dead_letter_collection_name() -> str:
    configured = os.getenv("AUDIT_DEAD_LETTER_COLLECTION", DEFAULT_AUDIT_DEAD_LETTER_COLLECTION).strip()
    return configured or DEFAULT_AUDIT_DEAD_LETTER_COLLECTION


class FirestoreAuditDeadLetterSink:
    backend = "firestore"

    def __init__(self, *, collection_name: str | None = None) -> None:
        self.collection_name = collection_name or _audit_dead_letter_collection_name()

    def record_dead_letter(self, dead_letter_id: str, payload: Dict[str, Any]) -> None:
        collection = get_optional_firestore_collection(
            self.collection_name,
            unavailable_message=f"Firestore client unavailable for {self.collection_name} audit dead-letter writes",
        )
        if collection is None:
            raise RuntimeError("collection_unavailable")

        collection.document(dead_letter_id).set(payload)


def build_audit_dead_letter_sink_from_env() -> AuditDeadLetterSink | None:
    backend = os.getenv("AUDIT_DEAD_LETTER_BACKEND", "local").strip().lower()
    if backend in {"", "local", "memory", "none", "disabled"}:
        return None
    if backend == "firestore":
        return FirestoreAuditDeadLetterSink()

    logging.warning(
        "Invalid AUDIT_DEAD_LETTER_BACKEND=%s. Falling back to local dead-letter buffer only.",
        backend,
    )
    return None


class FirestoreAuditRepository:
    def __init__(self, *, workflow_runs_collection: str, usage_events_collection: str) -> None:
        self.workflow_runs_collection = workflow_runs_collection
        self.usage_events_collection = usage_events_collection

    def record_workflow_run_start(self, run_id: str, payload: Dict[str, Any]) -> AuditWriteFailure | None:
        collection = self._get_collection(self.workflow_runs_collection)
        if collection is None:
            return self._collection_unavailable(
                collection_name=self.workflow_runs_collection,
                operation="workflow_run_start",
                payload=payload,
            )
        document = collection.document(run_id)
        return self._write_with_retries(
            write=lambda: document.set(payload),
            payload=payload,
            collection_name=self.workflow_runs_collection,
            operation="workflow_run_start",
        )

    def record_workflow_run_complete(self, run_id: str, payload: Dict[str, Any]) -> AuditWriteFailure | None:
        collection = self._get_collection(self.workflow_runs_collection)
        if collection is None:
            return self._collection_unavailable(
                collection_name=self.workflow_runs_collection,
                operation="workflow_run_complete",
                payload=payload,
            )
        document = collection.document(run_id)
        return self._write_with_retries(
            write=lambda: document.update(payload),
            payload=payload,
            collection_name=self.workflow_runs_collection,
            operation="workflow_run_complete",
        )

    def record_usage_event(self, event_id: str, payload: Dict[str, Any]) -> AuditWriteFailure | None:
        collection = self._get_collection(self.usage_events_collection)
        if collection is None:
            return self._collection_unavailable(
                collection_name=self.usage_events_collection,
                operation="usage_event_record",
                payload=payload,
            )
        document = collection.document(event_id)
        return self._write_with_retries(
            write=lambda: document.set(payload),
            payload=payload,
            collection_name=self.usage_events_collection,
            operation="usage_event_record",
        )

    @staticmethod
    def _get_collection(collection_name: str):
        return get_optional_firestore_collection(
            collection_name,
            unavailable_message=f"Firestore client unavailable for {collection_name} writes",
        )

    @staticmethod
    def _collection_unavailable(
        *,
        collection_name: str,
        operation: str,
        payload: Dict[str, Any],
    ) -> AuditWriteFailure:
        record_audit_write_failure(collection=collection_name, operation=operation)
        return AuditWriteFailure(
            collection_name=collection_name,
            operation=operation,
            payload=payload,
            error="collection_unavailable",
            attempts=0,
        )

    @staticmethod
    def _write_with_retries(
        *,
        write,
        payload: Dict[str, Any],
        collection_name: str,
        operation: str,
    ) -> AuditWriteFailure | None:
        max_attempts = _audit_write_retry_attempts() + 1
        retry_delay = _audit_write_retry_delay_seconds()
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                write()
                if attempt > 1:
                    record_audit_write_retry(collection=collection_name, operation=operation, outcome="success")
                return None
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
            logging.warning("Firestore %s skipped because write failed: %s", operation, last_error)
            return AuditWriteFailure(
                collection_name=collection_name,
                operation=operation,
                payload=payload,
                error=last_error,
                attempts=max_attempts,
            )

        return None
