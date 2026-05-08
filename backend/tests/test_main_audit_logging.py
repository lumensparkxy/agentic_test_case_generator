from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, Requirement, TestCase, TestStep


class MainAuditLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="firebase-uid-999",
            email="tester@example.com",
            name="Test User",
            provider="google.com",
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_parse_requirements_logs_workflow_run_and_usage_event(self) -> None:
        workflow_result = {
            "requirements": [{"id": "REQ-1", "text": "The system shall allow login"}],
            "approved": True,
            "review": {"approved": True, "score": 100, "threshold": 80, "summary": "ok", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {},
            "workflow_diagnostics": {},
        }

        with patch("app.routers.requirements.start_workflow_run", return_value="run-parse-1") as start_run:
            with patch("app.routers.requirements.complete_workflow_run") as complete_run:
                with patch("app.routers.requirements.record_usage_event", return_value="event-parse-1") as record_event:
                    with patch("app.routers.requirements.persist_requirement_versions", return_value=[
                        Requirement(
                            id="REQ-1",
                            text="The system shall allow login",
                            artifact_set_id="req-set-1",
                            artifact_item_id="req-item-1",
                            artifact_version_id="req-ver-1",
                            artifact_version_number=1,
                        )
                    ]) as persist_requirements:
                        with patch("app.routers.requirements.extract_requirements", return_value=workflow_result) as extract:
                            with TestClient(app) as client:
                                response = client.post(
                                    "/requirements/parse",
                                    files={"file": ("requirements.md", BytesIO(b"# Requirement\nThe system shall allow login"), "text/markdown")},
                                )

        self.assertEqual(response.status_code, 200)
        extract.assert_called_once()
        self.assertEqual(extract.call_args.args[3], "firebase-uid-999")
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        persist_requirements.assert_called_once()
        self.assertEqual(response.json()["requirements"][0]["artifact_set_id"], "req-set-1")

    def test_generate_test_cases_logs_workflow_run_and_usage_event(self) -> None:
        workflow_result = {
            "test_cases": [{"id": "TC-1", "title": "Login test", "steps": [{"step": 1, "action": "Act", "expected": "Observe"}], "priority": "Medium", "type": "Functional", "status": "Draft", "automation_status": "Manual"}],
            "approved": True,
            "review": {"approved": True, "score": 100, "threshold": 80, "summary": "ok", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
            "iteration_history": [],
            "coverage_plan": [],
            "requirement_analysis": [],
            "coverage_metrics": {},
            "workflow_settings": {},
            "workflow_diagnostics": {},
        }

        payload = {
            "requirements": [{"id": "REQ-1", "text": "The system shall allow login"}],
            "template": {"name": "default", "format": "table", "fields": ["id", "title", "steps"]},
            "context": None,
            "feedback": None,
            "workflow_settings": None,
        }

        with patch("app.routers.testcases.start_workflow_run", return_value="run-generate-1") as start_run:
            with patch("app.routers.testcases.complete_workflow_run") as complete_run:
                with patch("app.routers.testcases.record_usage_event", return_value="event-generate-1") as record_event:
                    with patch("app.routers.testcases.persist_test_case_versions", return_value=[
                        TestCase(
                            id="TC-1",
                            title="Login test",
                            steps=[TestStep(step=1, action="Act", expected="Observe")],
                            artifact_set_id="tc-set-1",
                            artifact_item_id="tc-item-1",
                            artifact_version_id="tc-ver-1",
                            artifact_version_number=1,
                        )
                    ]) as persist_test_cases:
                        with patch("app.routers.testcases.generate_test_cases", return_value=workflow_result) as generate:
                            with TestClient(app) as client:
                                response = client.post("/testcases/generate", json=payload)

        self.assertEqual(response.status_code, 200)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1], "firebase-uid-999")
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        persist_test_cases.assert_called_once()
        self.assertEqual(response.json()["test_cases"][0]["artifact_set_id"], "tc-set-1")


if __name__ == "__main__":
    unittest.main()