import time
from threading import RLock
from typing import Dict, Iterable, Tuple


LabelSet = Tuple[Tuple[str, str], ...]

_COUNTERS: Dict[Tuple[str, LabelSet], float] = {}
_SUMMARIES: Dict[Tuple[str, LabelSet], Dict[str, float]] = {}
_WORKFLOW_STARTS: Dict[str, Tuple[str, float]] = {}
_LOCK = RLock()

_METRIC_META = {
    "http_requests_total": ("counter", "Total HTTP requests processed."),
    "http_request_duration_seconds": ("summary", "HTTP request duration in seconds."),
    "workflow_runs_total": ("counter", "Workflow runs recorded by operation and status."),
    "workflow_duration_seconds": ("summary", "Workflow duration in seconds."),
    "agent_fallbacks_total": ("counter", "Agent workflow fallbacks by workflow and reason."),
    "audit_write_failures_total": ("counter", "Audit persistence write failures by collection and operation."),
    "audit_write_retries_total": ("counter", "Audit persistence retry attempts by collection, operation, and outcome."),
    "audit_dead_letters_total": ("counter", "Audit persistence failures recorded to the local dead-letter buffer."),
}


def _labelset(labels: Dict[str, object]) -> LabelSet:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items() if value is not None))


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: LabelSet) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in labels) + "}"


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.12g}"


def increment_counter(name: str, labels: Dict[str, object] | None = None, amount: float = 1.0) -> None:
    labelset = _labelset(labels or {})
    with _LOCK:
        _COUNTERS[(name, labelset)] = _COUNTERS.get((name, labelset), 0.0) + amount


def observe_summary(name: str, value: float, labels: Dict[str, object] | None = None) -> None:
    labelset = _labelset(labels or {})
    with _LOCK:
        summary = _SUMMARIES.setdefault((name, labelset), {"count": 0.0, "sum": 0.0})
        summary["count"] += 1.0
        summary["sum"] += max(0.0, float(value))


def record_http_request(*, method: str, path: str, status_code: int, duration_seconds: float) -> None:
    labels = {"method": method, "path": path, "status_code": status_code}
    increment_counter("http_requests_total", labels)
    observe_summary("http_request_duration_seconds", duration_seconds, labels)


def record_workflow_started(run_id: str, operation: str, status: str = "started") -> None:
    with _LOCK:
        _WORKFLOW_STARTS[run_id] = (operation, time.perf_counter())
    increment_counter("workflow_runs_total", {"operation": operation, "status": status})


def record_workflow_completed(run_id: str, status: str) -> None:
    with _LOCK:
        workflow_start = _WORKFLOW_STARTS.pop(run_id, None)
    operation = workflow_start[0] if workflow_start else "unknown"
    duration_seconds = time.perf_counter() - workflow_start[1] if workflow_start else 0.0
    labels = {"operation": operation, "status": status}
    increment_counter("workflow_runs_total", labels)
    observe_summary("workflow_duration_seconds", duration_seconds, labels)


def record_agent_fallback(*, workflow: str, reason: str) -> None:
    increment_counter("agent_fallbacks_total", {"workflow": workflow, "reason": reason})


def record_audit_write_failure(*, collection: str, operation: str) -> None:
    increment_counter("audit_write_failures_total", {"collection": collection, "operation": operation})


def record_audit_write_retry(*, collection: str, operation: str, outcome: str) -> None:
    increment_counter("audit_write_retries_total", {"collection": collection, "operation": operation, "outcome": outcome})


def record_audit_dead_letter(*, collection: str, operation: str) -> None:
    increment_counter("audit_dead_letters_total", {"collection": collection, "operation": operation})


def _iter_metric_names() -> Iterable[str]:
    names = set(_METRIC_META)
    names.update(name for name, _labels in _COUNTERS.keys())
    names.update(name for name, _labels in _SUMMARIES.keys())
    return sorted(names)


def render_prometheus_metrics() -> str:
    lines: list[str] = []
    with _LOCK:
        counters = dict(_COUNTERS)
        summaries = {key: dict(value) for key, value in _SUMMARIES.items()}

    for name in _iter_metric_names():
        metric_type, help_text = _METRIC_META.get(name, ("gauge", f"Metric {name}."))
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")

        for (counter_name, labels), value in sorted(counters.items()):
            if counter_name == name:
                lines.append(f"{name}{_format_labels(labels)} {_format_number(value)}")

        for (summary_name, labels), value in sorted(summaries.items()):
            if summary_name == name:
                formatted_labels = _format_labels(labels)
                lines.append(f"{name}_count{formatted_labels} {_format_number(value['count'])}")
                lines.append(f"{name}_sum{formatted_labels} {_format_number(value['sum'])}")

    lines.append("# EOF")
    return "\n".join(lines) + "\n"


def reset_metrics() -> None:
    """Clear in-memory metrics. Intended for tests."""
    with _LOCK:
        _COUNTERS.clear()
        _SUMMARIES.clear()
        _WORKFLOW_STARTS.clear()
