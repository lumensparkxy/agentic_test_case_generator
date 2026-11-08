"""Check a new test case's browser handoff without opening the application."""

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import HttpUrl, ValidationError

from plain_english_test_framework.compiler import CompilerError, ResolvedEnvironment, compile_parsed_spec
from plain_english_test_framework.ir_validator import IrValidationError
from plain_english_test_framework.spec_parser import SpecValidationError, parse_spec_document

from ..contracts.grounding import EnrichInput
from ..contracts.execution import ExecutionCandidate
from ..contracts.requirements import Requirement
from ..contracts.test_cases import TestCase
from .execution_service import preview_execution

BROWSER_AUTHORING_GUIDE = """Browser authoring contract for cases with a supplied app target and observed interface:
- Use one browser action and at most one observable check per source step. Split independent outcomes into separate steps.
- Start with action 'Open "exact supplied URL"'. Leave expected as an empty string on a pure navigation or interaction step when it has no separate source outcome.
- Use action 'Enter "literal input" into the "observed accessible label" field' and expected 'field "observed accessible label" value should equal "literal input"' when the field value matters.
- For an initially blank field, use action 'Inspect the "observed accessible label" field' and expected 'field "observed accessible label" value should equal ""'. Submit with no fill step. Empty fills are unsupported. Keep a required clear of a populated field manual.
- Use action 'Click exact button "observed accessible name"' or 'Click exact link "observed accessible name"'.
- Write checks as complete statements. Use expected 'exact text "source copy" is visible', 'exact heading "observed heading" is visible', 'exact button "observed button" is visible', or 'URL should be "exact supplied URL"'. An observation action such as 'Inspect the status output' may carry a separate expected check.
- The wording above illustrates syntax. Use the actual supplied names, routes and copy. Do not add generic page-load checks or combine a result with phrases such as "and the results update". Preserve any separate source-defined transition as its own observable check.
- Every business expectation must appear in a source step. Use expected_result to summarize those step outcomes. Keep required backend, audit, timing or unsupported checks in the case and mark it Manual. Missing interface evidence or prerequisites also means Manual.
- Use "To Be Automated" only when the browser target, all locators, and every step expectation are supported. Generation does not execute a test.
"""

_QUOTED = re.compile(r""""([^"\n]*)"|(?<!\w)'([^'\n]*)'(?!\w)""")
_DECLARED_LOCATOR = re.compile(r'(css(?: selector)?|test id)\s+"([^"\n]+)"', re.IGNORECASE)
_NOTED_CONTROL = re.compile(r'\b(field|heading|button|link)\s+"([^"\n]+)"|"([^"\n]+)"\s+(field|heading|button|link)\b', re.IGNORECASE)
_OPEN = re.compile(r'(?:Open|Navigate to)\s+"([^"\n]+)"\.?', re.IGNORECASE)
_ROLE_TYPES = {"heading": "Heading", "button": "Button", "link": "Navigation"}
_CONTROL_TYPES = {"field": "Field", **_ROLE_TYPES}
_DIAGNOSTIC_LIMIT = 10


@dataclass(frozen=True)
class _BrowserEvidence:
    target: str
    urls: set[str]
    names_by_type: dict[str, set[str]]
    text_literals: set[str]
    declared_locators: set[tuple[str, str]]


def _http_url(value: str) -> str | None:
    try:
        url = HttpUrl(value)
    except ValidationError:
        return None
    return str(url) if not (url.username or url.password) else None


def _evidence(context: EnrichInput | None, requirements: list[Requirement]) -> _BrowserEvidence | None:
    if not context:
        return None
    grounded = context.grounded_context
    app_urls = {}
    for source in grounded.artifact_sources if grounded else []:
        if source.source_type == "app" and source.status == "Analyzed" and source.url:
            url = _http_url(str(source.url))
            if url:
                app_urls[source.id] = url
    target = _http_url(str(context.app_link)) if context.app_link else None
    if not context.app_link:
        unique_urls = set(app_urls.values())
        if len(unique_urls) == 1:
            target = unique_urls.pop()
    if not target:
        return None

    origin = urlsplit(target)[:2]
    source_urls = {source_id: url for source_id, url in app_urls.items() if urlsplit(url)[:2] == origin}
    urls = {target, *source_urls.values()}
    names_by_type: dict[str, set[str]] = {}
    for element in grounded.ui_elements if grounded else []:
        if element.source_id is not None and element.source_id not in source_urls:
            continue
        if element.element_type == "Field" and element.description.startswith("Field identifier extracted from artifact:"):
            continue  # A DOM identifier alone does not establish the accessible label.
        names_by_type.setdefault(element.element_type, set()).add(element.name)
        if element.href:
            try:
                href = _http_url(urljoin(source_urls.get(element.source_id, target), element.href))
            except ValueError:
                continue
            if href and urlsplit(href)[:2] == origin:
                urls.add(href)

    notes = context.notes or ""
    for leading, name_after, name_before, trailing in _NOTED_CONTROL.findall(notes):
        names_by_type.setdefault(_CONTROL_TYPES[(leading or trailing).lower()], set()).add(name_after or name_before)
    source_text = [notes, *(text for item in requirements for text in (item.text, item.source_excerpt or ""))]
    literals = {double or single for text in source_text for double, single in _QUOTED.findall(text)}
    literals.update(name for names in names_by_type.values() for name in names)
    declared_locators = {
        ("css" if kind.lower().startswith("css") else "test_id", value) for text in source_text for kind, value in _DECLARED_LOCATOR.findall(text)
    }
    return _BrowserEvidence(target, urls, names_by_type, literals, declared_locators)


def _locator_supported(locator: dict[str, Any], evidence: _BrowserEvidence) -> bool:
    strategy, value = locator["strategy"], locator["value"]
    if strategy == "role":
        return value in evidence.names_by_type.get(_ROLE_TYPES.get(locator.get("role"), ""), ())
    if strategy == "label":
        return any(value in evidence.names_by_type.get(kind, ()) for kind in ("Field", "Message", "Other"))
    if strategy == "text":
        return value in evidence.text_literals
    return (strategy, value) in evidence.declared_locators


def _verify_candidate(case: TestCase, candidate: ExecutionCandidate | None, evidence: _BrowserEvidence) -> str | None:
    if not candidate:
        return "The case was outside the deterministic preview scope."
    if candidate.status != "executable" or not candidate.spec:
        if candidate.unsupported_steps:
            issue = candidate.unsupported_steps[0]
            return f"Source step {issue.step} could not be mapped to a complete browser check ({issue.reason_code})."
        return "The deterministic preview could not verify the steps or execution prerequisites."
    opening = _OPEN.fullmatch(case.steps[0].action.strip())
    if not opening or _http_url(opening[1]) not in evidence.urls:
        return "Start with an explicit navigation to a supplied application URL."
    try:
        ir = compile_parsed_spec(
            parse_spec_document(candidate.spec),
            environment=ResolvedEnvironment("web", {"baseUrl": evidence.target}, evidence.target, None),
        )
    except CompilerError, IrValidationError, SpecValidationError:
        return "The preview could not be compiled into a valid browser specification."

    steps = ir.raw["cases"][0]["steps"]
    source_navigations = [_http_url(match[1]) for step in case.steps if (match := _OPEN.fullmatch(step.action.strip()))]
    compiled_navigations = [_http_url(step["url"]) for step in steps if step["action"] == "navigate"]
    if source_navigations != compiled_navigations:
        return "Each browser navigation must use its own explicit action and supplied URL."

    entered_values: dict[tuple[str, str], str] = {}
    for step in steps:
        action = step["action"]
        if action in {"navigate", "assert_url"} and _http_url(step["url"]) not in evidence.urls:
            return "A browser URL lacks application evidence."
        if action == "navigate":
            entered_values.clear()
        locator = step.get("locator")
        if locator and not _locator_supported(locator, evidence):
            return "A browser locator or visible text lacks exact interface evidence."
        if action == "click" and not locator.get("exact"):
            return "Use a complete exact button or link action."
        if action == "fill":
            entered_values[(locator["strategy"], locator["value"])] = str(step["value"])
        if action in {"assert_text_equals", "assert_title", "assert_attribute_equals", "assert_value_equals", "assert_value_contains"}:
            expected = step["expected"]
            controlled_value = False
            if action in {"assert_value_equals", "assert_value_contains"}:
                entered = entered_values.get((locator["strategy"], locator["value"]), "")
                controlled_value = expected == entered if action == "assert_value_equals" else expected in entered
            if expected != "" and expected not in evidence.text_literals and not controlled_value:
                return "A browser assertion value lacks exact source or controlled input evidence."
    return None


def validate_browser_handoff(cases: list[TestCase], context: EnrichInput | None, requirements: list[Requirement]) -> list[str]:
    """Set browser intent on new output only. Business approval remains independent."""
    intended = [case for case in cases if case.automation_status != "Manual"]
    if not intended:
        return []
    evidence = _evidence(context, requirements)
    identity_counts = Counter(case.id for case in cases)
    candidates = {}
    if evidence:
        preview = preview_execution([case for case in intended if identity_counts[case.id] == 1], target_base_url=evidence.target)
        candidates = {
            candidate.source_test_case_id: candidate
            for group in (preview.executable, preview.manual, preview.unsupported, preview.invalid)
            for candidate in group
        }

    manual_count = 0
    details: list[str] = []
    for case in intended:
        if not evidence:
            reason = "No supplied application target could be verified."
        elif identity_counts[case.id] != 1:
            reason = "The case ID is ambiguous within this suite."
        else:
            reason = _verify_candidate(case, candidates.get(case.id), evidence)
        case.automation_status = "Manual" if reason else "To Be Automated"
        if reason:
            manual_count += 1
            if len(details) < _DIAGNOSTIC_LIMIT:
                details.append(f"Browser handoff for {case.id[:80]} needs manual review. {reason}")

    if not manual_count:
        return []
    return [f"Browser handoff retained {manual_count} of {len(intended)} browser-intended cases for manual review.", *details]
