from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, AutomationResponse, ExecutionPreviewResponse, ExecutionRunResponse


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

        with patch("app.main.start_workflow_run", return_value="run-automation-1") as start_run:
            with patch("app.main.complete_workflow_run") as complete_run:
                with patch("app.main.record_usage_event", return_value="event-automation-1") as record_event:
                    with patch("app.main.generate_playwright_pom", return_value=service_response) as generate:
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

    def test_execution_preview_logs_usage_and_returns_buckets(self) -> None:
        payload = {
            "test_cases": [
                {
                    "id": "TC-001",
                    "title": "Verify login",
                    "steps": [
                        {
                            "step": 1,
                            "action": "Open login page",
                            "expected": "Login form is displayed",
                        }
                    ],
                    "automation_status": "Automated",
                }
            ],
            "target_base_url": "https://example.test/app",
        }
        service_response = ExecutionPreviewResponse()

        with patch("app.main.start_workflow_run", return_value="run-execution-preview-1") as start_run:
            with patch("app.main.complete_workflow_run") as complete_run:
                with patch("app.main.record_usage_event", return_value="event-execution-preview-1") as record_event:
                    with patch("app.main.preview_execution", return_value=service_response) as preview:
                        with TestClient(app) as client:
                            response = client.post("/automation/execution/preview", json=payload)

        self.assertEqual(response.status_code, 200)
        preview.assert_called_once()
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        self.assertEqual(record_event.call_args.kwargs["event_type"], "automation.execution.previewed")

    def test_execution_run_logs_usage_and_returns_summary(self) -> None:
        payload = {
            "test_cases": [
                {
                    "id": "TC-001",
                    "title": "Verify login",
                    "steps": [
                        {
                            "step": 1,
                            "action": "Open login page",
                            "expected": "Login form is displayed",
                        }
                    ],
                    "automation_status": "Automated",
                }
            ],
            "selected_test_case_ids": ["TC-001"],
            "target_base_url": "https://example.test/app",
        }
        service_response = ExecutionRunResponse(status="passed", run_id="exec_test", preview=ExecutionPreviewResponse())

        with patch("app.main.start_workflow_run", return_value="run-execution-run-1") as start_run:
            with patch("app.main.complete_workflow_run") as complete_run:
                with patch("app.main.record_usage_event", return_value="event-execution-run-1") as record_event:
                    with patch("app.main.run_execution", return_value=service_response) as run:
                        with TestClient(app) as client:
                            response = client.post("/automation/execution/run", json=payload)

        self.assertEqual(response.status_code, 200)
        run.assert_called_once()
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        self.assertEqual(record_event.call_args.kwargs["event_type"], "automation.execution.ran")
        self.assertEqual(response.json()["run_id"], "exec_test")


if __name__ == "__main__":
    unittest.main()
