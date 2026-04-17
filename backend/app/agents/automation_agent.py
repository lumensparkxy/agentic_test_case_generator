import json
import logging
from typing import List

from google import genai

from ..config import get_settings
from ..models import AutomationInput, AutomationResponse
from ..utils.genai_response import extract_response_text


def _build_pom_prompt(payload: AutomationInput) -> str:
    base_url = str(payload.target_base_url) if payload.target_base_url else "https://example.com"

    tcs_summary = []
    for tc in payload.test_cases[:30]:  # cap to avoid token overflow
        steps_text = "; ".join(
            f"Step {s.step}: {s.action}" for s in (tc.steps or [])[:5]
        )
        tcs_summary.append(
            f"  - [{tc.id}] {tc.title} | Component: {tc.component or 'General'} | Steps: {steps_text}"
        )

    tc_block = "\n".join(tcs_summary) if tcs_summary else "  (no test cases provided)"

    return f"""You are a Playwright Python test automation engineer.

Generate a complete Playwright pytest Page Object Model (POM) for the following test suite.

Base URL: {base_url}
Test Cases:
{tc_block}

Requirements:
1. Create a `BasePage` class with `__init__(self, page: Page)` and common helpers (navigate, wait_for_url, etc.)
2. Create one or more page classes (e.g. `DocsPage`, `NavigationPage`) that extend `BasePage` and encapsulate relevant locators and actions.
3. Create a `conftest.py` with a `browser_context_args` and a `page` fixture.
4. Create a `test_playwright_docs.py` test file that uses the page classes to implement at least one test per unique component found in the test cases above.
5. All code must use `from playwright.sync_api import Page, expect` (sync API).
6. Use `page.get_by_role`, `page.get_by_text`, `page.get_by_label`, and `page.locator` for all selectors - no raw CSS or XPath unless unavoidable.
7. Include `# type: ignore` only where strictly needed. Add triple-quoted docstrings for every class and test function.
8. Output ONLY valid Python source code - no markdown fences, no explanations outside comments.
9. Separate files with a comment header like: `# === FILE: tests/pages/base_page.py ===`

Begin generating the code now.
"""


def generate_playwright_pom(payload: AutomationInput) -> AutomationResponse:
    if not payload.test_cases:
        return AutomationResponse(
            status="skipped",
            files=[],
            notes="No test cases provided; POM generation skipped.",
        )

    try:
        settings = get_settings()
        client = genai.Client(api_key=settings.gemini_api_key)
        prompt = _build_pom_prompt(payload)

        response = client.models.generate_content(
            model=settings.model_name,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=8192,
            ),
        )
        pom_code = extract_response_text(response)

        # Strip accidental markdown code fences
        if pom_code.startswith("```"):
            lines = pom_code.splitlines()
            pom_code = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )

        # Extract file names from headers in the generated code
        files: List[str] = []
        for line in pom_code.splitlines():
            if line.strip().startswith("# === FILE:") and "===" in line:
                fname = line.split("# === FILE:", 1)[1].replace("===", "").strip()
                if fname:
                    files.append(fname)

        if not files:
            files = [
                "tests/conftest.py",
                "tests/pages/base_page.py",
                "tests/pages/docs_page.py",
                "tests/test_playwright_docs.py",
            ]

        return AutomationResponse(
            status="generated",
            files=files,
            notes=pom_code,
        )

    except Exception as exc:
        logging.exception("[AutomationAgent] POM generation failed: %s", exc)
        return AutomationResponse(
            status="error",
            files=[],
            notes=f"POM generation failed: {exc}",
        )
