from typing import List
from ..models import AutomationInput, AutomationResponse


def generate_playwright_pom(payload: AutomationInput) -> AutomationResponse:
    files: List[str] = [
        "tests/pages/example_page.py",
        "tests/test_generated_cases.py",
    ]
    return AutomationResponse(
        status="stubbed",
        files=files,
        notes="POM generation stub. Implement selectors and actions for your app.",
    )
