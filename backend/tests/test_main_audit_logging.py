from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, GenerateTestCasesResponse, Requirement, TestCase, TestCaseGenerationEvidence, TestStep
from app.routers.testcases import _append_project_generation_snapshots, _test_case_project_payload


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

        with ExitStack() as stack:
            enforce_billing = stack.enter_context(patch("app.routers.requirements.enforce_billing_access", return_value=object()))
            record_consumption = stack.enter_context(patch("app.routers.requirements.record_billing_consumption"))
            start_run = stack.enter_context(patch("app.routers.requirements.start_workflow_run", return_value="run-parse-1"))
            complete_run = stack.enter_context(patch("app.routers.requirements.complete_workflow_run"))
            record_event = stack.enter_context(patch("app.routers.requirements.record_usage_event", return_value="event-parse-1"))
            persist_requirements = stack.enter_context(
                patch(
                    "app.routers.requirements.persist_requirement_versions",
                    return_value=[
                        Requirement(
                            id="REQ-1",
                            text="The system shall allow login",
                            artifact_set_id="req-set-1",
                            artifact_item_id="req-item-1",
                            artifact_version_id="req-ver-1",
                            artifact_version_number=1,
                        )
                    ],
                )
            )
            extract = stack.enter_context(patch("app.routers.requirements.extract_requirements", return_value=workflow_result))
            with TestClient(app) as client:
                response = client.post(
                    "/requirements/parse",
                    files={"file": ("requirements.md", BytesIO(b"# Requirement\nThe system shall allow login"), "text/markdown")},
                )

        self.assertEqual(response.status_code, 200)
        enforce_billing.assert_called_once()
        self.assertEqual(enforce_billing.call_args.kwargs["billing_key"], "requirements.parse")
        record_consumption.assert_called_once()
        self.assertEqual(record_consumption.call_args.kwargs["source_event_id"], "event-parse-1")
        extract.assert_called_once()
        self.assertEqual(extract.call_args.kwargs["actor_user_id"], "firebase-uid-999")
        self.assertTrue(extract.call_args.kwargs["request_id"])
        self.assertEqual(extract.call_args.kwargs["workflow_run_id"], "run-parse-1")
        self.assertEqual(extract.call_args.kwargs["operation"], "requirements.parse")
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        persist_requirements.assert_called_once()
        self.assertEqual(response.json()["requirements"][0]["artifact_set_id"], "req-set-1")

    def test_generate_test_cases_logs_workflow_run_and_usage_event(self) -> None:
        workflow_result = {
            "test_cases": [
                {
                    "id": "TC-1",
                    "title": "Login test",
                    "steps": [{"step": 1, "action": "Act", "expected": "Observe"}],
                    "priority": "Medium",
                    "type": "Functional",
                    "status": "Draft",
                    "automation_status": "Manual",
                }
            ],
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
            "requirement_analysis": [
                {
                    "requirement_id": "REQ-1",
                    "requirement_text": "The system shall allow login",
                    "business_rules": [
                        {
                            "id": "REQ-1-BR-01",
                            "requirement_id": "REQ-1",
                            "title": "Login allowed",
                            "description": "The system shall allow login",
                            "rule_type": "Business",
                        }
                    ],
                }
            ],
            "coverage_plan": [
                {
                    "requirement_id": "REQ-1",
                    "requirement_text": "The system shall allow login",
                    "scenarios": [
                        {
                            "id": "REQ-1-SCN-01",
                            "requirement_id": "REQ-1",
                            "scenario_type": "Happy Path",
                            "title": "Login succeeds",
                            "objective": "Verify successful login.",
                            "priority": "High",
                            "must_have": True,
                        }
                    ],
                }
            ],
        }

        with ExitStack() as stack:
            enforce_billing = stack.enter_context(patch("app.routers.testcases.enforce_billing_access", return_value=object()))
            record_consumption = stack.enter_context(patch("app.routers.testcases.record_billing_consumption"))
            start_run = stack.enter_context(patch("app.routers.testcases.start_workflow_run", return_value="run-generate-1"))
            complete_run = stack.enter_context(patch("app.routers.testcases.complete_workflow_run"))
            record_event = stack.enter_context(patch("app.routers.testcases.record_usage_event", return_value="event-generate-1"))
            persist_test_cases = stack.enter_context(
                patch(
                    "app.routers.testcases.persist_test_case_versions",
                    return_value=[
                        TestCase(
                            id="TC-1",
                            title="Login test",
                            steps=[TestStep(step=1, action="Act", expected="Observe")],
                            artifact_set_id="tc-set-1",
                            artifact_item_id="tc-item-1",
                            artifact_version_id="tc-ver-1",
                            artifact_version_number=1,
                        )
                    ],
                )
            )
            generate = stack.enter_context(patch("app.routers.testcases.generate_test_cases", return_value=workflow_result))
            with TestClient(app) as client:
                response = client.post("/testcases/generate", json=payload)

        self.assertEqual(response.status_code, 200)
        enforce_billing.assert_called_once()
        self.assertEqual(enforce_billing.call_args.kwargs["billing_key"], "testcases.generate")
        record_consumption.assert_called_once()
        self.assertEqual(record_consumption.call_args.kwargs["source_event_id"], "event-generate-1")
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["actor_user_id"], "firebase-uid-999")
        self.assertTrue(generate.call_args.kwargs["request_id"])
        self.assertEqual(generate.call_args.kwargs["workflow_run_id"], "run-generate-1")
        self.assertEqual(generate.call_args.kwargs["operation"], "testcases.generate")
        self.assertEqual(len(generate.call_args.args[0].coverage_plan), 1)
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        persist_test_cases.assert_called_once()
        self.assertEqual(response.json()["test_cases"][0]["artifact_set_id"], "tc-set-1")
        self.assertIn("generation_evidence", response.json())

    def test_test_case_project_payload_persists_generation_evidence(self) -> None:
        response = GenerateTestCasesResponse(
            test_cases=[TestCase(id="TC-1", title="Login test", steps=[TestStep(step=1, action="Act", expected="Observe")])],
            generation_evidence=TestCaseGenerationEvidence(
                request_id="req-1",
                workflow_run_id="run-1",
                operation="testcases.generate",
                final_test_case_count=1,
            ),
        )

        payload = _test_case_project_payload(response)

        self.assertEqual(payload["generation_evidence"]["request_id"], "req-1")
        self.assertEqual(payload["generation_evidence"]["workflow_run_id"], "run-1")
        self.assertEqual(payload["generation_evidence"]["final_test_case_count"], 1)

    def test_project_generation_keeps_machine_and_human_use_case_approval_separate(self) -> None:
        response = GenerateTestCasesResponse(
            test_cases=[TestCase(id="TC-1", title="Login test", steps=[TestStep(step=1, action="Act", expected="Observe")])],
            approved=True,
            review={
                "approved": True,
                "score": 100,
                "threshold": 90,
                "summary": "Automated quality checks passed.",
                "blocking_issues": [],
                "suggestions": [],
                "unmet_criteria": [],
            },
        )

        with (
            patch(
                "app.routers.testcases.get_project",
                return_value=SimpleNamespace(current_snapshots={}),
            ),
            patch(
                "app.routers.testcases.append_stage_snapshot",
                side_effect=[SimpleNamespace(snapshot_id="snap-use-cases"), SimpleNamespace(snapshot_id="snap-test-cases")],
            ) as append_snapshot,
        ):
            _append_project_generation_snapshots(
                project_id="project-1",
                response=response,
                operation="testcases.generate",
                actor=AuthUser(sub="user-1", email="user@example.com", name="User"),
                request_id="request-1",
                workflow_run_id="run-1",
                source_event_id="event-1",
                base_project_revision=4,
            )

        use_case_call, test_case_call = append_snapshot.call_args_list
        self.assertEqual(use_case_call.kwargs["stage"], "use_cases")
        self.assertFalse(use_case_call.kwargs["approved"])
        self.assertTrue(use_case_call.kwargs["payload"]["review"]["approved"])
        self.assertEqual(test_case_call.kwargs["stage"], "test_cases")
        self.assertTrue(test_case_call.kwargs["approved"])


if __name__ == "__main__":
    unittest.main()
