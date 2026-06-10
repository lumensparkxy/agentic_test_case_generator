"""Shared validation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Sequence


SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-]+=*", re.IGNORECASE),
    re.compile(r"\b(password|passwd|api[_-]?key|secret|token|cookie|session)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
)


@dataclass(frozen=True)
class ValidationIssue:
    """One deterministic validation problem."""

    path: str
    message: str
    code: str


def format_schema_path(path: Sequence[Any]) -> str:
    """Render a jsonschema error path as a compact JSON-path-like string."""

    if not path:
        return "$"
    formatted = "$"
    for part in path:
        formatted += f"[{part}]" if isinstance(part, int) else f".{part}"
    return formatted


def deduplicate_issues(issues: Sequence[ValidationIssue]) -> list[ValidationIssue]:
    """Preserve issue order while removing exact duplicates."""

    seen: set[ValidationIssue] = set()
    unique: list[ValidationIssue] = []
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
    return unique


def walk_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """Return every string value in a nested JSON-like document."""

    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        result: list[tuple[str, str]] = []
        for index, item in enumerate(value):
            result.extend(walk_strings(item, f"{path}[{index}]"))
        return result
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            result.extend(walk_strings(item, f"{path}.{key}"))
        return result
    return []


def find_secret_issues(value: Any, *, message: str, code: str) -> list[ValidationIssue]:
    """Find raw secret-looking string values in a JSON-like document."""

    issues: list[ValidationIssue] = []
    for path, string_value in walk_strings(value):
        for pattern in SENSITIVE_VALUE_PATTERNS:
            if pattern.search(string_value):
                issues.append(ValidationIssue(path, message, code))
                break
    return issues


def default_schema_path(filename: str) -> Path:
    """Resolve a schema file from a repo checkout or source tree."""

    cwd_candidate = Path.cwd() / "schemas" / filename
    if cwd_candidate.exists():
        return cwd_candidate

    source_candidate = Path(__file__).resolve().parents[2] / "schemas" / filename
    if source_candidate.exists():
        return source_candidate

    return cwd_candidate
