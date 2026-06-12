from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from .metrics import record_integration_request


logger = logging.getLogger(__name__)


def _safe_label(value: object, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(".", "_")
    cleaned = "".join(character if character.isalnum() or character == "_" else "_" for character in normalized)
    collapsed = "_".join(part for part in cleaned.split("_") if part)
    return collapsed or fallback


@contextmanager
def observe_integration_request(*, provider: str, operation: str) -> Iterator[None]:
    safe_provider = _safe_label(provider, fallback="unknown")
    safe_operation = _safe_label(operation, fallback="request")
    started_at = time.perf_counter()

    try:
        yield
    except Exception as exc:
        duration_seconds = time.perf_counter() - started_at
        status_code = getattr(exc, "status_code", None)
        record_integration_request(
            provider=safe_provider,
            operation=safe_operation,
            status="failure",
            duration_seconds=duration_seconds,
        )
        logger.warning(
            "Integration request failed",
            extra={
                "event": "integration.request.failed",
                "provider": safe_provider,
                "integration_operation": safe_operation,
                "integration_status": "failure",
                "status_code": status_code,
                "duration_ms": round(duration_seconds * 1000, 2),
                "error_type": type(exc).__name__,
            },
        )
        raise

    duration_seconds = time.perf_counter() - started_at
    record_integration_request(
        provider=safe_provider,
        operation=safe_operation,
        status="success",
        duration_seconds=duration_seconds,
    )
    logger.info(
        "Integration request completed",
        extra={
            "event": "integration.request.completed",
            "provider": safe_provider,
            "integration_operation": safe_operation,
            "integration_status": "success",
            "duration_ms": round(duration_seconds * 1000, 2),
        },
    )
