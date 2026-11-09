"""Check a new test case's browser handoff without opening the application."""

from collections import Counter
from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import HttpUrl, ValidationError

from plain_english_test_framework.compiler import CompilerError, ResolvedEnvironment, compile_parsed_spec
from plain_english_test_framework.ir_validator import IrValidationError
from plain_english_test_framework.semantic_assertions import QUOTED_BROWSER_LOCATOR, unquote_browser_locator
from plain_english_test_framework.spec_parser import SpecValidationError, parse_spec_document

from ..contracts.grounding import EnrichInput
from ..contracts.execution import ExecutionCandidate
from ..contracts.requirements import Requirement
from ..contracts.test_cases import TestCase
from .execution_service import EXPLICIT_OPEN_ACTION, LOCATOR_ATTRIBUTE_EQUALS_PATTERN, preview_execution

BROWSER_AUTHORING_GUIDE = """Browser authoring contract for cases with a supplied app target and observed interface:
- Use one browser action and at most one observable check per source step. Split independent outcomes into separate steps.
- Start with action 'Open "exact supplied URL"'. Leave expected as an empty string on a pure navigation or interaction step when it has no separate source outcome.
- Use action 'Enter "literal input" into field "observed accessible label"' and expected 'field "observed accessible label" value should equal "literal input"' when the field value matters. Preserve spaces in quoted values. Escape double quotes and backslashes inside the value.
- For multiline or brace-sensitive input, put the exact string in the step's test_data. Use action 'Enter step data into field "observed accessible label"' and expected 'field "observed accessible label" value should equal step data'. For an exact saved-text comparison, put the expected string on that observation step and use 'css "#saved-value" text should equal step data'. Use synthetic data or credential prerequisites.
- For a Context declaration 'Browser sample "sample-name" is "JSON-encoded string"', refer to that name in 'Enter browser sample "sample-name" into field "observed accessible label"', 'field "observed accessible label" value should equal browser sample "sample-name"', or 'test id "saved-value" text should equal browser sample "sample-name"'. Set test_data to null for named samples. The portal saves the declared literal and validates the resulting step. Use one sample per step and only supplied names.
- For a native dropdown with grounded select_options, use action 'Select exact option "observed option label" in field "observed accessible label"'. Leave expected empty for a selection with no separate source outcome and verify the business result in its own check. A field-value assertion needs the supplied DOM value, which may differ from the label. Keep unavailable or unsupported options manual.
- Context lines beginning with 'Browser page' or 'Browser dropdown' may supply observed same-origin URLs and exact native option labels on a named page. Use only the declared page and choices.
- For an available native checkbox with grounded checkbox_available, use action 'Check exact checkbox "observed accessible label"' or 'Uncheck exact checkbox "observed accessible label"'. Assert 'exact checkbox "observed accessible label" is checked' or 'exact checkbox "observed accessible label" is not checked' for a required state. Verify saved behavior separately.
- For an available native radio with grounded radio_available, use 'Select exact radio "observed option label"'. Assert 'exact radio "observed option label" is selected' or 'exact radio "observed option label" is not selected'. Check the prior option separately if it must clear. Verify a saved choice after reopening the supplied page.
- To clear a populated field, use action 'Enter "" into field "observed accessible label"' and check the empty value. For an initially blank field, inspect it and check the empty value without a fill step.
- Use action 'Click exact button "observed accessible name"' or 'Click exact link "observed accessible name"'.
- Write checks as complete statements. Use expected 'exact text "source copy" is visible', 'exact heading "observed heading" is visible', 'exact button "observed button" is visible', or 'URL should be "exact supplied URL"'. An observation action such as 'Inspect the status output' may carry a separate expected check.
- When the same text can appear in multiple elements, scope its check to a declared unique locator. Use expected 'css "#observed-status" text should equal "source copy"' or 'test id "observed-status" text should equal "source copy"'. Match the full observed text, including spaces. Use a separate visibility check when visibility itself is required. Never choose the first match or invent a locator.
- Use the exact observed CSS or test-ID selector for visibility, checked state, enabled state, or attribute equality. Examples include 'test id "saved-order" is visible' and 'css "#receipt" attribute "data-status" should equal "Saved"'. Preserve spaces and escapes in the selector and attribute value. Ground a specific attribute and value with the same explicit check in Context.
- To verify an exact count, use 'css "#orders tr" count should be 1' or 'test id "rejected-order" count should be 0'. Use the exact supplied selector and keep its whitespace and CSS escapes. Use different outer quotes when the selector contains quotes. For an asynchronous collection, first check its supplied loaded indicator in a separate step.
- For a source requirement that says a saved record displays submitted values, check each value on that same record after submission. A form value or confirmation alone leaves this outcome untested. A 'Browser result css "#saved-value" on "https://app.example/" shows field "Name" after button "Save"' declaration supplies an observed result locator. Use a scoped text check with the exact prior selected label or entered literal. For a text entry, include its exact field-value check before submission.
- A 'Browser rejection "Invalid input" on "https://app.example/" after button "Save" preserves css "#records tr" when exact heading "My records" is visible' declaration supplies an observed collection and its loaded indicator. Check the indicator, then assert the grounded starting count with expected 'css "#records tr" count should be 1' before the named submit action. Assert the exact error, then reopen the same URL with 'Open "https://app.example/"'. Check the indicator and the same count in that fresh page. Use the actual supplied selector and actor's starting count.
- For a named role or exact text that should be out of view, write 'exact link "observed link" is not visible' or 'exact text "source copy" is not visible'. Use a separate step at the state where visibility matters. This check allows hidden and absent matches. Keep DOM presence, timing, and authorization requirements separate.
- The wording above illustrates syntax. Use the actual supplied names, routes and copy. Do not add generic page-load checks or combine a result with phrases such as "and the results update". Preserve any separate source-defined transition as its own observable check.
- Every business expectation must appear in a source step. Use expected_result to summarize those step outcomes. Keep required backend, audit, timing or unsupported checks in the case and mark it Manual. Missing interface evidence or prerequisites also means Manual.
- Use "To Be Automated" only when the browser target, all locators, and every step expectation are supported. Generation does not execute a test.
"""

_QUOTED = re.compile(r""""([^"\n]*)"|(?<!\w)'([^'\n]*)'(?!\w)""")
_DECLARED_LOCATOR = re.compile(rf"\b(css(?: selector)?|test id)\s+({QUOTED_BROWSER_LOCATOR})", re.IGNORECASE)
_NOTED_CONTROL = re.compile(r'\b(field|heading|button|link)\s+"([^"\n]+)"|"([^"\n]+)"\s+(field|heading|button|link)\b', re.IGNORECASE)
_NOTED_PAGE = re.compile(r'Browser page "([^"\n]+)"', re.IGNORECASE)
_NOTED_DROPDOWN = re.compile(r'Browser dropdown "([^"\n]+)" on "([^"\n]+)" options (.+)', re.IGNORECASE)
_NOTED_RESULT = re.compile(r'Browser result (css|test id) "([^"\n]+)" on "([^"\n]+)" shows field "([^"\n]+)" after button "([^"\n]+)"', re.IGNORECASE)
_NOTED_REJECTION = re.compile(
    rf'Browser rejection "([^"\n]+)" on "([^"\n]+)" after button "([^"\n]+)" preserves (css|test id) ({QUOTED_BROWSER_LOCATOR}) '
    r'when exact (heading|text) "([^"\n]+)" is visible',
    re.IGNORECASE,
)
_DROPDOWN_DOM_VALUE = re.compile(r'\bfield\s+"([^"\n]+)"\s+DOM value\s+(?:is\s+)?"([^"\n]*)"', re.IGNORECASE)
_ROLE_TYPES = {"heading": "Heading", "button": "Button", "link": "Navigation", "radio": "Field"}
_CONTROL_TYPES = {"field": "Field", **_ROLE_TYPES}
_DIAGNOSTIC_LIMIT = 10


@dataclass(frozen=True)
class _BrowserEvidence:
    target: str
    urls: set[str]
    names_by_type: dict[str, set[str]]
    text_literals: set[str]
    declared_locators: set[tuple[str, str]]
    attribute_values: dict[tuple[str, str], set[tuple[str, str]]]
    select_options: dict[tuple[str, str], set[str]]
    select_dom_values: dict[str, set[str]]
    checkboxes: dict[tuple[str, str], bool]
    radios: dict[tuple[str, str], bool | None]
    result_fields: dict[tuple[str, str, str], tuple[str, str]]


@dataclass(frozen=True)
class RejectionReadback:
    message: str
    page: str
    button: str
    kind: str
    selector: str
    ready_kind: str
    ready_name: str


def _http_url(value: str) -> str | None:
    try:
        url = HttpUrl(value)
    except ValidationError:
        return None
    return str(url) if not (url.username or url.password) else None


def declared_browser_pages(notes: str, app_link: str | None) -> set[str]:
    target = _http_url(app_link) if app_link else None
    if not target:
        return set()
    origin = urlsplit(target)[:2]
    pages = set()
    for line in notes.splitlines():
        if match := _NOTED_PAGE.fullmatch(line.strip()):
            page = _http_url(match[1])
            if page and urlsplit(page)[:2] == origin:
                pages.add(page)
    return pages


def declared_rejection_readbacks(notes: str, app_link: str | None) -> list[RejectionReadback]:
    target = _http_url(app_link) if app_link else None
    if not target:
        return []
    pages = {target, *declared_browser_pages(notes, target)}
    declarations = []
    for line in notes.splitlines():
        if match := _NOTED_REJECTION.fullmatch(line.strip()):
            message, raw_page, button, kind, quoted_selector, ready_kind, ready_name = match.groups()
            selector = unquote_browser_locator(quoted_selector)
            page = _http_url(raw_page)
            if page in pages and all(value.strip() for value in (message, button, selector, ready_name)):
                declarations.append(RejectionReadback(message, page, button, kind.lower(), selector, ready_kind.lower(), ready_name))
    return list(dict.fromkeys(declarations))


def _compiled_steps(candidate: ExecutionCandidate, target: str) -> list[dict[str, Any]]:
    return compile_parsed_spec(parse_spec_document(candidate.spec), environment=ResolvedEnvironment("web", {"baseUrl": target}, target, None)).raw["cases"][0][
        "steps"
    ]


def _rejection_has_missing_readback(steps: list[dict[str, Any]], rule: RejectionReadback) -> bool:
    locator = {"strategy": "css" if rule.kind == "css" else "test_id", "value": rule.selector}
    ready = {"strategy": "text", "value": rule.ready_name, "exact": True}
    if rule.ready_kind == "heading":
        ready.update(strategy="role", role="heading")

    def ready_count(page_steps):
        ready_at = next((index for index, step in enumerate(page_steps) if step["action"] == "assert_visible" and step.get("locator") == ready), None)
        if ready_at is None:
            return None
        return next((step["expected"] for step in page_steps[ready_at + 1 :] if step["action"] == "assert_count" and step.get("locator") == locator), None)

    boundary_actions = {"navigate", "click"}
    boundaries = [index for index, step in enumerate(steps) if step["action"] in boundary_actions]
    for index, step in enumerate(steps):
        error_locator = step.get("locator", {})
        error = step["action"] == "assert_visible" and error_locator.get("strategy") == "text" and error_locator.get("value") == rule.message
        error = error or (step["action"] in {"assert_text_equals", "assert_scoped_text_equals"} and step.get("expected") == rule.message)
        if not error:
            continue
        submit_index = next((at for at in reversed(boundaries) if at < index), None)
        submit_locator = steps[submit_index].get("locator", {}) if submit_index is not None else {}
        if submit_locator.get("role") != "button" or submit_locator.get("value") != rule.button:
            continue
        opened = next((at for at in range(submit_index - 1, -1, -1) if steps[at]["action"] == "navigate"), None)
        if opened is None or _http_url(steps[opened]["url"]) != rule.page:
            continue
        before_start = next(at for at in reversed(boundaries) if at < submit_index)
        before = ready_count(steps[before_start + 1 : submit_index])
        later = [at for at in boundaries if at > index]
        if before is None or not later or steps[later[0]]["action"] != "navigate" or _http_url(steps[later[0]]["url"]) != rule.page:
            return True
        end = later[1] if len(later) > 1 else len(steps)
        after = ready_count(steps[later[0] + 1 : end])
        if after != before:
            return True
    return False


def missing_rejection_readbacks(case: TestCase, app_link: str, rules: list[RejectionReadback]) -> list[RejectionReadback]:
    relevant = [rule for rule in rules if any(rule.message in step.expected for step in case.steps)]
    if not relevant or case.automation_status == "Manual":
        return []
    preview = preview_execution([case], target_base_url=app_link)
    if not preview.executable:
        return []
    try:
        steps = _compiled_steps(preview.executable[0], app_link)
    except CompilerError, IrValidationError, SpecValidationError:
        return []
    return [rule for rule in relevant if _rejection_has_missing_readback(steps, rule)]


def _exact_option_labels(raw: str) -> set[str]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError, RecursionError:
        return set()
    if not isinstance(values, list) or not 1 <= len(values) <= 40:
        return set()
    if any(not isinstance(value, str) or not value.strip() or value.strip() != value or "\n" in value or "\r" in value for value in values):
        return set()
    labels = set(values)
    return labels if len(labels) == len(values) else set()


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
    select_options: dict[tuple[str, str], set[str]] = {}
    other_native_fields: set[tuple[str, str]] = set()
    checkboxes: dict[tuple[str, str], bool] = {}
    radios: dict[tuple[str, str], bool | None] = {}
    for element in grounded.ui_elements if grounded else []:
        if element.source_id is not None and element.source_id not in source_urls:
            continue
        if element.element_type == "Field" and element.description.startswith("Field identifier extracted from artifact:"):
            continue  # A DOM identifier alone does not establish the accessible label.
        names_by_type.setdefault(element.element_type, set()).add(element.name)
        if element.element_type == "Field" and element.select_options is not None:
            key = (source_urls.get(element.source_id, target), element.name)
            select_options[key] = set(element.select_options) if key not in select_options else set()
        elif element.element_type == "Field" and element.source_id in source_urls:
            other_native_fields.add((source_urls[element.source_id], element.name))
        if element.element_type == "Field" and element.checkbox_available is not None:
            key = (source_urls.get(element.source_id, target), element.name)
            checkboxes[key] = element.checkbox_available and key not in checkboxes
        if element.element_type == "Field" and element.radio_available is not None:
            key = (source_urls.get(element.source_id, target), element.name)
            radios[key] = element.radio_available if key not in radios else None
        if element.href:
            try:
                href = _http_url(urljoin(source_urls.get(element.source_id, target), element.href))
            except ValueError:
                continue
            if href and urlsplit(href)[:2] == origin:
                urls.add(href)

    notes = context.notes or ""
    urls.update(declared_browser_pages(notes, str(context.app_link) if context.app_link else None))
    for line in notes.splitlines():
        if match := _NOTED_DROPDOWN.fullmatch(line.strip()):
            field, raw_page, raw_options = match.groups()
            page = _http_url(raw_page)
            if page not in urls:
                continue
            key = (page, field)
            labels = _exact_option_labels(raw_options)
            names_by_type.setdefault("Field", set()).add(field)
            # Conflicting or malformed declarations cannot widen the selectable choices.
            if key in other_native_fields or key in checkboxes or (key in select_options and select_options[key] != labels):
                labels = set()
            select_options[key] = labels
    notes_lines = notes.splitlines()
    regular_notes = "\n".join(line for line in notes_lines if not line.strip().lower().startswith("browser result "))
    for leading, name_after, name_before, trailing in _NOTED_CONTROL.findall(regular_notes):
        names_by_type.setdefault(_CONTROL_TYPES[(leading or trailing).lower()], set()).add(name_after or name_before)
    source_text = [regular_notes, *(text for item in requirements for text in (item.text, item.source_excerpt or ""))]
    literals = {double or single for text in source_text for double, single in _QUOTED.findall(text)}
    literals.update(name for names in names_by_type.values() for name in names)
    declared_locators = {
        ("css" if kind.lower().startswith("css") else "test_id", unquote_browser_locator(quoted))
        for text in source_text
        for kind, quoted in _DECLARED_LOCATOR.findall(text)
    }
    attribute_values: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for text in source_text:
        for match in LOCATOR_ATTRIBUTE_EQUALS_PATTERN.finditer(text):
            if not match["kind"]:
                continue
            strategy = "css" if match["kind"].lower() in {"css", "selector"} else "test_id"
            locator = (strategy, unquote_browser_locator(match["value"]))
            if locator in declared_locators:
                attribute = unquote_browser_locator(match["attribute"])
                attribute_values.setdefault(locator, set()).add((attribute, unquote_browser_locator(match["expected"])))
    select_dom_values: dict[str, set[str]] = {}
    for text in source_text:
        for field, value in _DROPDOWN_DOM_VALUE.findall(text):
            select_dom_values.setdefault(field, set()).add(value)
    result_bindings: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for line in notes_lines:
        if match := _NOTED_RESULT.fullmatch(line.strip()):
            kind, selector, raw_page, field, button = match.groups()
            page = _http_url(raw_page)
            if page in urls and selector.strip() and field in names_by_type.get("Field", ()) and button in names_by_type.get("Button", ()):
                strategy = "css" if kind.lower() == "css" else "test_id"
                result_bindings.setdefault((page, strategy, selector), set()).add((button, field))
    result_fields = {key: next(iter(bindings)) for key, bindings in result_bindings.items() if len(bindings) == 1}
    return _BrowserEvidence(
        target, urls, names_by_type, literals, declared_locators, attribute_values, select_options, select_dom_values, checkboxes, radios, result_fields
    )


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
    opening = EXPLICIT_OPEN_ACTION.fullmatch(case.steps[0].action.strip())
    if not opening or _http_url(opening[1]) not in evidence.urls:
        return "Start with an explicit navigation to a supplied application URL."
    try:
        steps = _compiled_steps(candidate, evidence.target)
    except CompilerError, IrValidationError, SpecValidationError:
        return "The preview could not be compiled into a valid browser specification."

    source_navigations = [_http_url(match[1]) for step in case.steps if (match := EXPLICIT_OPEN_ACTION.fullmatch(step.action.strip()))]
    compiled_navigations = [_http_url(step["url"]) for step in steps if step["action"] == "navigate"]
    if source_navigations != compiled_navigations:
        return "Each browser navigation must use its own explicit action and supplied URL."

    entered_values: dict[tuple[str, str], str] = {}
    pending_values: dict[str, str] = {}
    submission: tuple[str, dict[str, str]] | None = None
    current_page: str | None = evidence.target
    for step in steps:
        action = step["action"]
        if action in {"navigate", "assert_url"}:
            page = _http_url(step["url"])
            if page not in evidence.urls:
                return "A browser URL lacks application evidence."
            if action == "navigate" or current_page != page:
                entered_values.clear()
                pending_values.clear()
                submission = None
            current_page = page
        locator = step.get("locator")
        binding = evidence.result_fields.get((current_page, locator["strategy"], locator["value"])) if locator else None
        if locator and not binding and not _locator_supported(locator, evidence):
            return "A browser locator or visible text lacks exact interface evidence."
        if action == "click" and not locator.get("exact"):
            return "Use a complete exact button or link action."
        if action == "click":
            if locator.get("role") == "link":
                current_page = None
                entered_values.clear()
                submission = None
            else:
                submission = (locator["value"], pending_values)
            pending_values = {}
        if action == "select_option":
            if current_page is None or step["value"] not in evidence.select_options.get((current_page, locator["value"]), ()):
                return "A dropdown selection lacks an exact selectable option for this field and page."
            pending_values.pop(locator["value"], None)
            if "[[env:" not in step["value"]:
                pending_values[locator["value"]] = step["value"]
        if action == "set_checked" or (action == "assert_checked" and locator["strategy"] == "label"):
            availability = evidence.checkboxes.get((current_page, locator["value"])) if current_page is not None else None
            if availability is None:
                return "A checkbox step lacks an exact native checkbox on this page."
            if action == "set_checked" and not availability:
                return "The grounded native checkbox is unavailable for interaction."
        if action in {"select_radio", "assert_radio_selected"}:
            availability = evidence.radios.get((current_page, locator["value"])) if current_page is not None else None
            if availability is None:
                return "A radio step lacks an unambiguous native radio option on this page."
            if action == "select_radio" and not availability:
                return "The grounded native radio option is unavailable for selection."
        value_action = action in {"assert_value_equals", "assert_value_contains"}
        fill_action = action in {"fill", "fill_literal"}
        if fill_action or value_action:
            field = locator["value"]
            known_checkbox = locator["strategy"] == "label" and (
                (current_page, field) in evidence.checkboxes if current_page is not None else any(label == field for _, label in evidence.checkboxes)
            )
            if known_checkbox:
                return "A known checkbox needs a checked or unchecked operation or assertion."
            known_radio = locator["strategy"] == "label" and (
                (current_page, field) in evidence.radios if current_page is not None else any(label == field for _, label in evidence.radios)
            )
            if known_radio:
                return "A known radio option needs a selection or selected-state assertion."
            known_dropdown = (
                (current_page, field) in evidence.select_options if current_page is not None else any(label == field for _, label in evidence.select_options)
            )
            if fill_action:
                if known_dropdown:
                    return "A known dropdown needs an exact option selection instead of a text fill."
                entered_values[(locator["strategy"], field)] = str(step["value"])
                pending_values.pop(field, None)
                if locator["strategy"] == "label" and locator.get("exact") and (action == "fill_literal" or "[[env:" not in str(step["value"])):
                    pending_values[field] = str(step["value"])
            elif known_dropdown:
                expected = step["expected"]
                known_values = evidence.select_dom_values.get(field, ())
                verified = expected in known_values if action == "assert_value_equals" else any(expected in value for value in known_values)
                if not verified:
                    return "A dropdown field-value check lacks an explicitly declared DOM value."
                continue
        if value_action or action in {"assert_text_equals", "assert_scoped_text_equals", "assert_title", "assert_attribute_equals"}:
            expected = step["expected"]
            if action == "assert_attribute_equals":
                declared = evidence.attribute_values.get((locator["strategy"], locator["value"]))
                if declared is not None:
                    if (step["attribute"], expected) not in declared:
                        return "The attribute check does not match a declared attribute and value for this locator."
                    continue
            if action == "assert_scoped_text_equals" and binding and submission and submission[0] == binding[0] and binding[1] in submission[1]:
                if expected != submission[1][binding[1]]:
                    return "The saved-result text does not match the exact submitted input."
                continue
            controlled_value = False
            if value_action:
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
