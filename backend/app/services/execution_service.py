from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin
from uuid import uuid4

import yaml

from plain_english_test_framework.compiler import CompilerError, compile_spec_file
from plain_english_test_framework.ir_validator import IrValidationError
from plain_english_test_framework.local_runner import LocalPlaywrightRunnerError, run_local_playwright
from plain_english_test_framework.playwright_generator import PlaywrightGenerationError
from plain_english_test_framework.spec_parser import SpecValidationError
from plain_english_test_framework.validation import ValidationIssue

from ..config import ExecutionSettings, get_execution_settings
from ..models import (
    ExecutionCandidate,
    ExecutionIssue,
    ExecutionPreviewResponse,
    ExecutionPreviewSummary,
    ExecutionRunItem,
    ExecutionRunResponse,
    ExecutionRunSummary,
    ExecutionUnsupportedStep,
    TestCase,
    TestStep,
)


URL_PATTERN = re.compile(r"https?://[^\s\"')]+", re.IGNORECASE)
QUOTED_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")
VISUAL_LOOKUP_PATTERN = re.compile(
    r"\b(?:locate|find|scan|inspect|review|check|verify|confirm)\b",
    re.IGNORECASE,
)
FILL_PATTERN = re.compile(
    r"\b(?:enter|fill|type|input)\b\s+(?P<value>.+?)\s+\b(?:in|into)\b\s+(?:the\s+)?(?P<label>.+?)(?:\s+field)?\.?$",
    re.IGNORECASE,
)
CLICK_PATTERN = re.compile(
    r"\b(?:click|press|tap|select)\b\s+(?:the\s+)?(?P<name>.+?)(?:\s+(?P<role>button|control|link|option))?\.?$",
    re.IGNORECASE,
)
TEXT_EQUALS_PATTERN = re.compile(
    r"['\"](?P<label>[^'\"]+)['\"]\s+(?:should\s+)?(?:equal|equals|be)\s+['\"](?P<expected>[^'\"]*)['\"]",
    re.IGNORECASE,
)
VISIBLE_PATTERN = re.compile(
    r"(?P<text>.+?)\s+(?:is|are|should be)\s+(?:displayed|visible|shown|present)\b",
    re.IGNORECASE,
)
HEADING_VISIBLE_PATTERN = re.compile(
    r"\bheading\s+['\"](?P<text>[^'\"]+)['\"]\s+(?:is|should be)\s+(?:displayed|visible|shown|present)\b",
    re.IGNORECASE,
)
LOADS_PATTERN = re.compile(r"(?P<text>.+?)\s+loads\s+successfully\b", re.IGNORECASE)
UNSUPPORTED_DOMAIN_TERMS = (
    "sap gui",
    "hana studio",
    "solution manager",
    "alm",
    "transaction code",
    "t-code",
    "transaction su",
    "transaction sm",
    "transaction se",
    "su01",
    "su10",
    "sm37",
    "se38",
    "se80",
    "abap",
    "batch job",
    "backup",
    "restore",
    "sla",
    "performance timing",
)


@dataclass(frozen=True)
class _ConvertedStep:
    kind: str
    body: str


def preview_execution(
    test_cases: Iterable[TestCase],
    *,
    target_base_url: str | None = None,
    settings: ExecutionSettings | None = None,
) -> ExecutionPreviewResponse:
    settings = settings or get_execution_settings()
    cases = list(test_cases)
    warnings: list[str] = []
    if len(cases) > settings.max_cases_per_request:
        warnings.append(
            f"Only the first {settings.max_cases_per_request} test cases were reviewed for execution."
        )
        cases = cases[: settings.max_cases_per_request]

    executable: list[ExecutionCandidate] = []
    manual: list[ExecutionCandidate] = []
    unsupported: list[ExecutionCandidate] = []
    invalid: list[ExecutionCandidate] = []
    base_url = _normalize_base_url(target_base_url or settings.default_base_url)

    for test_case in cases:
        candidate = _candidate_for_test_case(test_case, base_url=base_url)
        if candidate.status == "executable":
            executable.append(candidate)
        elif candidate.status == "manual":
            manual.append(candidate)
        elif candidate.status == "unsupported":
            unsupported.append(candidate)
        else:
            invalid.append(candidate)

    return ExecutionPreviewResponse(
        executable=executable,
        manual=manual,
        unsupported=unsupported,
        invalid=invalid,
        warnings=warnings,
        summary=ExecutionPreviewSummary(
            executable=len(executable),
            manual=len(manual),
            unsupported=len(unsupported),
            invalid=len(invalid),
        ),
    )


def run_execution(
    test_cases: Iterable[TestCase],
    *,
    selected_test_case_ids: Iterable[str] = (),
    target_base_url: str | None = None,
    settings: ExecutionSettings | None = None,
) -> ExecutionRunResponse:
    settings = settings or get_execution_settings()
    preview = preview_execution(test_cases, target_base_url=target_base_url, settings=settings)
    run_id = f"exec_{uuid4().hex[:12]}"

    if not settings.enabled:
        summary = ExecutionRunSummary(
            skipped=len(preview.executable),
            manual=len(preview.manual),
            unsupported=len(preview.unsupported),
            invalid=len(preview.invalid),
        )
        return ExecutionRunResponse(
            status="disabled",
            run_id=run_id,
            preview=preview,
            summary=summary,
            warnings=[*preview.warnings, "Execution is disabled by backend configuration."],
        )

    selected_ids = {str(case_id) for case_id in selected_test_case_ids if str(case_id).strip()}
    executable_candidates = [
        candidate for candidate in preview.executable
        if not selected_ids or candidate.source_test_case_id in selected_ids or candidate.id in selected_ids
    ]
    selected_non_executable = _selected_non_executable(preview, selected_ids)
    run_root = settings.artifact_root / run_id
    specs_dir = run_root / "specs"
    ir_dir = run_root / "ir"
    generated_dir = run_root / "generated" / "playwright"
    artifacts_root = run_root / "artifacts" / "playwright"
    env_path = run_root / "environment.yaml"
    data_dir = run_root / "data"

    specs_dir.mkdir(parents=True, exist_ok=True)
    ir_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path.write_text(yaml.safe_dump({"baseUrl": _normalize_base_url(target_base_url or settings.default_base_url)}, sort_keys=False), encoding="utf-8")

    results: list[ExecutionRunItem] = []
    for candidate in executable_candidates:
        results.append(
            _run_candidate(
                candidate,
                specs_dir=specs_dir,
                ir_dir=ir_dir,
                data_dir=data_dir,
                env_path=env_path,
                generated_dir=generated_dir,
                artifacts_root=artifacts_root,
                settings=settings,
            )
        )

    for candidate in selected_non_executable:
        results.append(
            ExecutionRunItem(
                id=candidate.id,
                source_test_case_id=candidate.source_test_case_id,
                title=candidate.title,
                status="skipped",
                issues=[
                    ExecutionIssue(
                        path="$",
                        message=f"Candidate is {candidate.status} and was not executed.",
                        code=f"execution.{candidate.status}",
                    )
                ],
            )
        )

    summary = _summarize_run(results, preview)
    status = "passed" if summary.failed == 0 and summary.invalid == 0 else "failed"
    return ExecutionRunResponse(
        status=status,
        run_id=run_id,
        artifacts_root=str(run_root),
        results=results,
        preview=preview,
        warnings=preview.warnings,
        summary=summary,
    )


def _candidate_for_test_case(test_case: TestCase, *, base_url: str) -> ExecutionCandidate:
    candidate_id = _slug_identifier(test_case.id)
    metadata = _metadata_for_test_case(test_case)
    traceability_ids = [
        *test_case.linked_requirement_ids,
        *test_case.scenario_refs,
        *(test_case.source_refs or []),
    ]

    if test_case.automation_status == "Manual":
        return ExecutionCandidate(
            id=candidate_id,
            source_test_case_id=test_case.id,
            title=test_case.title,
            status="manual",
            metadata=metadata,
            review_reasons=["Automation Status is Manual, so the case is skipped by default."],
            traceability_ids=traceability_ids,
        )

    converted_steps: list[_ConvertedStep] = []
    unsupported_steps: list[ExecutionUnsupportedStep] = []
    fatal_unsupported_steps: list[ExecutionUnsupportedStep] = []
    for step in test_case.steps:
        unsupported_reason = _unsupported_domain_reason(step)
        if unsupported_reason is not None:
            fatal_unsupported_steps.append(_unsupported_step(step, unsupported_reason, "manual_execution"))
            continue

        step_conversions = _convert_step(step, base_url=base_url)
        if not step_conversions:
            unsupported_steps.append(_unsupported_step(step, "unsupported_step", "locator_mapping_or_manual_execution"))
            continue
        converted_steps.extend(step_conversions)

    if fatal_unsupported_steps:
        return ExecutionCandidate(
            id=candidate_id,
            source_test_case_id=test_case.id,
            title=test_case.title,
            status="unsupported",
            metadata=metadata,
            unsupported_steps=fatal_unsupported_steps,
            review_reasons=["One or more source steps cannot be represented by the current browser DSL."],
            traceability_ids=traceability_ids,
        )

    if not converted_steps:
        return ExecutionCandidate(
            id=candidate_id,
            source_test_case_id=test_case.id,
            title=test_case.title,
            status="invalid",
            metadata=metadata,
            review_reasons=["No executable browser steps were found."],
            traceability_ids=traceability_ids,
        )

    if converted_steps[0].kind != "navigate":
        unsupported_steps.append(
            ExecutionUnsupportedStep(
                step=test_case.steps[0].step if test_case.steps else 0,
                action=test_case.steps[0].action if test_case.steps else "",
                expected=test_case.steps[0].expected if test_case.steps else None,
                test_data=test_case.steps[0].test_data if test_case.steps else None,
                reason_code="implicit_navigation",
                suggested_next_action="Target base URL was used as the deterministic browser navigation step.",
            )
        )
        converted_steps.insert(0, _ConvertedStep("navigate", f'I open "{_escape_step_text(base_url)}"'))

    if not any(step.kind in {"assert_visible", "assert_text_equals", "assert_url"} for step in converted_steps):
        return ExecutionCandidate(
            id=candidate_id,
            source_test_case_id=test_case.id,
            title=test_case.title,
            status="unsupported",
            metadata=metadata,
            unsupported_steps=[
                ExecutionUnsupportedStep(
                    step=test_case.steps[-1].step if test_case.steps else 0,
                    action=test_case.steps[-1].action if test_case.steps else "",
                    expected=test_case.expected_result or (test_case.steps[-1].expected if test_case.steps else None),
                    test_data=test_case.test_data,
                    reason_code="missing_assertion",
                    suggested_next_action="Add a visible text or text equality assertion.",
                )
            ],
            review_reasons=["Executable browser cases need at least one deterministic assertion."],
            traceability_ids=traceability_ids,
        )

    spec = {
        "schemaVersion": "1.0",
        "id": candidate_id,
        "title": test_case.title,
        "description": test_case.description or test_case.expected_result or None,
        "tags": _normalize_tags(test_case),
        "steps": _render_structured_steps(converted_steps),
    }
    if spec["description"] is None:
        spec.pop("description")

    return ExecutionCandidate(
        id=candidate_id,
        source_test_case_id=test_case.id,
        title=test_case.title,
        status="executable",
            spec=spec,
            metadata=metadata,
            unsupported_steps=unsupported_steps,
            review_reasons=(
                ["Some source steps were not executable and were omitted from the generated browser spec."]
                if unsupported_steps
                else []
            ),
            traceability_ids=traceability_ids,
        )


def _convert_step(step: TestStep, *, base_url: str) -> list[_ConvertedStep]:
    converted: list[_ConvertedStep] = []
    action = _normalize_text(step.action)
    expected = _normalize_text(step.expected)

    if _looks_like_navigation(action):
        converted.append(_ConvertedStep("navigate", f'I open "{_resolve_step_url(action, base_url)}"'))
    elif match := FILL_PATTERN.search(action):
        value = _clean_value(match.group("value"))
        label = _clean_label(match.group("label"))
        if value and label:
            converted.append(_ConvertedStep("fill", f'I enter "{_escape_step_text(value)}" into "{_escape_step_text(label)}"'))
    elif match := CLICK_PATTERN.search(action):
        name = _clean_label(match.group("name"))
        if name:
            role = str(match.group("role") or "").lower()
            if role == "link":
                converted.append(_ConvertedStep("click", f'I click link "{_escape_step_text(name)}"'))
            else:
                converted.append(_ConvertedStep("click", f'I click "{_escape_step_text(name)}"'))
    elif assertion := _convert_assertion(action):
        converted.append(assertion)
    elif lookup_assertion := _convert_visual_lookup(action):
        converted.append(lookup_assertion)

    if expected:
        assertion = _convert_assertion(expected)
        if assertion:
            converted.append(assertion)

    return converted


def _convert_assertion(text: str) -> _ConvertedStep | None:
    if re.search(r"\bURL\b", text, re.IGNORECASE):
        if match := URL_PATTERN.search(text):
            return _ConvertedStep("assert_url", f'URL should be "{_escape_step_text(match.group(0))}"')

    if match := HEADING_VISIBLE_PATTERN.search(text):
        heading_text = _clean_value(match.group("text"))
        if heading_text:
            return _ConvertedStep("assert_visible", f'heading "{_escape_step_text(heading_text)}" should be visible')

    if match := TEXT_EQUALS_PATTERN.search(text):
        label = _clean_label(match.group("label"))
        expected = _clean_value(match.group("expected"))
        if label:
            return _ConvertedStep("assert_text_equals", f'"{_escape_step_text(label)}" should equal "{_escape_step_text(expected)}"')

    quoted_values = QUOTED_PATTERN.findall(text)
    if quoted_values and re.search(r"\b(displays?|displayed|visible|shown|present|appears)\b", text, re.IGNORECASE):
        return _ConvertedStep("assert_visible", f'"{_escape_step_text(quoted_values[-1])}" should be visible')

    for pattern in (VISIBLE_PATTERN, LOADS_PATTERN):
        if match := pattern.search(text):
            visible_text = _clean_visible_text(match.group("text"))
            if visible_text:
                return _ConvertedStep("assert_visible", f'"{_escape_step_text(visible_text)}" should be visible')

    return None


def _convert_visual_lookup(text: str) -> _ConvertedStep | None:
    if not VISUAL_LOOKUP_PATTERN.search(text):
        return None
    quoted_values = [value.strip() for value in QUOTED_PATTERN.findall(text) if value.strip()]
    if not quoted_values:
        return None
    return _ConvertedStep("assert_visible", f'"{_escape_step_text(quoted_values[0])}" should be visible')


def _render_structured_steps(steps: list[_ConvertedStep]) -> list[str]:
    rendered: list[str] = []
    emitted_interaction = False
    emitted_assertion = False
    for index, step in enumerate(steps):
        if index == 0 and step.kind == "navigate":
            keyword = "Given"
        elif step.kind in {"assert_visible", "assert_text_equals", "assert_url"}:
            keyword = "And" if emitted_assertion else "Then"
            emitted_assertion = True
        elif step.kind == "navigate":
            keyword = "And"
        else:
            keyword = "And" if emitted_interaction else "When"
            emitted_interaction = True
        rendered.append(f"{keyword} {step.body}")
    return rendered


def _run_candidate(
    candidate: ExecutionCandidate,
    *,
    specs_dir: Path,
    ir_dir: Path,
    data_dir: Path,
    env_path: Path,
    generated_dir: Path,
    artifacts_root: Path,
    settings: ExecutionSettings,
) -> ExecutionRunItem:
    spec_path = specs_dir / f"{candidate.id}.yaml"
    ir_path = ir_dir / f"{candidate.id}.ir.json"
    artifacts_dir = artifacts_root / candidate.id
    try:
        spec_path.write_text(yaml.safe_dump(candidate.spec, sort_keys=False), encoding="utf-8")
        ir = compile_spec_file(
            spec_path,
            environment_path=env_path,
            environment_name="web",
            data_dir=data_dir,
        )
        ir_path.write_text(json.dumps(ir.raw, indent=2, sort_keys=True), encoding="utf-8")
        run = run_local_playwright(
            ir_path,
            generated_dir=generated_dir,
            artifacts_dir=artifacts_dir,
            config_path=settings.playwright_config_path,
            cwd=settings.runtime_cwd,
        )
    except (CompilerError, SpecValidationError, IrValidationError, PlaywrightGenerationError, LocalPlaywrightRunnerError) as exc:
        return ExecutionRunItem(
            id=candidate.id,
            source_test_case_id=candidate.source_test_case_id,
            title=candidate.title,
            status="invalid",
            spec_id=candidate.id,
            ir_path=str(ir_path) if ir_path.exists() else None,
            artifacts_dir=str(artifacts_dir),
            issues=_issues_from_exception(exc),
        )

    status = "passed" if run.returncode == 0 else "failed"
    return ExecutionRunItem(
        id=candidate.id,
        source_test_case_id=candidate.source_test_case_id,
        title=candidate.title,
        status=status,
        spec_id=candidate.id,
        ir_path=str(ir_path),
        generated_spec_path=str(run.generated_spec_path),
        artifacts_dir=str(run.paths.artifacts_dir),
        returncode=run.returncode,
        stdout=run.stdout,
        stderr=run.stderr,
    )


def _summarize_run(results: list[ExecutionRunItem], preview: ExecutionPreviewResponse) -> ExecutionRunSummary:
    return ExecutionRunSummary(
        passed=sum(1 for item in results if item.status == "passed"),
        failed=sum(1 for item in results if item.status == "failed"),
        invalid=sum(1 for item in results if item.status == "invalid"),
        skipped=sum(1 for item in results if item.status == "skipped"),
        unsupported=len(preview.unsupported),
        manual=len(preview.manual),
    )


def _selected_non_executable(preview: ExecutionPreviewResponse, selected_ids: set[str]) -> list[ExecutionCandidate]:
    if not selected_ids:
        return []
    candidates = [*preview.manual, *preview.unsupported, *preview.invalid]
    return [
        candidate for candidate in candidates
        if candidate.source_test_case_id in selected_ids or candidate.id in selected_ids
    ]


def _issues_from_exception(exc: Exception) -> list[ExecutionIssue]:
    issues = getattr(exc, "issues", None)
    if not issues:
        return [ExecutionIssue(path="$", message=str(exc), code="execution.error")]
    return [_issue_to_model(issue) for issue in issues]


def _issue_to_model(issue: ValidationIssue) -> ExecutionIssue:
    return ExecutionIssue(path=issue.path, message=issue.message, code=issue.code)


def _unsupported_step(step: TestStep, reason_code: str, suggested_next_action: str) -> ExecutionUnsupportedStep:
    return ExecutionUnsupportedStep(
        step=step.step,
        action=step.action,
        expected=step.expected,
        test_data=step.test_data,
        reason_code=reason_code,
        suggested_next_action=suggested_next_action,
    )


def _unsupported_domain_reason(step: TestStep) -> str | None:
    text = f"{step.action} {step.expected} {step.test_data or ''}".lower()
    for term in UNSUPPORTED_DOMAIN_TERMS:
        if term in text:
            return "unsupported_non_browser_domain"
    return None


def _looks_like_navigation(text: str) -> bool:
    lowered = text.lower()
    if URL_PATTERN.search(text):
        return True
    if any(word in lowered for word in ("navigate", "open", "go to", "visit")):
        return True
    return bool(
        re.search(r"\blaunch\b", lowered)
        and re.search(r"\b(app|application|browser|page|site|url)\b", lowered)
        and not re.search(r"\blaunch\s+command\b", lowered)
    )


def _resolve_step_url(action: str, base_url: str) -> str:
    if match := URL_PATTERN.search(action):
        return match.group(0)
    if path_match := re.search(r"\b(?:path|route|url)\s+['\"]([^'\"]+)['\"]", action, re.IGNORECASE):
        return urljoin(base_url.rstrip("/") + "/", path_match.group(1).lstrip("/"))
    return base_url


def _metadata_for_test_case(test_case: TestCase) -> dict[str, Any]:
    return {
        "description": test_case.description,
        "priority": test_case.priority,
        "type": test_case.type,
        "status": test_case.status,
        "preconditions": test_case.preconditions,
        "expected_result": test_case.expected_result,
        "test_data": test_case.test_data,
        "automation_status": test_case.automation_status,
        "component": test_case.component,
        "tags": test_case.tags or [],
        "linked_requirement_ids": test_case.linked_requirement_ids,
        "scenario_refs": test_case.scenario_refs,
        "source_refs": test_case.source_refs or [],
    }


def _normalize_tags(test_case: TestCase) -> list[str]:
    raw_tags = [
        *(test_case.tags or []),
        *test_case.linked_requirement_ids,
        *test_case.scenario_refs,
    ]
    tags: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        normalized = _slug_identifier(tag).replace("_", "-")
        if normalized and normalized not in seen:
            tags.append(normalized)
            seen.add(normalized)
    return tags


def _normalize_base_url(value: str | Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "http://127.0.0.1:5173"
    return normalized


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_value(value: str) -> str:
    normalized = _normalize_text(value).strip(" .")
    if quoted := QUOTED_PATTERN.findall(normalized):
        return quoted[0].strip()
    return normalized


def _clean_label(value: str) -> str:
    normalized = _clean_value(value)
    normalized = re.sub(r"^(?:the|a|an)\s+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+(?:field|input|button|control|link|option)$", "", normalized, flags=re.IGNORECASE)
    return normalized.strip()


def _clean_visible_text(value: str) -> str:
    normalized = _clean_value(value)
    normalized = re.sub(r"^(?:the|a|an)\s+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(?:page|screen|message|form|label|section)$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized


def _escape_step_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _slug_identifier(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    if not slug:
        slug = "case"
    if not slug[0].isalpha():
        slug = f"tc_{slug}"
    return slug
