from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.automation_agent import generate_playwright_pom
from app.models import AutomationInput, TestCase, TestStep


class AutomationAgentTests(unittest.TestCase):
    def test_playwright_pom_uses_deterministic_fallback_without_model_credentials(self) -> None:
        payload = AutomationInput(
            test_cases=[
                TestCase(
                    id="TC-001",
                    title="Docs home loads",
                    steps=[TestStep(step=1, action="Open docs home", expected="The page is visible")],
                )
            ],
            target_base_url="https://playwright.dev/python/",
        )

        with patch("app.agents.automation_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            response = generate_playwright_pom(payload)

        self.assertEqual(response.status, "generated")
        self.assertIn("tests/test_playwright_docs.py", response.files)
        self.assertIn("https://playwright.dev/python/", response.notes)
        self.assertIn("class DocsPage", response.notes)


if __name__ == "__main__":
    unittest.main()
