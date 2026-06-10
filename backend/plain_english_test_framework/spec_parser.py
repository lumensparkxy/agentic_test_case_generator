"""Deterministic parser and validator for structured plain-English specs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
import yaml

from plain_english_test_framework.validation import (
    ValidationIssue,
    deduplicate_issues,
    default_schema_path,
    find_secret_issues,
    format_schema_path,
)


DEFAULT_SCHEMA_PATH = default_schema_path("spec.schema.json")

PLACEHOLDER_PATTERN = re.compile(
    r"\{(?:env|data)\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\}"
)
BRACED_PATTERN = re.compile(r"\{[^{}\s]*(?:\.[^{}\s]*)*\}")
MARKDOWN_YAML_BLOCK_PATTERN = re.compile(r"(?ms)^```ya?ml\s*\n(.*?)\n```")
STEP_KEYWORD_PATTERN = re.compile(r"^(Given|When|Then|And|But)\s+")


class SpecValidationError(Exception):
    """Raised when a spec cannot be parsed or validated."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class ParsedSpec:
    """Validated structured spec ready for later compiler stages."""

    schema_version: str
    id: str
    title: str
    description: str | None
    tags: tuple[str, ...]
    data_set: str | None
    steps: tuple[str, ...]
    source_path: Path | None
    raw: Mapping[str, Any]


def parse_spec_file(path: str | Path, *, schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> ParsedSpec:
    """Parse and validate a YAML or Markdown spec file."""

    spec_path = Path(path)
    return parse_spec_text(spec_path.read_text(encoding="utf-8"), source_path=spec_path, schema_path=schema_path)


def parse_spec_text(
    text: str,
    *,
    source_path: str | Path | None = None,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> ParsedSpec:
    """Parse and validate a spec from YAML text or a Markdown wrapper."""

    source = Path(source_path) if source_path is not None else None
    yaml_text = _extract_yaml_payload(text, source)
    document = _load_yaml_document(yaml_text, source)
    issues = validate_spec_document(document, schema_path=schema_path)
    if issues:
        raise SpecValidationError(issues)
    return _to_parsed_spec(document, source)


def validate_spec_document(document: Any, *, schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> tuple[ValidationIssue, ...]:
    """Validate a loaded spec document and return all known issues."""

    issues: list[ValidationIssue] = []
    if not isinstance(document, dict):
        return (ValidationIssue("$", "spec document must be a mapping/object", "spec.type"),)

    issues.extend(_validate_schema(document, Path(schema_path)))
    issues.extend(_validate_steps(document))
    issues.extend(_validate_placeholders(document))
    issues.extend(_validate_sensitive_values(document))
    return tuple(deduplicate_issues(issues))


def _extract_yaml_payload(text: str, source_path: Path | None) -> str:
    suffix = source_path.suffix.lower() if source_path else ""
    if suffix not in {".md", ".markdown"}:
        return text

    blocks = MARKDOWN_YAML_BLOCK_PATTERN.findall(text)
    if len(blocks) != 1:
        count = "no" if not blocks else "multiple"
        raise SpecValidationError(
            (ValidationIssue("$", f"Markdown spec must contain exactly one fenced YAML block; found {count}", "markdown.yaml_block"),)
        )
    return blocks[0]


def _load_yaml_document(text: str, source_path: Path | None) -> Any:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecValidationError((ValidationIssue("$", f"invalid YAML: {exc}", "yaml.parse"),)) from exc

    if document is None:
        raise SpecValidationError((ValidationIssue("$", "spec document must not be empty", "yaml.empty"),))
    return document


def _validate_schema(document: Mapping[str, Any], schema_path: Path) -> list[ValidationIssue]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    issues: list[ValidationIssue] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
        issues.append(ValidationIssue(format_schema_path(error.path), error.message, f"schema.{error.validator}"))
    return issues


def _validate_steps(document: Mapping[str, Any]) -> list[ValidationIssue]:
    steps = document.get("steps")
    if not isinstance(steps, list) or not steps:
        return []

    issues: list[ValidationIssue] = []
    first_keyword = _step_keyword(steps[0]) if isinstance(steps[0], str) else None
    if first_keyword != "Given":
        issues.append(ValidationIssue("$.steps[0]", 'first step must start with "Given"', "steps.first_given"))

    for index, step in enumerate(steps):
        if not isinstance(step, str):
            continue
        keyword = _step_keyword(step)
        if keyword in {"And", "But"} and index == 0:
            issues.append(ValidationIssue(f"$.steps[{index}]", f'"{keyword}" cannot be the first step', "steps.leading_conjunction"))
        if keyword == "Then":
            break

    return issues


def _validate_placeholders(document: Mapping[str, Any]) -> list[ValidationIssue]:
    steps = document.get("steps")
    if not isinstance(steps, list):
        return []

    issues: list[ValidationIssue] = []
    uses_data_placeholder = False
    for index, step in enumerate(steps):
        if not isinstance(step, str):
            continue

        if step.count("{") != step.count("}"):
            issues.append(ValidationIssue(f"$.steps[{index}]", "placeholder braces are not balanced", "placeholder.unbalanced"))

        for braced in BRACED_PATTERN.findall(step):
            if not PLACEHOLDER_PATTERN.fullmatch(braced):
                issues.append(
                    ValidationIssue(
                        f"$.steps[{index}]",
                        f"unsupported placeholder {braced}; use {{env.*}} or {{data.*}}",
                        "placeholder.unsupported",
                    )
                )

        if "{data." in step:
            uses_data_placeholder = True

    if uses_data_placeholder and not document.get("dataSet"):
        issues.append(ValidationIssue("$.dataSet", "dataSet is required when steps use {data.*} placeholders", "data_set.required"))

    return issues


def _validate_sensitive_values(document: Mapping[str, Any]) -> list[ValidationIssue]:
    return find_secret_issues(document, message="raw secret-looking value is not allowed in specs", code="secret.raw_value")


def _to_parsed_spec(document: Mapping[str, Any], source_path: Path | None) -> ParsedSpec:
    return ParsedSpec(
        schema_version=document["schemaVersion"],
        id=document["id"],
        title=document["title"],
        description=document.get("description"),
        tags=tuple(document.get("tags", ())),
        data_set=document.get("dataSet"),
        steps=tuple(document["steps"]),
        source_path=source_path,
        raw=document,
    )


def _step_keyword(step: str) -> str | None:
    match = STEP_KEYWORD_PATTERN.match(step)
    return match.group(1) if match else None
