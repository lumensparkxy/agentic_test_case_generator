from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml

from plain_english_test_framework.compiler import SCOPED_TEXT_ASSERTION_PATTERN, VALUE_ASSERTION_PATTERN, CompilerError, compile_spec_file
from plain_english_test_framework.ir_validator import IrValidationError
from plain_english_test_framework.local_runner import (
    LocalPlaywrightRunnerError,
    generate_local_playwright_spec,
    run_local_playwright_specs,
)
from plain_english_test_framework.playwright_generator import TARGET_ORIGIN_ERROR, PlaywrightGenerationError
from plain_english_test_framework.semantic_assertions import is_ambiguous_semantic_visible_text
from plain_english_test_framework.spec_parser import SpecValidationError
from plain_english_test_framework.validation import ValidationIssue

from ..config import ExecutionSettings, get_execution_settings
from ..models import (
    ExecutionCandidate,
    ExecutionIssue,
    ExecutionPreviewResponse,
    ExecutionRunItem,
    ExecutionRunResponse,
    ExecutionRunSummary,
    ExecutionUnsupportedStep,
    TestCase,
    TestStep,
)
from .execution_target import bind_execution_url


URL_PATTERN = re.compile(
    r"""(?P<quote>["'\x60])(?P<quoted>https?://[^\s]+?)(?P=quote)"""
    r"|<(?P<angled>https?://[^\s<>]+)>"
    r"""|(?P<bare>https?://[^\s"'\x60()<>]+)""",
    re.IGNORECASE,
)
QUOTED_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")
OBSERVATION_ACTION_PATTERN = re.compile(r"^(?:locate|find|scan|inspect|review|observe|look for)\b", re.IGNORECASE)
ASSERTION_ACTION_PATTERN = re.compile(r"^(?:verify|assert|confirm|check|URL)\b", re.IGNORECASE)
ACTION_SEQUENCE_PATTERN = re.compile(
    r"\b(?:and|then)\s+(?:verify|assert|confirm|check|uncheck|click|press|tap|select|open|navigate|enter|fill|wait|submit)\b",
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
ASSERTION_PREFIX_PATTERN = re.compile(r"^(?:(?:verify|assert|confirm|check)(?:\s+that)?\s+)?(?:the\s+)?", re.IGNORECASE)
QUOTED_ASSERTION_TARGET = r"""(?:"(?:\\["\\]|[^"\\])*"|'(?:\\['\\]|[^'\\])*')"""
SCOPED_TEXT_ASSERTION_INTENT = re.compile(rf"^(?:css|test\s+id)\s+{QUOTED_ASSERTION_TARGET}\s+text\b", re.IGNORECASE)
EXACT_CLICK_ACTION = re.compile(rf"^Click exact (?P<role>button|link)\s+(?P<name>{QUOTED_ASSERTION_TARGET})\.?$", re.IGNORECASE)
EXACT_CLICK_INTENT = re.compile(r"^Click\s+exact\b", re.IGNORECASE)
EXACT_SELECT_ACTION = re.compile(
    rf"^Select exact option\s+(?P<option>{QUOTED_ASSERTION_TARGET})\s+in field\s+(?P<field>{QUOTED_ASSERTION_TARGET})\.?$",
    re.IGNORECASE,
)
EXACT_SELECT_INTENT = re.compile(r"^Select\s+exact\b", re.IGNORECASE)
EXACT_CHECKBOX_ACTION = re.compile(rf"^(?P<verb>Check|Uncheck) exact checkbox\s+(?P<name>{QUOTED_ASSERTION_TARGET})\.?$", re.IGNORECASE)
EXACT_CHECKBOX_ACTION_INTENT = re.compile(r"^(?:Check\s+exact\s+checkbox|Uncheck)\b", re.IGNORECASE)
EXACT_CHECKBOX_ASSERTION = re.compile(
    rf"exact\s+checkbox\s+(?P<name>{QUOTED_ASSERTION_TARGET})"
    r"\s+(?P<state>is(?:\s+not)?|should(?:\s+not)?\s+be)\s+checked",
    re.IGNORECASE,
)
EXACT_CHECKBOX_ASSERTION_INTENT = re.compile(r"^exact\s+checkbox\b", re.IGNORECASE)
EXPLICIT_OPEN_ACTION = re.compile(r'(?:Open|Navigate to)\s+"([^"\n]+)"\.?', re.IGNORECASE)
QUOTED_NAVIGATION_ARGUMENT = re.compile(
    rf"(?:\b(?:path|route|url|at)\s+|^(?:open|navigate to|go to|visit)\s+)(?P<url>{QUOTED_ASSERTION_TARGET})",
    re.IGNORECASE,
)
BARE_NAVIGATION_ARGUMENT = re.compile(
    r"(?:\b(?:path|route|url|at)\s+|^(?:open|navigate to|go to|visit)\s+)"
    r"""(?P<url>(?:[A-Za-z][A-Za-z0-9+.-]*:|[/.?#\\])[^\s"'\x60()<>]+)""",
    re.IGNORECASE,
)
QUOTED_FIELD_ASSERTION_PATTERN = re.compile(
    r'(?P<locator>"(?:\\["\\]|[^"\\])+")\s+(?:field|input|textarea)\s+(?:value\s+)?(?:should\s+)?'
    r'(?P<operator>equals?|contains?|be|is)\s+(?P<expected>"(?:\\["\\]|[^"\\])*")',
    re.IGNORECASE,
)
FIELD_VALUE_INTENT_PATTERN = re.compile(
    rf"""^(?:
        (?:field|input|textarea|css|test\s+id)\s+{QUOTED_ASSERTION_TARGET}\s+(?:value\b|should\s+equal\b)
        |{QUOTED_ASSERTION_TARGET}\s+(?:field|input|textarea)\s+(?:value|should|equals?|matches?|contains?)\b
        |(?:[\w-]+\s+){{1,20}}(?:field|input|textarea)\s+(?:value\b|should\s+(?:equal|match|contain|be\s+(?:empty|blank))\b)
        |(?:current\s+)?value\s+of\s+
    )""",
    re.IGNORECASE | re.VERBOSE,
)
VISIBLE_PATTERN = re.compile(
    r"(?P<text>[\w ./'-]+?)\s+(?:is|are|should be)\s+(?:displayed|visible|shown|present)",
    re.IGNORECASE,
)
ROLE_VISIBLE_PATTERN = re.compile(
    rf"(?P<exact>exact\s+)?(?P<role>heading|link|button)\s+(?P<target>{QUOTED_ASSERTION_TARGET})"
    r"\s+(?:is|should be)\s+(?:displayed|visible|shown|present)",
    re.IGNORECASE,
)
EXACT_HIDDEN_PATTERN = re.compile(
    rf"exact\s+(?P<kind>heading|link|button|text)\s+(?P<target>{QUOTED_ASSERTION_TARGET})"
    r"\s+(?:is\s+not|should\s+not\s+be)\s+visible\b",
    re.IGNORECASE,
)
NEGATIVE_VISIBILITY_INTENT = re.compile(r"\bnot\s+(?:be\s+)?(?:visible|present)\b", re.IGNORECASE)
QUOTED_VISIBLE_PATTERN = re.compile(
    rf"(?P<exact>exact\s+(?:visible\s+)?text\s+)?(?P<target>{QUOTED_ASSERTION_TARGET})"
    r"\s+(?:is|should be)\s+(?:displayed|visible|shown|present)",
    re.IGNORECASE,
)
VISIBLE_SUBJECT_QUALIFIER = re.compile(r"\b(?:and|or|not|no|never|without|but|while|after|before|when)\b", re.IGNORECASE)
URL_ASSERTION_PREFIX = re.compile(r"(?:URL\s+(?:is|should\s+be)(?:\s+exactly)?|[\w -]{1,120}\s+loads successfully at)\s+", re.IGNORECASE)
VISIBLE_INTENT_PATTERN = re.compile(r"\b(?:displays?|displayed|visible|shown|present|appears)\b", re.IGNORECASE)
PAGE_TITLE_EQUALS_PATTERN = re.compile(
    r"\b(?:page\s+)?title\s+(?:is|should\s+be|equals?|should\s+equal)\s+['\"](?P<title>[^'\"]*)['\"]",
    re.IGNORECASE,
)
LOCATOR_VISIBLE_PATTERN = re.compile(
    r"\b(?:(?P<kind>css|selector|test\s*id|test-id|data-testid)\s+)?['\"](?P<value>[^'\"]+)['\"]\s+"
    r"(?:is|should be)\s+(?:displayed|visible|shown|present)\b",
    re.IGNORECASE,
)
LOCATOR_CHECKED_PATTERN = re.compile(
    r"\b(?:(?P<kind>css|selector|test\s*id|test-id|data-testid)\s+)?['\"](?P<value>[^'\"]+)['\"]\s+"
    r"should\s+(?P<negation>not\s+)?be\s+checked\b",
    re.IGNORECASE,
)
LOCATOR_ENABLED_PATTERN = re.compile(
    r"\b(?:(?P<kind>css|selector|test\s*id|test-id|data-testid)\s+)?['\"](?P<value>[^'\"]+)['\"]\s+"
    r"(?:is|should be)\s+(?P<state>enabled|disabled)\b",
    re.IGNORECASE,
)
LOCATOR_ATTRIBUTE_EQUALS_PATTERN = re.compile(
    r"\b(?:(?P<kind>css|selector|test\s*id|test-id|data-testid)\s+)?['\"](?P<value>[^'\"]+)['\"]\s+"
    r"(?:attribute|attr)\s+['\"](?P<attribute>[^'\"]+)['\"]\s+(?:is|should\s+be|equals?|should\s+equal)\s+"
    r"['\"](?P<expected>[^'\"]*)['\"]",
    re.IGNORECASE,
)
LOCATOR_COUNT_PATTERN = re.compile(
    r"\b(?:(?P<kind>css|selector|test\s*id|test-id|data-testid)\s+)?['\"](?P<value>[^'\"]+)['\"]\s+"
    r"count\s+(?:is|should\s+be|equals?|should\s+equal)\s+(?P<count>\d+)\b",
    re.IGNORECASE,
)
ASSERTION_KINDS = {
    "assert_visible",
    "assert_hidden",
    "assert_text_equals",
    "assert_scoped_text_equals",
    "assert_value_equals",
    "assert_value_contains",
    "assert_url",
    "assert_title",
    "assert_checked",
    "assert_enabled",
    "assert_attribute_equals",
    "assert_count",
}
RUNTIME_ASSERTION_TERMS = (
    "cli",
    "command",
    "console",
    "exit code",
    "return code",
    "scope mismatch",
    "scopemismatch",
    "stack trace",
    "stderr",
    "stdout",
    "terminal",
    "traceback",
    "typeerror",
)
RUNTIME_ASSERTION_PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(term) for term in RUNTIME_ASSERTION_TERMS) + r")\b", re.IGNORECASE)
STATE_ASSERTION_PATTERN = re.compile(
    r"\b(?:attribute|attr|checked|count|disabled|enabled|href|page title|return code|scopemismatch|src|stderr|stdout|title)\b",
    re.IGNORECASE,
)
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
DUPLICATE_CANDIDATE_ID_WARNING = "Duplicate or colliding test case IDs were assigned unique execution candidate IDs."


@dataclass(frozen=True)
class _ConvertedStep:
    kind: str
    body: str
    url: str | None = None


@dataclass(frozen=True)
class _PreparedExecutionCandidate:
    candidate: ExecutionCandidate
    spec_path: Path
    ir_path: Path
    generated_spec_path: Path


@dataclass(frozen=True)
class _PlaywrightCaseResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    issues: tuple[ExecutionIssue, ...] = ()


def preview_execution(
    test_cases: Iterable[TestCase],
    *,
    target_base_url: str | None = None,
    source_app_url: str | None = None,
    settings: ExecutionSettings | None = None,
) -> ExecutionPreviewResponse:
    settings = settings or get_execution_settings()
    cases = [test_case for test_case in test_cases if test_case.status != "Deprecated"]
    warnings: list[str] = []
    if len(cases) > settings.max_cases_per_request:
        warnings.append(f"Only the first {settings.max_cases_per_request} test cases were reviewed for execution.")
        cases = cases[: settings.max_cases_per_request]

    executable: list[ExecutionCandidate] = []
    manual: list[ExecutionCandidate] = []
    unsupported: list[ExecutionCandidate] = []
    invalid: list[ExecutionCandidate] = []
    used_candidate_ids: set[str] = set()
    base_url = _normalize_base_url(target_base_url or settings.default_base_url)

    for test_case in cases:
        candidate = _candidate_for_test_case(test_case, base_url=base_url, source_app_url=source_app_url)
        candidate, candidate_id_changed = _with_unique_candidate_id(candidate, used_candidate_ids=used_candidate_ids)
        if candidate_id_changed and DUPLICATE_CANDIDATE_ID_WARNING not in warnings:
            warnings.append(DUPLICATE_CANDIDATE_ID_WARNING)
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
    )


def run_execution(
    test_cases: Iterable[TestCase],
    *,
    selected_test_case_ids: Iterable[str] = (),
    target_base_url: str | None = None,
    source_app_url: str | None = None,
    settings: ExecutionSettings | None = None,
) -> ExecutionRunResponse:
    settings = settings or get_execution_settings()
    preview = preview_execution(test_cases, target_base_url=target_base_url, source_app_url=source_app_url, settings=settings)
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
        candidate for candidate in preview.executable if not selected_ids or candidate.source_test_case_id in selected_ids or candidate.id in selected_ids
    ]
    selected_non_executable = _selected_non_executable(preview, selected_ids)
    run_root = settings.artifact_root / run_id
    specs_dir = run_root / "specs"
    ir_dir = run_root / "ir"
    generated_dir = run_root / "generated" / "playwright"
    artifacts_root = run_root / "artifacts" / "playwright"
    run_artifacts_dir = artifacts_root / "run"
    env_path = run_root / "environment.yaml"
    data_dir = run_root / "data"

    specs_dir.mkdir(parents=True, exist_ok=True)
    ir_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path.write_text(yaml.safe_dump({"baseUrl": _normalize_base_url(target_base_url or settings.default_base_url)}, sort_keys=False), encoding="utf-8")

    result_by_candidate_id: dict[str, ExecutionRunItem] = {}
    prepared_candidates: list[_PreparedExecutionCandidate] = []
    for candidate in executable_candidates:
        prepared, invalid_result = _prepare_candidate(
            candidate,
            specs_dir=specs_dir,
            ir_dir=ir_dir,
            data_dir=data_dir,
            env_path=env_path,
            generated_dir=generated_dir,
            artifacts_dir=run_artifacts_dir,
        )
        if invalid_result:
            result_by_candidate_id[candidate.id] = invalid_result
        elif prepared:
            prepared_candidates.append(prepared)

    if prepared_candidates:
        for item in _run_prepared_candidates(
            prepared_candidates,
            generated_dir=generated_dir,
            artifacts_dir=run_artifacts_dir,
            settings=settings,
        ):
            result_by_candidate_id[item.id] = item

    results = [result_by_candidate_id[candidate.id] for candidate in executable_candidates if candidate.id in result_by_candidate_id]

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
    status = "passed" if summary.passed and summary.failed == 0 and summary.invalid == 0 else "failed"
    playwright_report_paths = list(dict.fromkeys(item.playwright_report_path for item in results if item.playwright_report_path))
    return ExecutionRunResponse(
        status=status,
        run_id=run_id,
        artifacts_root=str(run_root),
        playwright_report_paths=playwright_report_paths,
        results=results,
        preview=preview,
        warnings=preview.warnings,
        summary=summary,
    )


def _candidate_for_test_case(test_case: TestCase, *, base_url: str, source_app_url: str | None) -> ExecutionCandidate:
    from .execution_prerequisites import prerequisite_blockers

    candidate_id = _slug_identifier(test_case.id)
    blockers = prerequisite_blockers(test_case)
    if blockers:
        return ExecutionCandidate(id=candidate_id, source_test_case_id=test_case.id, title=test_case.title, status="manual", review_reasons=blockers)
    test_case = test_case.model_copy(deep=True)
    for prerequisite in test_case.execution_prerequisites:
        if prerequisite.reference and prerequisite.reference.startswith("env:PETF_TEST_"):
            marker = "[[" + prerequisite.reference + "]]"
            for step in test_case.steps:
                step.action = step.action.replace("{{" + prerequisite.name + "}}", marker)
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
    for step in test_case.steps:
        step_conversions, unsupported = _convert_step(step, base_url=base_url)
        if not unsupported:
            try:
                step_conversions = [_bind_url_step(item, base_url=base_url, source_app_url=source_app_url) for item in step_conversions]
            except ValueError:
                unsupported = _unsupported_step(
                    step,
                    "execution.target_origin_mismatch",
                    "Use a relative URL or an HTTP(S) URL on the selected target or verified saved application origin.",
                )
        if unsupported:
            unsupported_steps.append(unsupported)
        else:
            converted_steps.extend(step_conversions)

    if unsupported_steps:
        return ExecutionCandidate(
            id=candidate_id,
            source_test_case_id=test_case.id,
            title=test_case.title,
            status="unsupported",
            metadata=metadata,
            unsupported_steps=unsupported_steps,
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
        try:
            initial_url = bind_execution_url(base_url, base_url=base_url, source_app_url=source_app_url)
        except ValueError:
            return ExecutionCandidate(
                id=candidate_id,
                source_test_case_id=test_case.id,
                title=test_case.title,
                status="invalid",
                metadata=metadata,
                review_reasons=["Use a valid HTTP(S) execution target without inline credentials."],
                traceability_ids=traceability_ids,
            )
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
        converted_steps.insert(0, _url_step("navigate", initial_url))

    if not any(step.kind in ASSERTION_KINDS for step in converted_steps):
        return ExecutionCandidate(
            id=candidate_id,
            source_test_case_id=test_case.id,
            title=test_case.title,
            status="unsupported",
            metadata=metadata,
            unsupported_steps=[
                *unsupported_steps,
                ExecutionUnsupportedStep(
                    step=test_case.steps[-1].step if test_case.steps else 0,
                    action=test_case.steps[-1].action if test_case.steps else "",
                    expected=test_case.expected_result or (test_case.steps[-1].expected if test_case.steps else None),
                    test_data=test_case.test_data,
                    reason_code="missing_assertion",
                    suggested_next_action="Add a deterministic browser assertion such as visible text, title, locator state, or URL.",
                ),
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
        review_reasons=(["The configured target supplies the initial navigation step."] if unsupported_steps else []),
        traceability_ids=traceability_ids,
    )


def _with_unique_candidate_id(
    candidate: ExecutionCandidate,
    *,
    used_candidate_ids: set[str],
) -> tuple[ExecutionCandidate, bool]:
    base_id = candidate.id
    unique_id = base_id
    suffix = 2
    while unique_id in used_candidate_ids:
        unique_id = f"{base_id}_{suffix}"
        suffix += 1
    used_candidate_ids.add(unique_id)

    if unique_id == base_id:
        return candidate, False

    updates: dict[str, Any] = {"id": unique_id}
    if candidate.spec is not None:
        updates["spec"] = {**candidate.spec, "id": unique_id}
    return candidate.model_copy(update=updates), True


def _convert_step(step: TestStep, *, base_url: str) -> tuple[list[_ConvertedStep], ExecutionUnsupportedStep | None]:
    converted: list[_ConvertedStep] = []
    action = _normalize_text(step.action)
    expected = _normalize_text(step.expected)
    unquoted_action = re.sub(QUOTED_ASSERTION_TARGET, "", action)
    action_value, unsupported_action = _field_value_assertion(step.action)
    expected_value, unsupported_expected = _field_value_assertion(step.expected)
    action_assertion = _convert_assertion(step.action)
    expected_assertion = _convert_assertion(step.expected)
    exact_click = EXACT_CLICK_ACTION.fullmatch(step.action.strip())
    exact_select = EXACT_SELECT_ACTION.fullmatch(step.action.strip())
    exact_checkbox = EXACT_CHECKBOX_ACTION.fullmatch(step.action.strip())
    # A supported assertion's quoted locator and value are data, not operations.
    literal_assertions = {"assert_hidden", "assert_checked", "assert_scoped_text_equals"}
    action_is_literal = action_value or exact_click or exact_select or exact_checkbox or (action_assertion and action_assertion.kind in literal_assertions)
    expected_is_literal = expected_value or (expected_assertion and expected_assertion.kind in literal_assertions)
    domain_text = " ".join(("" if action_is_literal else step.action, "" if expected_is_literal else step.expected, step.test_data or "")).lower()
    if any(term in domain_text for term in UNSUPPORTED_DOMAIN_TERMS):
        return [], _unsupported_step(step, "unsupported_non_browser_domain", "manual_execution")
    if unsupported_action or unsupported_expected:
        return [], _unsupported_step(
            step,
            "unsupported_field_value_assertion",
            'Use field "Employee name" value should equal "Alex" with an exact label and value, or keep the check manual.',
        )

    if action_value:
        converted.append(action_value)
    elif EXACT_CLICK_INTENT.match(action):
        if not exact_click or not (name := _assertion_literal(exact_click["name"])).strip():
            return [], _unmapped_step(step, action, is_expected=False)
        role = exact_click["role"].lower()
        converted.append(_ConvertedStep("click", f'I click exact {role} "{_escape_step_text(name)}"'))
    elif EXACT_SELECT_INTENT.match(action):
        if not exact_select:
            return [], _unmapped_step(step, action, is_expected=False)
        option, field = (_assertion_literal(exact_select[key]) for key in ("option", "field"))
        if not (option.strip() and field.strip()):
            return [], _unmapped_step(step, action, is_expected=False)
        converted.append(_ConvertedStep("select_option", f'I select exact option "{_escape_step_text(option)}" in field "{_escape_step_text(field)}"'))
    elif EXACT_CHECKBOX_ACTION_INTENT.match(action):
        if not exact_checkbox or not (name := _assertion_literal(exact_checkbox["name"])).strip():
            return [], _unmapped_step(step, action, is_expected=False)
        verb = exact_checkbox["verb"].lower()
        converted.append(_ConvertedStep("set_checked", f'I {verb} exact checkbox "{_escape_step_text(name)}"'))
    elif action_assertion:
        converted.append(action_assertion)
    elif (
        ASSERTION_ACTION_PATTERN.match(action)
        or EXACT_CHECKBOX_ASSERTION_INTENT.match(_assertion_statement(step.action))
        or SCOPED_TEXT_ASSERTION_INTENT.match(_assertion_statement(step.action))
        or NEGATIVE_VISIBILITY_INTENT.search(unquoted_action)
        or ACTION_SEQUENCE_PATTERN.search(unquoted_action)
    ):
        return [], _unmapped_step(step, action, is_expected=False)
    elif OBSERVATION_ACTION_PATTERN.match(action):
        # The expected check represents the observation. A control name such
        # as "Open receipt" does not turn it into a navigation.
        if not (expected_value or expected_assertion) or _unsupported_browser_assertion_text_reason(action):
            return [], _unmapped_step(step, action, is_expected=False)
    elif match := FILL_PATTERN.search(action):
        value = _clean_value(match.group("value"))
        label = _clean_label(match.group("label"))
        if value and label:
            locator = f'"{_escape_step_text(label)}"'
            field_locator = f"field {locator}"
            if not expected_value and (equality := TEXT_EQUALS_PATTERN.fullmatch(_assertion_statement(step.expected))) and equality["label"] == label:
                expected_value = _ConvertedStep(
                    "assert_value_equals",
                    f'{field_locator} value should equal "{_escape_step_text(equality["expected"])}"',
                )
            if expected_value and expected_value.body.startswith(f"{field_locator} value "):
                locator = field_locator
            converted.append(_ConvertedStep("fill", f'I enter "{_escape_step_text(value)}" into {locator}'))
    elif match := CLICK_PATTERN.search(action):
        name = _clean_label(match.group("name"))
        if name:
            role = str(match.group("role") or "").lower()
            if role == "link":
                converted.append(_ConvertedStep("click", f'I click link "{_escape_step_text(name)}"'))
            else:
                converted.append(_ConvertedStep("click", f'I click "{_escape_step_text(name)}"'))
    elif _looks_like_navigation(action):
        converted.append(_url_step("navigate", _resolve_step_url(action, base_url)))

    expected_assertion = expected_value or expected_assertion
    if expected and not expected_assertion:
        return [], _unmapped_step(step, expected, is_expected=True)
    if not converted and action and not OBSERVATION_ACTION_PATTERN.match(action):
        return [], _unmapped_step(step, action, is_expected=False)
    if expected_assertion:
        converted.append(expected_assertion)
    if not converted:
        return [], _unmapped_step(step, action, is_expected=False)

    return converted, None


def is_complete_browser_action(action: str) -> bool:
    """Recognize a standalone interaction without substituting a default target."""
    action = action.strip()
    converted, issue = _convert_step(TestStep(step=1, action=action, expected=""), base_url="")
    if issue or len(converted) != 1:
        return False
    kind = converted[0].kind
    if kind == "navigate":
        opening = EXPLICIT_OPEN_ACTION.fullmatch(action)
        return bool(opening and (URL_PATTERN.fullmatch(opening[1]) or (opening[1].startswith("/") and not opening[1].startswith("//"))))
    if kind == "click":
        return bool(EXACT_CLICK_ACTION.fullmatch(action))
    return kind in {"fill", "select_option", "set_checked"}


def _field_value_assertion(text: str) -> tuple[_ConvertedStep | None, bool]:
    statement = _assertion_statement(text)
    if not FIELD_VALUE_INTENT_PATTERN.match(statement):
        return None, False
    if quoted_field := QUOTED_FIELD_ASSERTION_PATTERN.fullmatch(statement):
        operator = "contain" if quoted_field["operator"].lower() in {"contain", "contains"} else "equal"
        statement = f"field {quoted_field['locator']} value should {operator} {quoted_field['expected']}"
    if (match := VALUE_ASSERTION_PATTERN.fullmatch(statement)) and match["locator"].strip():
        kind = match["kind"].lower()
        operator = match["operator"].lower()
        if operator == "contain":
            if not match["expected"]:
                return None, True
            action = "assert_value_contains"
        else:
            action = "assert_value_equals"
        return _ConvertedStep(action, f'{kind} "{match["locator"]}" value should {operator} "{match["expected"]}"'), False
    return None, True


def _assertion_statement(text: str) -> str:
    return ASSERTION_PREFIX_PATTERN.sub("", text.strip(), count=1).removesuffix(".").rstrip()


def _convert_assertion(text: str) -> _ConvertedStep | None:
    text = _assertion_statement(text)
    if match := SCOPED_TEXT_ASSERTION_PATTERN.fullmatch(text):
        if match["locator"].strip():
            return _ConvertedStep("assert_scoped_text_equals", text)
        return None

    if match := EXACT_CHECKBOX_ASSERTION.fullmatch(text):
        target = _assertion_literal(match["name"])
        if not target.strip():
            return None
        negation = "not " if "not" in match["state"].lower().split() else ""
        return _ConvertedStep("assert_checked", f'exact checkbox "{_escape_step_text(target)}" should {negation}be checked')

    if match := EXACT_HIDDEN_PATTERN.fullmatch(text):
        target = _assertion_literal(match["target"])
        kind = match["kind"].lower()
        if not target.strip() or (kind == "text" and is_ambiguous_semantic_visible_text(target)):
            return None
        return _ConvertedStep("assert_hidden", f'exact {kind} "{_escape_step_text(target)}" should not be visible')

    if match := URL_ASSERTION_PREFIX.match(text):
        target = text[match.end() :]
        if URL_PATTERN.fullmatch(target) and (url := _extract_prose_url(target)):
            return _url_step("assert_url", url)
        return None

    if _contains_runtime_assertion_intent(text) or _contains_page_title_visible_assertion(text) or _contains_attribute_visible_assertion(text):
        return None

    if match := PAGE_TITLE_EQUALS_PATTERN.fullmatch(text):
        title = match["title"]
        return _ConvertedStep("assert_title", f'page title should be "{_escape_step_text(title)}"')

    if match := ROLE_VISIBLE_PATTERN.fullmatch(text):
        role = match["role"].lower()
        role_text = _assertion_literal(match["target"])
        if not role_text.strip():
            return None
        qualifier = "exact " if match["exact"] else ""
        return _ConvertedStep("assert_visible", f'{qualifier}{role} "{_escape_step_text(role_text)}" should be visible')

    if match := LOCATOR_ATTRIBUTE_EQUALS_PATTERN.fullmatch(text):
        locator = _locator_assertion_body(match.group("kind"), match.group("value"))
        if locator:
            attribute = _clean_value(match.group("attribute"))
            expected = match["expected"]
            return _ConvertedStep(
                "assert_attribute_equals",
                f'{locator} attribute "{_escape_step_text(attribute)}" should equal "{_escape_step_text(expected)}"',
            )

    if match := LOCATOR_COUNT_PATTERN.fullmatch(text):
        locator = _locator_assertion_body(match.group("kind"), match.group("value"))
        if locator:
            return _ConvertedStep("assert_count", f"{locator} count should be {int(match.group('count'))}")

    if match := LOCATOR_CHECKED_PATTERN.fullmatch(text):
        locator = _locator_assertion_body(match.group("kind"), match.group("value"))
        if locator:
            negation = "not " if match.group("negation") else ""
            return _ConvertedStep("assert_checked", f"{locator} should {negation}be checked")

    if match := LOCATOR_ENABLED_PATTERN.fullmatch(text):
        locator = _locator_assertion_body(match.group("kind"), match.group("value"))
        if locator:
            state = str(match.group("state")).lower()
            return _ConvertedStep("assert_enabled", f"{locator} should be {state}")

    if match := LOCATOR_VISIBLE_PATTERN.fullmatch(text):
        locator = _locator_assertion_body(match.group("kind"), match.group("value"))
        if locator:
            return _ConvertedStep("assert_visible", f"{locator} should be visible")

    if (match := TEXT_EQUALS_PATTERN.fullmatch(text)) and match["label"].strip():
        return _ConvertedStep("assert_text_equals", f'"{_escape_step_text(match["label"])}" should equal "{_escape_step_text(match["expected"])}"')

    if match := QUOTED_VISIBLE_PATTERN.fullmatch(text):
        visible_text = _assertion_literal(match["target"])
        if visible_text.strip() and not is_ambiguous_semantic_visible_text(visible_text):
            qualifier = "exact text " if match["exact"] else ""
            return _ConvertedStep("assert_visible", f'{qualifier}"{_escape_step_text(visible_text)}" should be visible')

    if (match := VISIBLE_PATTERN.fullmatch(text)) and not VISIBLE_SUBJECT_QUALIFIER.search(match["text"]):
        visible_text = match["text"].strip()
        if visible_text and not is_ambiguous_semantic_visible_text(visible_text):
            return _ConvertedStep("assert_visible", f'"{_escape_step_text(visible_text)}" should be visible')

    return None


def _assertion_literal(quoted: str) -> str:
    return re.sub(r"""\\(["'\\])""", r"\1", quoted[1:-1])


def _render_structured_steps(steps: list[_ConvertedStep]) -> list[str]:
    rendered: list[str] = []
    emitted_interaction = False
    emitted_assertion = False
    for index, step in enumerate(steps):
        if index == 0 and step.kind == "navigate":
            keyword = "Given"
        elif step.kind in ASSERTION_KINDS:
            keyword = "And" if emitted_assertion else "Then"
            emitted_assertion = True
        elif step.kind == "navigate":
            keyword = "And"
        else:
            keyword = "And" if emitted_interaction else "When"
            emitted_interaction = True
        rendered.append(f"{keyword} {step.body}")
    return rendered


def _prepare_candidate(
    candidate: ExecutionCandidate,
    *,
    specs_dir: Path,
    ir_dir: Path,
    data_dir: Path,
    env_path: Path,
    generated_dir: Path,
    artifacts_dir: Path,
) -> tuple[_PreparedExecutionCandidate | None, ExecutionRunItem | None]:
    spec_path = specs_dir / f"{candidate.id}.yaml"
    ir_path = ir_dir / f"{candidate.id}.ir.json"
    try:
        spec_path.write_text(yaml.safe_dump(candidate.spec, sort_keys=False), encoding="utf-8")
        ir = compile_spec_file(
            spec_path,
            environment_path=env_path,
            environment_name="web",
            data_dir=data_dir,
        )
        ir_path.write_text(json.dumps(ir.raw, indent=2, sort_keys=True), encoding="utf-8")
        generated_spec_path = generate_local_playwright_spec(ir_path, generated_dir=generated_dir, enforce_target_origin=True)
    except (CompilerError, SpecValidationError, IrValidationError, PlaywrightGenerationError) as exc:
        return None, _invalid_execution_item(candidate, ir_path=ir_path, artifacts_dir=artifacts_dir, issues=_issues_from_exception(exc))

    return (
        _PreparedExecutionCandidate(
            candidate=candidate,
            spec_path=spec_path,
            ir_path=ir_path,
            generated_spec_path=generated_spec_path,
        ),
        None,
    )


def _run_prepared_candidates(
    prepared_candidates: list[_PreparedExecutionCandidate],
    *,
    generated_dir: Path,
    artifacts_dir: Path,
    settings: ExecutionSettings,
) -> list[ExecutionRunItem]:
    try:
        run = run_local_playwright_specs(
            [prepared.generated_spec_path for prepared in prepared_candidates],
            generated_dir=generated_dir,
            artifacts_dir=artifacts_dir,
            config_path=settings.playwright_config_path,
            cwd=settings.runtime_cwd,
        )
    except LocalPlaywrightRunnerError as exc:
        issues = _issues_from_exception(exc)
        return [
            _invalid_execution_item(
                prepared.candidate,
                ir_path=prepared.ir_path,
                generated_spec_path=prepared.generated_spec_path,
                artifacts_dir=artifacts_dir,
                issues=issues,
            )
            for prepared in prepared_candidates
        ]

    report_json_path = run.paths.artifacts_dir / "results.json"
    html_report_dir = run.paths.html_report_dir
    report_json_path_value = str(report_json_path) if report_json_path.exists() else None
    html_report_dir_value = str(html_report_dir) if html_report_dir.exists() else None
    case_results, report_issues = _load_playwright_case_results(report_json_path)

    results: list[ExecutionRunItem] = []
    for prepared in prepared_candidates:
        candidate = prepared.candidate
        case_result = case_results.get(candidate.id)
        if case_result:
            result_status = case_result.status
            result_stdout = case_result.stdout or run.stdout
            result_stderr = case_result.stderr or run.stderr
            result_issues = list(case_result.issues)
        else:
            result_status = "invalid"
            result_stdout = run.stdout
            result_stderr = run.stderr
            result_issues = [
                *report_issues,
                ExecutionIssue(
                    path="$",
                    message="No matching Playwright JSON result was found for this execution candidate.",
                    code="playwright.result_missing",
                ),
            ]

        results.append(
            ExecutionRunItem(
                id=candidate.id,
                source_test_case_id=candidate.source_test_case_id,
                title=candidate.title,
                status=result_status,
                spec_id=candidate.id,
                ir_path=str(prepared.ir_path),
                generated_spec_path=str(prepared.generated_spec_path),
                artifacts_dir=str(run.paths.artifacts_dir),
                report_json_path=report_json_path_value,
                playwright_report_path=html_report_dir_value,
                returncode=run.returncode,
                stdout=result_stdout,
                stderr=result_stderr,
                issues=result_issues,
            )
        )

    return results


def _invalid_execution_item(
    candidate: ExecutionCandidate,
    *,
    ir_path: Path,
    artifacts_dir: Path,
    issues: list[ExecutionIssue],
    generated_spec_path: Path | None = None,
) -> ExecutionRunItem:
    return ExecutionRunItem(
        id=candidate.id,
        source_test_case_id=candidate.source_test_case_id,
        title=candidate.title,
        status="invalid",
        spec_id=candidate.id,
        ir_path=str(ir_path) if ir_path.exists() else None,
        generated_spec_path=str(generated_spec_path) if generated_spec_path else None,
        artifacts_dir=str(artifacts_dir),
        issues=issues,
    )


def _load_playwright_case_results(report_json_path: Path) -> tuple[dict[str, _PlaywrightCaseResult], list[ExecutionIssue]]:
    if not report_json_path.exists():
        return {}, [
            ExecutionIssue(
                path="$",
                message=f"Playwright JSON report was not found at {report_json_path}.",
                code="playwright.report_missing",
            )
        ]

    try:
        report = json.loads(report_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [
            ExecutionIssue(
                path="$",
                message=f"Playwright JSON report could not be parsed: {exc}.",
                code="playwright.report_parse",
            )
        ]
    if not isinstance(report, dict):
        return {}, [
            ExecutionIssue(
                path="$",
                message="Playwright JSON report root must be an object.",
                code="playwright.report_type",
            )
        ]

    results: dict[str, _PlaywrightCaseResult] = {}
    for test in _iter_playwright_tests(report):
        case_id = _annotation_description(test, "caseId") or _annotation_description(test, "specId")
        if not case_id:
            continue
        results[case_id] = _playwright_case_result(test)
    return results, []


def _iter_playwright_tests(report: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for suite in report.get("suites") or []:
        yield from _iter_playwright_suite_tests(suite)


def _iter_playwright_suite_tests(suite: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for spec in suite.get("specs") or []:
        for test in spec.get("tests") or []:
            yield test
    for child_suite in suite.get("suites") or []:
        yield from _iter_playwright_suite_tests(child_suite)


def _annotation_description(test: dict[str, Any], annotation_type: str) -> str | None:
    annotations = list(test.get("annotations") or [])
    for result in test.get("results") or []:
        annotations.extend(result.get("annotations") or [])

    for annotation in annotations:
        if annotation.get("type") == annotation_type and annotation.get("description"):
            return str(annotation["description"])
    return None


def _playwright_case_result(test: dict[str, Any]) -> _PlaywrightCaseResult:
    results = list(test.get("results") or [])
    issues = tuple(_playwright_issues(results))
    return _PlaywrightCaseResult(
        status="invalid" if any(issue.code == "execution.target_origin_mismatch" for issue in issues) else _playwright_status(test),
        stdout=_playwright_stdio_text(results, "stdout"),
        stderr=_playwright_stdio_text(results, "stderr"),
        issues=issues,
    )


def _playwright_status(test: dict[str, Any]) -> str:
    test_status = str(test.get("status") or "").strip()
    result_statuses = {str(result.get("status") or "").strip() for result in test.get("results") or []}

    if test_status == "skipped" or result_statuses == {"skipped"}:
        return "skipped"
    if test_status in {"expected", "flaky"}:
        return "passed"
    if "passed" in result_statuses and not (result_statuses & {"failed", "timedOut", "interrupted"}):
        return "passed"
    return "failed"


def _playwright_stdio_text(results: list[dict[str, Any]], field: str) -> str:
    parts: list[str] = []
    for result in results:
        for entry in result.get(field) or []:
            if isinstance(entry, dict):
                if "text" in entry:
                    parts.append(str(entry["text"]))
                elif "buffer" in entry:
                    parts.append(str(entry["buffer"]))
    return "".join(parts)


def _playwright_issues(results: list[dict[str, Any]]) -> list[ExecutionIssue]:
    issues: list[ExecutionIssue] = []
    for index, result in enumerate(results):
        errors = result.get("errors") or []
        entries = [(f"errors[{error_index}]", error) for error_index, error in enumerate(errors)]
        if not entries and result.get("error"):
            entries = [("error", result["error"])]
        for path, error in entries:
            message = _playwright_error_message(error)
            escaped_target = message.removeprefix("Error: ").startswith(TARGET_ORIGIN_ERROR)
            issues.append(
                ExecutionIssue(
                    path=f"$.results[{index}].{path}",
                    message=message,
                    code="execution.target_origin_mismatch" if escaped_target else "playwright.error",
                )
            )
    return issues


def _playwright_error_message(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("value") or error)
    return str(error)


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
    return [candidate for candidate in candidates if candidate.source_test_case_id in selected_ids or candidate.id in selected_ids]


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


def _unmapped_step(step: TestStep, text: str, *, is_expected: bool) -> ExecutionUnsupportedStep:
    if _contains_ambiguous_semantic_visible_assertion(text):
        return _unsupported_step(
            step,
            "ambiguous_semantic_assertion",
            'Use an exact accessible name such as heading "Dashboard" should be visible, or keep the step manual.',
        )
    if reason := _unsupported_browser_assertion_text_reason(text):
        return _unsupported_step(step, "unsupported_browser_assertion", reason)
    if is_expected:
        return _unsupported_step(
            step,
            "unsupported_expected_result",
            "Map the entire expected result to an observable browser assertion. Separate independent checks into steps or verify this result manually.",
        )
    return _unsupported_step(step, "unsupported_step", "Use a supported browser action with a grounded target, or keep the step manual.")


def _unsupported_browser_assertion_text_reason(text: str) -> str | None:
    if not text or _convert_assertion(text):
        return None

    if _contains_runtime_assertion_intent(text):
        return "Runtime, CLI, stdout/stderr, and exception assertions are not browser DOM assertions; keep them manual or add runtime-level checks."

    if _contains_page_title_visible_assertion(text):
        return 'Use page title should be "Exact Title" instead of asserting page title text is visible.'

    if _contains_attribute_visible_assertion(text):
        return 'Use css "selector" attribute "name" should equal "value" or test id "id" attribute "name" should equal "value".'

    if STATE_ASSERTION_PATTERN.search(text) and _looks_like_assertion_statement(text):
        return (
            'Use a supported explicit assertion such as css "#id" should be checked, '
            'css "#id" should be disabled, css "a" attribute "href" should equal "/path", '
            'or css ".item" count should be 3.'
        )

    return None


def _contains_ambiguous_semantic_visible_assertion(text: str) -> bool:
    if not text or not re.search(r"\b(displays?|displayed|visible|shown|present|appears)\b", text, re.IGNORECASE):
        return False

    if ROLE_VISIBLE_PATTERN.search(text):
        return False

    for match in LOCATOR_VISIBLE_PATTERN.finditer(text):
        if _locator_assertion_body(match.group("kind"), match.group("value")):
            return False

    quoted_values = QUOTED_PATTERN.findall(text)
    if any(is_ambiguous_semantic_visible_text(_clean_value(value)) for value in quoted_values):
        return True

    if match := VISIBLE_PATTERN.search(text):
        visible_text = _clean_visible_text(match.group("text"))
        return is_ambiguous_semantic_visible_text(visible_text)

    return False


def _contains_runtime_assertion_intent(text: str) -> bool:
    if _has_supported_locator_visible_assertion(text):
        return False
    return bool(RUNTIME_ASSERTION_PATTERN.search(text) and _looks_like_assertion_statement(text))


def _contains_page_title_visible_assertion(text: str) -> bool:
    if not re.search(r"\b(displays?|displayed|visible|shown|present|appears)\b", text, re.IGNORECASE):
        return False
    if _has_supported_locator_visible_assertion(text):
        return False
    visible_values = [_clean_value(value).lower() for value in QUOTED_PATTERN.findall(text)]
    if any("page title" in value or "title mismatch" in value or "incorrect page title" in value for value in visible_values):
        return True
    return bool(re.search(r"\bpage title\b", text, re.IGNORECASE))


def _contains_attribute_visible_assertion(text: str) -> bool:
    if not re.search(r"\b(displays?|displayed|visible|shown|present|appears)\b", text, re.IGNORECASE):
        return False
    if _has_supported_locator_visible_assertion(text):
        return False
    attribute_names = {"aria-label", "class", "data-testid", "href", "id", "name", "role", "src", "value"}
    quoted_values = {_clean_value(value).lower() for value in QUOTED_PATTERN.findall(text)}
    return bool(quoted_values & attribute_names)


def _has_supported_locator_visible_assertion(text: str) -> bool:
    for match in LOCATOR_VISIBLE_PATTERN.finditer(text):
        if _locator_assertion_body(match.group("kind"), match.group("value")):
            return True
    return False


def _looks_like_assertion_statement(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:assert|confirm|displayed|equals?|expect|expected|present|shown|should|verify|visible)\b",
            text,
            re.IGNORECASE,
        )
    )


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
    if match := QUOTED_NAVIGATION_ARGUMENT.search(action):
        return _assertion_literal(match["url"])
    if match := BARE_NAVIGATION_ARGUMENT.search(action):
        return match["url"].rstrip(".,;:!?")
    if url := _extract_prose_url(action):
        return url
    return base_url


def _url_step(kind: str, url: str) -> _ConvertedStep:
    prefix = "I open" if kind == "navigate" else "URL should be"
    return _ConvertedStep(kind, f'{prefix} "{_escape_step_text(url)}"', url)


def _bind_url_step(step: _ConvertedStep, *, base_url: str, source_app_url: str | None) -> _ConvertedStep:
    if step.url is None:
        return step
    return _url_step(step.kind, bind_execution_url(step.url, base_url=base_url, source_app_url=source_app_url))


def _extract_prose_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    if match is None:
        return None
    # Bare URLs in sentences can end at punctuation. Delimit a URL when
    # punctuation is part of the address.
    return match.group("quoted") or match.group("angled") or match.group("bare").rstrip(".,;:!?")


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


def _clean_locator_value(value: str) -> str:
    normalized = _normalize_text(value).strip()
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


def _locator_assertion_body(kind: str | None, value: str) -> str | None:
    locator_value = _clean_locator_value(value)
    if not locator_value:
        return None

    locator_kind = _normalize_locator_kind(kind)
    if locator_kind == "test id":
        return f'test id "{_escape_step_text(locator_value)}"'
    if locator_kind == "css" or _looks_like_selector_value(locator_value):
        return f'css "{_escape_step_text(locator_value)}"'
    return None


def _normalize_locator_kind(kind: str | None) -> str | None:
    normalized = re.sub(r"[\s_-]+", " ", str(kind or "").strip().lower())
    if not normalized:
        return None
    if normalized in {"test id", "data testid", "data-testid"}:
        return "test id"
    if normalized in {"css", "selector"}:
        return "css"
    return None


def _looks_like_selector_value(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if normalized.startswith(("#", ".", "[", ":", ">")):
        return True
    if re.search(r"\[[^\]]+\]", normalized):
        return True
    if re.search(r"^[a-zA-Z][\w-]*(?:[#.][\w-]+)+$", normalized):
        return True
    return bool(re.search(r"^[a-zA-Z][\w-]*\s+[.#\[]", normalized))


def _escape_step_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _slug_identifier(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    if not slug:
        slug = "case"
    if not slug[0].isalpha():
        slug = f"tc_{slug}"
    return slug
