"""Deterministic environment/data resolver and spec-to-IR compiler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from plain_english_test_framework.ir_validator import ValidatedIr, parse_ir_document
from plain_english_test_framework.semantic_assertions import is_ambiguous_semantic_visible_text
from plain_english_test_framework.spec_parser import ParsedSpec, parse_spec_file
from plain_english_test_framework.validation import ValidationIssue, deduplicate_issues, find_secret_issues


IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER_PATTERN = re.compile(r"\{(?P<namespace>env|data)\.(?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")
STEP_PATTERN = re.compile(r"^(Given|When|Then|And|But)\s+(?P<body>.+)$")
OPEN_PATTERN = re.compile(r'^I open "(?P<url>[^"]+)"$')
ENTER_PATTERN = re.compile(r'^I enter "(?P<value>[^"]*)" into "(?P<label>[^"]+)"$')
CLICK_LINK_PATTERN = re.compile(r'^I click link "(?P<name>[^"]+)"$')
CLICK_PATTERN = re.compile(r'^I click "(?P<name>[^"]+)"$')
ROLE_VISIBLE_PATTERN = re.compile(r'^(?P<role>heading|link|button) "(?P<text>[^"]+)" should be visible$')
LOCATOR_VISIBLE_PATTERN = re.compile(r'^(?P<kind>css|test id) "(?P<value>[^"]+)" should be visible$')
VISIBLE_PATTERN = re.compile(r'^"(?P<text>[^"]+)" should be visible$')
TEXT_EQUALS_PATTERN = re.compile(r'^"(?P<label>[^"]+)" should equal "(?P<expected>[^"]*)"$')
URL_EQUALS_PATTERN = re.compile(r'^URL should be "(?P<url>[^"]+)"$')
TITLE_EQUALS_PATTERN = re.compile(r'^page title should be "(?P<title>[^"]*)"$')
LOCATOR_CHECKED_PATTERN = re.compile(r'^(?P<kind>css|test id) "(?P<value>[^"]+)" should (?P<negation>not )?be checked$')
LOCATOR_ENABLED_PATTERN = re.compile(r'^(?P<kind>css|test id) "(?P<value>[^"]+)" should be (?P<state>enabled|disabled)$')
LOCATOR_ATTRIBUTE_EQUALS_PATTERN = re.compile(r'^(?P<kind>css|test id) "(?P<value>[^"]+)" attribute "(?P<attribute>[^"]+)" should equal "(?P<expected>[^"]*)"$')
LOCATOR_COUNT_PATTERN = re.compile(r'^(?P<kind>css|test id) "(?P<value>[^"]+)" count should be (?P<count>\d+)$')
SCALAR_TYPES = (str, int, float, bool, type(None))


class CompilerError(Exception):
    """Raised when a validated spec cannot be compiled into valid IR."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class ResolvedEnvironment:
    """Non-sensitive environment values selected for one compiler run."""

    name: str
    values: Mapping[str, Any]
    base_url: str
    auth_state_ref: str | None


@dataclass(frozen=True)
class DataRow:
    """One scalar-only data row used to produce one executable IR case."""

    id: str
    values: Mapping[str, Any]


def compile_spec_file(
    spec_path: str | Path,
    *,
    environment_path: str | Path,
    environment_name: str,
    data_dir: str | Path | None = None,
    environment_overrides: Mapping[str, Any] | None = None,
) -> ValidatedIr:
    """Compile a structured spec file into schema-valid JSON IR."""

    spec = parse_spec_file(spec_path)
    environment = resolve_environment_file(
        environment_path,
        environment_name=environment_name,
        overrides=environment_overrides,
    )
    data_rows = resolve_data_rows(spec, data_dir=data_dir)
    return compile_parsed_spec(spec, environment=environment, data_rows=data_rows)


def compile_parsed_spec(
    spec: ParsedSpec,
    *,
    environment: ResolvedEnvironment,
    data_rows: Sequence[DataRow] | None = None,
) -> ValidatedIr:
    """Compile a parsed spec and resolved fixtures into schema-valid JSON IR."""

    rows = tuple(data_rows or (DataRow(id="", values={}),))
    document = _build_ir_document(spec, environment, rows)
    issues = _validate_compiled_document(document)
    if issues:
        raise CompilerError(issues)
    return parse_ir_document(document)


def resolve_environment_file(
    path: str | Path,
    *,
    environment_name: str,
    overrides: Mapping[str, Any] | None = None,
) -> ResolvedEnvironment:
    """Resolve a flat or base/environments YAML file into one environment."""

    env_path = Path(path)
    document = _load_yaml_mapping(env_path, document_name="environment")
    issues: list[ValidationIssue] = []

    selected = _select_environment(document, environment_name, issues)
    if overrides:
        selected = _deep_merge(selected, _expand_dot_path_mapping(overrides))

    issues.extend(_validate_environment(environment_name, selected))
    if issues:
        raise CompilerError(tuple(deduplicate_issues(issues)))

    auth_state_ref = _optional_string_at(selected, "authStateRef")
    if auth_state_ref is None:
        auth_state_ref = _optional_string_at(selected, "auth.storageStatePath")

    return ResolvedEnvironment(
        name=environment_name,
        values=selected,
        base_url=selected["baseUrl"],
        auth_state_ref=auth_state_ref,
    )


def resolve_data_rows(spec: ParsedSpec, *, data_dir: str | Path | None = None) -> tuple[DataRow, ...]:
    """Resolve the data set referenced by a parsed spec into scalar data rows."""

    if spec.data_set is None:
        return (DataRow(id="", values={}),)

    if data_dir is None:
        raise CompilerError((ValidationIssue("$.dataSet", "data_dir is required for specs with dataSet", "data_set.data_dir_required"),))

    data_path = Path(data_dir) / f"{spec.data_set}.yaml"
    document = _load_yaml_mapping(data_path, document_name="data set")
    rows = document.get("rows")
    issues: list[ValidationIssue] = []
    if not isinstance(rows, list) or not rows:
        issues.append(ValidationIssue("$.rows", "data set must contain a non-empty rows list", "data.rows_required"))
    else:
        issues.extend(_validate_data_rows(rows))

    if issues:
        raise CompilerError(tuple(deduplicate_issues(issues)))

    return tuple(DataRow(id=str(row["id"]), values=dict(row)) for row in rows)


def _build_ir_document(spec: ParsedSpec, environment: ResolvedEnvironment, rows: Sequence[DataRow]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for row in rows:
        data = row.values
        compiled_steps: list[dict[str, Any]] = []
        for step_index, original_step in enumerate(spec.steps):
            compiled_steps.append(_compile_step(original_step, step_index, environment.values, data))

        _assign_unique_step_ids(compiled_steps)
        cases.append(_build_case(spec, row, compiled_steps))

    ir_environment: dict[str, Any] = {
        "name": environment.name,
        "baseUrl": environment.base_url,
    }
    if environment.auth_state_ref is not None:
        ir_environment["authStateRef"] = environment.auth_state_ref

    spec_metadata: dict[str, Any] = {
        "id": spec.id,
        "title": spec.title,
        "tags": list(spec.tags),
    }
    if spec.source_path is not None:
        spec_metadata["sourcePath"] = str(spec.source_path)

    return {
        "schemaVersion": "1.0",
        "spec": spec_metadata,
        "environment": ir_environment,
        "cases": cases,
    }


def _build_case(spec: ParsedSpec, row: DataRow, steps: list[dict[str, Any]]) -> dict[str, Any]:
    if row.id:
        case: dict[str, Any] = {
            "id": f"{spec.id}_{row.id}",
            "title": f"{spec.title} [{row.id}]",
            "dataRowId": row.id,
            "resolvedData": {key: value for key, value in row.values.items() if key != "id"},
            "steps": steps,
        }
    else:
        case = {
            "id": spec.id,
            "title": spec.title,
            "steps": steps,
        }
    return case


def _compile_step(original: str, source_step_index: int, environment: Mapping[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
    body_match = STEP_PATTERN.match(original)
    body = body_match.group("body") if body_match else original

    if match := OPEN_PATTERN.match(body):
        url = _resolve_text(match.group("url"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"open {_url_slug_source(url)}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "navigate",
            "url": url,
        }

    if match := ENTER_PATTERN.match(body):
        label = _resolve_text(match.group("label"), environment, data, path=f"$.steps[{source_step_index}]")
        value = _resolve_value(match.group("value"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"enter {label}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "fill",
            "locator": {
                "strategy": "label",
                "value": label,
            },
            "value": value,
        }

    if match := CLICK_LINK_PATTERN.match(body):
        name = _resolve_text(match.group("name"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"click link {name}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "click",
            "locator": {
                "strategy": "role",
                "role": "link",
                "value": name,
            },
        }

    if match := CLICK_PATTERN.match(body):
        name = _resolve_text(match.group("name"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"click {name}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "click",
            "locator": {
                "strategy": "role",
                "role": "button",
                "value": name,
            },
        }

    if match := ROLE_VISIBLE_PATTERN.match(body):
        role = str(match.group("role")).lower()
        text = _resolve_text(match.group("text"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert {role} {text}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_visible",
            "locator": {
                "strategy": "role",
                "role": role,
                "value": text,
            },
        }

    if match := LOCATOR_VISIBLE_PATTERN.match(body):
        value = _resolve_text(match.group("value"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert {match.group('kind')} {value} visible"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_visible",
            "locator": _locator_for_kind(match.group("kind"), value),
        }

    if match := TITLE_EQUALS_PATTERN.match(body):
        title = _resolve_text(match.group("title"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert title {title}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_title",
            "expected": title,
        }

    if match := LOCATOR_CHECKED_PATTERN.match(body):
        value = _resolve_text(match.group("value"), environment, data, path=f"$.steps[{source_step_index}]")
        expected = not bool(match.group("negation"))
        return {
            "id": _slug_identifier(f"assert checked {value}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_checked",
            "locator": _locator_for_kind(match.group("kind"), value),
            "expected": expected,
        }

    if match := LOCATOR_ENABLED_PATTERN.match(body):
        value = _resolve_text(match.group("value"), environment, data, path=f"$.steps[{source_step_index}]")
        expected = str(match.group("state")).lower() == "enabled"
        return {
            "id": _slug_identifier(f"assert enabled {value}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_enabled",
            "locator": _locator_for_kind(match.group("kind"), value),
            "expected": expected,
        }

    if match := LOCATOR_ATTRIBUTE_EQUALS_PATTERN.match(body):
        value = _resolve_text(match.group("value"), environment, data, path=f"$.steps[{source_step_index}]")
        expected = _resolve_text(match.group("expected"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert attribute {match.group('attribute')} {value}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_attribute_equals",
            "locator": _locator_for_kind(match.group("kind"), value),
            "attribute": match.group("attribute"),
            "expected": expected,
        }

    if match := LOCATOR_COUNT_PATTERN.match(body):
        value = _resolve_text(match.group("value"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert count {value}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_count",
            "locator": _locator_for_kind(match.group("kind"), value),
            "expected": int(match.group("count")),
        }

    if match := VISIBLE_PATTERN.match(body):
        text = _resolve_text(match.group("text"), environment, data, path=f"$.steps[{source_step_index}]")
        if is_ambiguous_semantic_visible_text(text):
            raise CompilerError(
                (
                    ValidationIssue(
                        f"$.steps[{source_step_index}]",
                        (
                            f'visible text assertion "{text}" names a UI role or locator concept; '
                            'use an exact accessible name such as heading "Dashboard" should be visible'
                        ),
                        "step.ambiguous_semantic_assertion",
                    ),
                )
            )
        return {
            "id": _slug_identifier(f"assert {text}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_visible",
            "locator": {
                "strategy": "text",
                "value": text,
            },
        }

    if match := URL_EQUALS_PATTERN.match(body):
        url = _resolve_text(match.group("url"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert url {_url_slug_source(url)}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_url",
            "url": url,
        }

    if match := TEXT_EQUALS_PATTERN.match(body):
        label = _resolve_text(match.group("label"), environment, data, path=f"$.steps[{source_step_index}]")
        expected = _resolve_value(match.group("expected"), environment, data, path=f"$.steps[{source_step_index}]")
        return {
            "id": _slug_identifier(f"assert {label}"),
            "sourceStepIndex": source_step_index,
            "original": original,
            "action": "assert_text_equals",
            "locator": {
                "strategy": "label",
                "value": label,
            },
            "expected": expected,
        }

    raise CompilerError(
        (
            ValidationIssue(
                f"$.steps[{source_step_index}]",
                f"unsupported structured step: {original}",
                "step.unsupported",
            ),
        )
    )


def _locator_for_kind(kind: str, value: str) -> dict[str, str]:
    normalized = kind.strip().lower().replace("-", " ")
    if normalized == "test id":
        return {"strategy": "test_id", "value": value}
    return {"strategy": "css", "value": value}


def _resolve_text(text: str, environment: Mapping[str, Any], data: Mapping[str, Any], *, path: str) -> str:
    resolved = _resolve_value(text, environment, data, path=path)
    if not isinstance(resolved, str):
        return str(resolved)
    return resolved


def _resolve_value(text: str, environment: Mapping[str, Any], data: Mapping[str, Any], *, path: str) -> Any:
    exact_match = PLACEHOLDER_PATTERN.fullmatch(text)
    if exact_match:
        return _resolve_placeholder(exact_match, environment, data, path=path)

    def replacement(match: re.Match[str]) -> str:
        value = _resolve_placeholder(match, environment, data, path=path)
        if value is None:
            return ""
        return str(value)

    return PLACEHOLDER_PATTERN.sub(replacement, text)


def _resolve_placeholder(match: re.Match[str], environment: Mapping[str, Any], data: Mapping[str, Any], *, path: str) -> Any:
    namespace = match.group("namespace")
    lookup_path = match.group("path")
    source = environment if namespace == "env" else data
    try:
        value = _lookup_path(source, lookup_path)
    except KeyError as exc:
        code = "binding.missing_env" if namespace == "env" else "binding.missing_data"
        raise CompilerError((ValidationIssue(path, f"missing {namespace} binding {{{namespace}.{lookup_path}}}", code),)) from exc

    if not isinstance(value, SCALAR_TYPES):
        code = "binding.non_scalar_env" if namespace == "env" else "binding.non_scalar_data"
        raise CompilerError((ValidationIssue(path, f"binding {{{namespace}.{lookup_path}}} must resolve to a scalar value", code),))
    return value


def _select_environment(document: Mapping[str, Any], environment_name: str, issues: list[ValidationIssue]) -> dict[str, Any]:
    if not IDENTIFIER_PATTERN.fullmatch(environment_name):
        issues.append(ValidationIssue("$.environment", "environment name must be a lowercase identifier", "environment.invalid_name"))
        return {}

    if "environments" not in document:
        return dict(document)

    base = document.get("base", {})
    environments = document.get("environments")
    if not isinstance(base, dict):
        issues.append(ValidationIssue("$.base", "base environment config must be an object", "environment.base_type"))
        base = {}
    if not isinstance(environments, dict):
        issues.append(ValidationIssue("$.environments", "environments must be an object", "environment.environments_type"))
        return dict(base)
    selected = environments.get(environment_name)
    if not isinstance(selected, dict):
        issues.append(ValidationIssue("$.environments", f"environment {environment_name!r} is not defined", "environment.missing"))
        selected = {}
    return _deep_merge(base, selected)


def _validate_environment(environment_name: str, environment: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "baseUrl" not in environment:
        issues.append(ValidationIssue("$.baseUrl", "environment baseUrl is required", "environment.base_url_required"))
    elif not isinstance(environment["baseUrl"], str) or not environment["baseUrl"].startswith(("http://", "https://")):
        issues.append(ValidationIssue("$.baseUrl", "environment baseUrl must be an http(s) URL", "environment.base_url_invalid"))

    auth_state_ref = _optional_string_at(environment, "authStateRef")
    storage_state_path = _optional_string_at(environment, "auth.storageStatePath")
    if auth_state_ref is not None and not auth_state_ref:
        issues.append(ValidationIssue("$.authStateRef", "authStateRef must not be empty", "environment.auth_state_ref_empty"))
    if storage_state_path is not None and not storage_state_path:
        issues.append(ValidationIssue("$.auth.storageStatePath", "storageStatePath must not be empty", "environment.storage_state_path_empty"))

    issues.extend(find_secret_issues(environment, message="raw secret-looking value is not allowed in environment config", code="secret.raw_value"))
    if not environment_name:
        issues.append(ValidationIssue("$.environment", "environment name is required", "environment.name_required"))
    return issues


def _validate_data_rows(rows: Sequence[Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        row_path = f"$.rows[{index}]"
        if not isinstance(row, dict):
            issues.append(ValidationIssue(row_path, "data row must be an object", "data.row_type"))
            continue

        row_id = row.get("id")
        if not isinstance(row_id, str) or not IDENTIFIER_PATTERN.fullmatch(row_id):
            issues.append(ValidationIssue(f"{row_path}.id", "data row id must be a lowercase identifier", "data.row_id_invalid"))
        elif row_id in seen_ids:
            issues.append(ValidationIssue(f"{row_path}.id", f"duplicate data row id {row_id!r}", "data.row_id_duplicate"))
        else:
            seen_ids.add(row_id)

        for key, value in row.items():
            if not IDENTIFIER_PATTERN.fullmatch(str(key)):
                issues.append(ValidationIssue(f"{row_path}.{key}", "data keys must be lowercase identifiers", "data.key_invalid"))
            if not isinstance(value, SCALAR_TYPES):
                issues.append(ValidationIssue(f"{row_path}.{key}", "data row values must be scalar", "data.value_non_scalar"))

        issues.extend(find_secret_issues(row, message="raw secret-looking value is not allowed in data rows", code="secret.raw_value"))
    return issues


def _validate_compiled_document(document: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
    issues = find_secret_issues(document, message="raw secret-looking value is not allowed in compiled IR", code="secret.raw_value")
    return tuple(deduplicate_issues(issues))


def _load_yaml_mapping(path: Path, *, document_name: str) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CompilerError((ValidationIssue("$", f"{document_name} file not found: {path}", f"{document_name.replace(' ', '_')}.not_found"),)) from exc
    except yaml.YAMLError as exc:
        raise CompilerError((ValidationIssue("$", f"invalid YAML in {document_name}: {exc}", "yaml.parse"),)) from exc

    if not isinstance(document, dict):
        raise CompilerError((ValidationIssue("$", f"{document_name} document must be an object", f"{document_name.replace(' ', '_')}.type"),))
    return document


def _lookup_path(source: Mapping[str, Any], lookup_path: str) -> Any:
    value: Any = source
    for part in lookup_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise KeyError(lookup_path)
        value = value[part]
    return value


def _optional_string_at(source: Mapping[str, Any], lookup_path: str) -> str | None:
    try:
        value = _lookup_path(source, lookup_path)
    except KeyError:
        return None
    return value if isinstance(value, str) else None


def _expand_dot_path_mapping(flat: Mapping[str, Any]) -> dict[str, Any]:
    expanded: dict[str, Any] = {}
    for key, value in flat.items():
        cursor = expanded
        parts = key.split(".")
        for part in parts[:-1]:
            next_value = cursor.setdefault(part, {})
            if not isinstance(next_value, dict):
                next_value = {}
                cursor[part] = next_value
            cursor = next_value
        cursor[parts[-1]] = value
    return expanded


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _assign_unique_step_ids(steps: list[dict[str, Any]]) -> None:
    seen: dict[str, int] = {}
    for step in steps:
        base_id = step["id"]
        count = seen.get(base_id, 0)
        seen[base_id] = count + 1
        if count:
            step["id"] = f"{base_id}_{count + 1}"


def _url_slug_source(url: str) -> str:
    path = url.split("?", maxsplit=1)[0].rstrip("/")
    last_segment = path.rsplit("/", maxsplit=1)[-1]
    return last_segment or "home"


def _slug_identifier(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug:
        slug = "step"
    if not slug[0].isalpha():
        slug = f"step_{slug}"
    return slug
