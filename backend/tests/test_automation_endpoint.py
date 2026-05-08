from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, AutomationResponse


class AutomationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="automation-user",
            email="automation@example.com",
            name="Automation User",
            provider="google.com",
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_playwright_generation_logs_usage_and_returns_files(self) -> None:
        payload = {
            "test_cases": [
                {
                    "id": "TC-001",
                    "title": "Verify login",
                    "description": "Checks successful login",
                    "priority": "High",
                    "type": "Functional",
                    "status": "Draft",
                    "preconditions": "User account exists",
                    "steps": [
                        {
                            "step": 1,
                            "action": "Open login page",
                            "expected": "Login form is displayed",
                        }
                    ],
                    "expected_result": "User is logged in",
                    "test_data": "Valid account credentials",
                    "estimated_time": "5 mins",
                    "automation_status": "To Be Automated",
                    "component": "Authentication",
                    "tags": ["REQ-001"],
                }
            ],
            "target_base_url": "https://example.test/app",
        }
        service_response = AutomationResponse(
            status="generated",
            files=["pages/login_page.py", "tests/test_login.py"],
            notes="Generated Playwright stubs.",
        )

        with patch("app.routers.automation.start_workflow_run", return_value="run-automation-1") as start_run:
            with patch("app.routers.automation.complete_workflow_run") as complete_run:
                with patch("app.routers.automation.record_usage_event", return_value="event-automation-1") as record_event:
                    with patch("app.routers.automation.generate_playwright_pom", return_value=service_response) as generate:
                        with TestClient(app) as client:
                            response = client.post("/automation/playwright", json=payload)

        self.assertEqual(response.status_code, 200)
        generate.assert_called_once()
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        self.assertEqual(response.json()["files"], ["pages/login_page.py", "tests/test_login.py"])
        self.assertEqual(record_event.call_args.kwargs["event_type"], "automation.playwright.generated")
        self.assertEqual(record_event.call_args.kwargs["quantity"], 1)


if __name__ == "__main__":
    unittest.main()
