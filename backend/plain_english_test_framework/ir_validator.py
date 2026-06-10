"""Deterministic validator for the JSON intermediate representation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from plain_english_test_framework.validation import (
    ValidationIssue,
    deduplicate_issues,
    default_schema_path,
    find_secret_issues,
    format_schema_path,
)


DEFAULT_IR_SCHEMA_PATH = default_schema_path("ir.schema.json")


class IrValidationError(Exception):
    """Raised when an IR document cannot be parsed or validated."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class ValidatedIr:
    """Schema-valid IR document ready for later generator stages."""

    schema_version: str
    spec_id: str
    case_count: int
    source_path: Path | None
    raw: Mapping[str, Any]


def parse_ir_file(path: str | Path, *, schema_path: str | Path = DEFAULT_IR_SCHEMA_PATH) -> ValidatedIr:
    """Parse and validate a JSON IR file."""

    ir_path = Path(path)
    try:
        document = json.loads(ir_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IrValidationError((ValidationIssue("$", f"invalid JSON: {exc}", "json.parse"),)) from exc
    return parse_ir_document(document, source_path=ir_path, schema_path=schema_path)


def parse_ir_document(
    document: Any,
    *,
    source_path: str | Path | None = None,
    schema_path: str | Path = DEFAULT_IR_SCHEMA_PATH,
) -> ValidatedIr:
    """Validate an already-loaded JSON IR document."""

    issues = validate_ir_document(document, schema_path=schema_path)
    if issues:
        raise IrValidationError(issues)

    source = Path(source_path) if source_path is not None else None
    return ValidatedIr(
        schema_version=document["schemaVersion"],
        spec_id=document["spec"]["id"],
        case_count=len(document["cases"]),
        source_path=source,
        raw=document,
    )


def validate_ir_document(document: Any, *, schema_path: str | Path = DEFAULT_IR_SCHEMA_PATH) -> tuple[ValidationIssue, ...]:
    """Validate a JSON-like IR document and return all known issues."""

    issues: list[ValidationIssue] = []
    if not isinstance(document, dict):
        return (ValidationIssue("$", "IR document must be an object", "ir.type"),)

    issues.extend(_validate_schema(document, Path(schema_path)))
    issues.extend(_validate_sensitive_values(document))
    return tuple(deduplicate_issues(issues))


def _validate_schema(document: Mapping[str, Any], schema_path: Path) -> list[ValidationIssue]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    issues: list[ValidationIssue] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
        issues.append(ValidationIssue(format_schema_path(error.path), error.message, f"schema.{error.validator}"))
    return issues


def _validate_sensitive_values(document: Mapping[str, Any]) -> list[ValidationIssue]:
    return find_secret_issues(document, message="raw secret-looking value is not allowed in IR", code="secret.raw_value")
