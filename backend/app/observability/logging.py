import json
import logging
import os
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Dict


_LOG_CONTEXT: ContextVar[Dict[str, Any]] = ContextVar("tcg_log_context", default={})
_CONFIGURED = False


_RESERVED_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _serialize_log_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_log_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_log_value(item) for item in value]
    return str(value)


def bind_log_context(**fields: Any) -> Token[Dict[str, Any]]:
    """Bind request/workflow fields to logs emitted in the current context."""
    current = dict(_LOG_CONTEXT.get())
    current.update({key: value for key, value in fields.items() if value is not None})
    return _LOG_CONTEXT.set(current)


def reset_log_context(token: Token[Dict[str, Any]]) -> None:
    _LOG_CONTEXT.reset(token)


def get_log_context() -> Dict[str, Any]:
    return dict(_LOG_CONTEXT.get())


class JsonLogFormatter(logging.Formatter):
    """Format log records as JSON while preserving safe structured extras."""

    def __init__(self) -> None:
        super().__init__()
        self.service_name = os.getenv("SERVICE_NAME", "agentic-test-case-generator-api")
        self.service_version = os.getenv("SERVICE_VERSION", "dev")
        self.environment = os.getenv("ENVIRONMENT", "local")

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service_name": self.service_name,
            "service_version": self.service_version,
            "environment": self.environment,
        }

        payload.update({key: _serialize_log_value(value) for key, value in get_log_context().items()})

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_FIELDS or key.startswith("_") or key in payload:
                continue
            payload[key] = _serialize_log_value(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(*, force: bool = False) -> None:
    """Configure application logging once, using JSON by default."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = os.getenv("LOG_FORMAT", "json").strip().lower()

    handler = logging.StreamHandler()
    handler.setLevel(level)
    if log_format == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    else:
        handler.setFormatter(JsonLogFormatter())
    setattr(handler, "_tcg_observability_handler", True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [
        existing_handler
        for existing_handler in root_logger.handlers
        if not getattr(existing_handler, "_tcg_observability_handler", False)
    ]
    root_logger.addHandler(handler)
    _CONFIGURED = True
