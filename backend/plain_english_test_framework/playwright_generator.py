"""Generate TypeScript Playwright Test specs from validated IR."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from plain_english_test_framework.ir_validator import ValidatedIr, parse_ir_document, parse_ir_file
from plain_english_test_framework.validation import ValidationIssue


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


def generate_playwright_spec(ir: ValidatedIr | Mapping[str, Any]) -> GeneratedPlaywrightSpec:
    """Generate a TypeScript Playwright Test spec from schema-valid IR."""

    validated = ir if isinstance(ir, ValidatedIr) else parse_ir_document(ir)
    document = validated.raw
    contents = _render_document(document)
    return GeneratedPlaywrightSpec(spec_id=validated.spec_id, case_count=validated.case_count, contents=contents)


def generate_playwright_spec_file(path: str | Path) -> GeneratedPlaywrightSpec:
    """Read, validate, and generate a TypeScript Playwright Test spec from a JSON IR file."""

    return generate_playwright_spec(parse_ir_file(path))


def _render_document(document: Mapping[str, Any]) -> str:
    lines: list[str] = [
        "import { expect, test } from \"@playwright/test\";",
        "",
        f"test.describe({_js_string(document['spec']['title'])}, () => {{",
    ]

    for case in document["cases"]:
        lines.extend(_render_case(document, case))

    lines.append("});")
    lines.append("")
    return "\n".join(lines)


def _render_case(document: Mapping[str, Any], case: Mapping[str, Any]) -> list[str]:
    lines = [
        f"  test({_js_string(case['title'])}, async ({{ page }}) => {{",
        f"    test.info().annotations.push({{ type: \"specId\", description: {_js_string(document['spec']['id'])} }});",
        f"    test.info().annotations.push({{ type: \"caseId\", description: {_js_string(case['id'])} }});",
    ]
    if case.get("dataRowId"):
        lines.append(
            f"    test.info().annotations.push({{ type: \"dataRowId\", description: {_js_string(case['dataRowId'])} }});"
        )

    for step in case["steps"]:
        lines.extend(_render_step(step))

    lines.append("  });")
    lines.append("")
    return lines


def _render_step(step: Mapping[str, Any]) -> list[str]:
    action = step["action"]
    lines = [
        f"    await test.step({_js_string(step['original'])}, async () => {{",
        f"      {_render_action(step)}",
        "    });",
    ]
    if action not in {"navigate", "fill", "click", "assert_visible", "assert_text_equals", "assert_url"}:
        raise PlaywrightGenerationError(
            (
                ValidationIssue(
                    f"$.cases[].steps[{step.get('sourceStepIndex', '?')}]",
                    f"unsupported IR action {action!r}",
                    "playwright.action_unsupported",
                ),
            )
        )
    return lines


def _render_action(step: Mapping[str, Any]) -> str:
    action = step["action"]
    if action == "navigate":
        return f"await page.goto({_js_string(step['url'])});"
    if action == "fill":
        return f"await {_render_locator(step['locator'])}.fill({_js_string(_to_playwright_text(step['value']))});"
    if action == "click":
        return f"await {_render_locator(step['locator'])}.click();"
    if action == "assert_visible":
        return f"await expect({_render_locator(step['locator'])}).toBeVisible();"
    if action == "assert_text_equals":
        return f"await expect({_render_locator(step['locator'])}).toHaveText({_js_string(_to_playwright_text(step['expected']))});"
    if action == "assert_url":
        return f"await expect(page).toHaveURL({_js_string(step['url'])});"
    return ""


def _render_locator(locator: Mapping[str, Any]) -> str:
    strategy = locator["strategy"]
    value = _js_string(locator["value"])
    if strategy == "test_id":
        return f"page.getByTestId({value})"
    if strategy == "role":
        role = _js_string(locator.get("role", "button"))
        return f"page.getByRole({role}, {{ name: {value} }})"
    if strategy == "label":
        return f"page.getByLabel({value})"
    if strategy == "text":
        return f"page.getByText({value})"
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


def _js_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)
