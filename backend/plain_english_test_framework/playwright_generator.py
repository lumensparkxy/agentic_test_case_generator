"""Generate TypeScript Playwright Test specs from validated IR."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from plain_english_test_framework.ir_validator import ValidatedIr, parse_ir_document, parse_ir_file
from plain_english_test_framework.validation import ValidationIssue

TARGET_ORIGIN_ERROR = "Execution target origin mismatch:"
TARGET_ORIGIN_GUARD = """
async function protectExecutionTarget(page: Page, baseURL: string) {
  const selectedOrigin = new URL(baseURL).origin;
  const context = page.context();
  let observedOrigin: string | undefined;

  const recordMismatch = (url: string) => {
    if (url === "about:blank") return false;
    const parsed = new URL(url);
    if (parsed.protocol === "chrome-error:") return false;
    if (parsed.origin === selectedOrigin) return false;
    observedOrigin ??= parsed.origin === "null" ? parsed.protocol : parsed.origin;
    return true;
  };
  const isTopLevel = (request: Request) => {
    if (!request.isNavigationRequest()) return false;
    try {
      return request.frame().parentFrame() === null;
    } catch {
      // The browser can start a popup navigation before creating its frame.
      return true;
    }
  };
  const watchPage = (candidate: Page) => {
    recordMismatch(candidate.url());
    candidate.on("framenavigated", (frame) => {
      if (frame === candidate.mainFrame()) recordMismatch(frame.url());
    });
  };

  await context.route((url) => url.origin !== selectedOrigin, async (route) => {
    if (isTopLevel(route.request()) && recordMismatch(route.request().url())) {
      await route.abort("blockedbyclient");
    } else {
      await route.fallback();
    }
  });
  // Routing may only see the initial request in a redirect chain.
  context.on("request", (request) => {
    if (request.redirectedFrom() && isTopLevel(request)) recordMismatch(request.url());
  });
  context.on("page", watchPage);
  watchPage(page);

  return (url?: string) => {
    if (url) recordMismatch(url);
    recordMismatch(page.url());
    if (observedOrigin) {
      throw new Error(`__TARGET_ORIGIN_ERROR__ expected ${selectedOrigin}, observed ${observedOrigin}`);
    }
  };
}
""".strip().replace("__TARGET_ORIGIN_ERROR__", TARGET_ORIGIN_ERROR)

SUPPORTED_ACTIONS = {
    "navigate",
    "fill",
    "fill_literal",
    "select_option",
    "select_radio",
    "set_checked",
    "click",
    "assert_visible",
    "assert_hidden",
    "assert_text_equals",
    "assert_scoped_text_equals",
    "assert_value_equals",
    "assert_value_contains",
    "assert_url",
    "assert_title",
    "assert_checked",
    "assert_radio_selected",
    "assert_enabled",
    "assert_attribute_equals",
    "assert_count",
}


class PlaywrightGenerationError(Exception):
    """Raised when schema-valid IR cannot be represented as Playwright code."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class GeneratedPlaywrightSpec:
    """Generated TypeScript spec contents and source metadata."""

    spec_id: str
    case_count: int
    contents: str


def generate_playwright_spec(ir: ValidatedIr | Mapping[str, Any], *, enforce_target_origin: bool = False) -> GeneratedPlaywrightSpec:
    """Generate a TypeScript Playwright Test spec from schema-valid IR."""

    validated = ir if isinstance(ir, ValidatedIr) else parse_ir_document(ir)
    document = validated.raw
    contents = _render_document(document, enforce_target_origin=enforce_target_origin)
    return GeneratedPlaywrightSpec(spec_id=validated.spec_id, case_count=validated.case_count, contents=contents)


def generate_playwright_spec_file(path: str | Path, *, enforce_target_origin: bool = False) -> GeneratedPlaywrightSpec:
    """Read, validate, and generate a TypeScript Playwright Test spec from a JSON IR file."""

    return generate_playwright_spec(parse_ir_file(path), enforce_target_origin=enforce_target_origin)


def _render_document(document: Mapping[str, Any], *, enforce_target_origin: bool) -> str:
    lines = ['import { expect, test } from "@playwright/test";', ""]
    if enforce_target_origin:
        lines.extend(['import type { Page, Request } from "@playwright/test";', "", TARGET_ORIGIN_GUARD, ""])
    lines.append(f"test.describe({_js_string(document['spec']['title'])}, () => {{")

    for case in document["cases"]:
        lines.extend(_render_case(document, case, enforce_target_origin=enforce_target_origin))

    lines.append("});")
    lines.append("")
    return "\n".join(lines)


def _render_case(document: Mapping[str, Any], case: Mapping[str, Any], *, enforce_target_origin: bool) -> list[str]:
    lines = [
        f"  test({_js_string(case['title'])}, async ({{ page }}) => {{",
        f'    test.info().annotations.push({{ type: "specId", description: {_js_string(document["spec"]["id"])} }});',
        f'    test.info().annotations.push({{ type: "caseId", description: {_js_string(case["id"])} }});',
    ]
    if case.get("dataRowId"):
        lines.append(f'    test.info().annotations.push({{ type: "dataRowId", description: {_js_string(case["dataRowId"])} }});')

    if enforce_target_origin:
        base_url = _js_string(document["environment"]["baseUrl"])
        lines.append(f"    const checkTarget = await protectExecutionTarget(page, {base_url});")
        lines.append("    try {")
        for step in case["steps"]:
            url_argument = _js_string(step["url"]) if step["action"] == "navigate" else ""
            lines.append(f"      checkTarget({url_argument});")
            lines.extend(f"  {line}" for line in _render_step(step, enforce_target_origin=True))
        lines.extend(["    } finally {", "      checkTarget();", "    }"])
    else:
        for step in case["steps"]:
            lines.extend(_render_step(step))

    lines.append("  });")
    lines.append("")
    return lines


def _render_step(step: Mapping[str, Any], *, enforce_target_origin: bool = False) -> list[str]:
    action = step["action"]
    if action not in SUPPORTED_ACTIONS:
        raise PlaywrightGenerationError(
            (
                ValidationIssue(
                    f"$.cases[].steps[{step.get('sourceStepIndex', '?')}]",
                    f"unsupported IR action {action!r}",
                    "playwright.action_unsupported",
                ),
            )
        )

    lines = [
        f"    await test.step({_js_string(step['original'])}, async () => {{",
        *(f"      {line}" for line in _render_action(step, enforce_target_origin=enforce_target_origin).splitlines()),
        "    });",
    ]
    return lines


def _render_action(step: Mapping[str, Any], *, enforce_target_origin: bool = False) -> str:
    action = step["action"]
    if action == "navigate":
        return f"await page.goto({_js_string(step['url'])});"
    if action == "fill":
        return f"await {_render_locator(step['locator'])}.fill({_input_value(step['value'])});"
    if action == "fill_literal":
        return f"await {_render_locator(step['locator'])}.fill({_js_string(step['value'])});"
    if action == "select_option":
        locator = _render_locator(step["locator"])
        label = _js_string(step["value"])
        return "\n".join(
            (
                f'const select = {locator}.and(page.locator("select:not([multiple])"));',
                "await expect.poll(() => select.locator('option').evaluateAll((options, label) =>",
                "  options.filter(option => option instanceof HTMLOptionElement && option.label === label)",
                """    .map(option => !option.matches(':disabled') && !option.closest('[hidden], [inert], [aria-disabled="true" i]')),""",
                f"  {label})).toEqual([true]);",
                *(["checkTarget();"] if enforce_target_origin else []),
                f"await select.selectOption({{ label: {label} }});",
            )
        )
    if action == "click":
        return f"await {_render_locator(step['locator'])}.click();"
    if action in {"select_radio", "assert_radio_selected"}:
        locator = f"{_render_locator(step['locator'])}.and(page.locator('input[type=\"radio\"]'))"
        if action == "assert_radio_selected":
            checked = json.dumps(_to_bool(step["expected"]))
            return f"await expect({locator}).toBeChecked({{ checked: {checked} }});"
        return "\n".join(
            (
                f"const radio = {locator};",
                "await expect(radio).toBeVisible();",
                "await expect(radio).toBeEnabled();",
                *(["checkTarget();"] if enforce_target_origin else []),
                "await radio.check();",
            )
        )
    if action == "set_checked":
        locator = _render_locator(step["locator"])
        checked = json.dumps(step["value"])
        return "\n".join(
            (
                f"const checkbox = {locator}.and(page.locator('input[type=\"checkbox\"]'));",
                "await expect(checkbox).toBeVisible();",
                "await expect(checkbox).toBeEnabled();",
                *(["checkTarget();"] if enforce_target_origin else []),
                f"await checkbox.setChecked({checked});",
                f'await expect(checkbox.and(page.locator(":not(:indeterminate)"))).toBeChecked({{ checked: {checked} }});',
            )
        )
    if action == "assert_visible":
        return f"await expect({_render_locator(step['locator'])}).toBeVisible();"
    if action == "assert_hidden":
        locator = _render_locator(step["locator"], include_hidden=True)
        return f"await expect({locator}.filter({{ visible: true }})).toHaveCount(0);"
    if action == "assert_text_equals":
        return f"await expect({_render_locator(step['locator'])}).toHaveText({_js_string(_to_playwright_text(step['expected']))});"
    if action == "assert_scoped_text_equals":
        # Playwright normalizes whitespace when the expectation is a string.
        literal = re.escape(_to_playwright_text(step["expected"]))
        pattern = _js_string(f"^{literal}$")
        return f"await expect({_render_locator(step['locator'])}).toHaveText(new RegExp({pattern}));"
    if action == "assert_value_equals":
        return f"await expect({_render_locator(step['locator'])}).toHaveValue({_js_string(_to_playwright_text(step['expected']))});"
    if action == "assert_value_contains":
        pattern = _js_string(re.escape(step["expected"]))
        return f"await expect({_render_locator(step['locator'])}).toHaveValue(new RegExp({pattern}));"
    if action == "assert_url":
        return f"await expect(page).toHaveURL({_js_string(step['url'])});"
    if action == "assert_title":
        return f"await expect(page).toHaveTitle({_js_string(_to_playwright_text(step['expected']))});"
    if action == "assert_checked":
        if step["locator"]["strategy"] == "label" and step["locator"].get("exact"):
            locator = _render_locator(step["locator"])
            checked = json.dumps(_to_bool(step["expected"]))
            return f"await expect({locator}.and(page.locator('input[type=\"checkbox\"]:not(:indeterminate)'))).toBeChecked({{ checked: {checked} }});"
        if _to_bool(step["expected"]):
            return f"await expect({_render_locator(step['locator'])}).toBeChecked();"
        return f"await expect({_render_locator(step['locator'])}).not.toBeChecked();"
    if action == "assert_enabled":
        if _to_bool(step["expected"]):
            return f"await expect({_render_locator(step['locator'])}).toBeEnabled();"
        return f"await expect({_render_locator(step['locator'])}).toBeDisabled();"
    if action == "assert_attribute_equals":
        return (
            f"await expect({_render_locator(step['locator'])}).toHaveAttribute("
            f"{_js_string(step['attribute'])}, {_js_string(_to_playwright_text(step['expected']))});"
        )
    if action == "assert_count":
        return f"await expect({_render_locator(step['locator'])}).toHaveCount({_to_non_negative_int(step['expected'])});"
    return ""


def _render_locator(locator: Mapping[str, Any], *, include_hidden: bool = False) -> str:
    strategy = locator["strategy"]
    value = _js_string(locator["value"])
    if strategy == "test_id":
        return f"page.getByTestId({value})"
    if strategy == "role":
        role = _js_string(locator.get("role", "button"))
        exact = ", exact: true" if locator.get("exact") else ""
        hidden = ", includeHidden: true" if include_hidden else ""
        return f"page.getByRole({role}, {{ name: {value}{exact}{hidden} }})"
    if strategy == "label":
        exact = ", { exact: true }" if locator.get("exact") else ""
        return f"page.getByLabel({value}{exact})"
    if strategy == "text":
        exact = ", { exact: true }" if locator.get("exact") else ""
        return f"page.getByText({value}{exact})"
    if strategy == "css":
        return f"page.locator({value})"

    raise PlaywrightGenerationError(
        (
            ValidationIssue(
                "$.cases[].steps[].locator.strategy",
                f"unsupported locator strategy {strategy!r}",
                "playwright.locator_strategy_unsupported",
            ),
        )
    )


def _to_playwright_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise PlaywrightGenerationError((ValidationIssue("$", f"expected boolean assertion value, got {value!r}", "playwright.expected_type"),))


def _to_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PlaywrightGenerationError((ValidationIssue("$", f"expected integer assertion value, got {value!r}", "playwright.expected_type"),)) from exc
    if parsed < 0:
        raise PlaywrightGenerationError((ValidationIssue("$", f"expected non-negative count, got {parsed}", "playwright.expected_type"),))
    return parsed


def _js_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _input_value(value):
    text = _to_playwright_text(value)
    match = re.fullmatch(r"\[\[env:(PETF_TEST_[A-Z0-9_]+)\]\]", text)
    if match:
        name = _js_string(match[1])
        return f"(() => {{ const value = process.env[{name}]; if (!value) throw new Error('Execution prerequisite unavailable'); return value; }})()"
    if "[[env:" in text:
        raise PlaywrightGenerationError((ValidationIssue("$", "Unsupported execution reference", "playwright.reference_invalid"),))
    return _js_string(text)
