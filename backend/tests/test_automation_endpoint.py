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
from app.models import AuthUser, AutomationResponse, ExecutionCandidate, ExecutionPreviewResponse, ExecutionRunResponse, ExecutionRunSummary


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
            diagnostics={"generated_case_count": 1},
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

    def test_project_generation_saves_a_valid_report_stage_not_execution_evidence(self) -> None:
        payload = {
            "project_id": "project-1",
            "test_cases": [{"id": "TC-1", "title": "Unknown UI", "steps": [{"step": 1, "action": "Open feature", "expected": "Feature visible"}]}],
        }
        response = AutomationResponse(
            status="skipped",
            files=[],
            diagnostics={"generated_case_count": 0},
            case_diagnostics=[
                {"test_case_id": "TC-1", "status": "manual", "reason": "Missing observed interface", "source_expected_results": ["Feature visible"]}
            ],
        )
        project = SimpleNamespace(current_revision=9)
        with (
            patch("app.routers.automation.project_for", return_value=project),
            patch("app.main.start_workflow_run", return_value="run-manual"),
            patch("app.main.complete_workflow_run"),
            patch("app.main.record_usage_event", return_value="event-manual"),
            patch("app.main.generate_playwright_pom", return_value=response),
            patch("app.routers.automation.append_stage_snapshot") as append,
            patch("app.routers.automation.finish_generation", side_effect=lambda response, *args: response),
            TestClient(app) as client,
        ):
            result = client.post("/automation/playwright", json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        saved = append.call_args.kwargs
        self.assertEqual(saved["stage"], "reports")
        self.assertEqual(saved["base_project_revision"], 9)
        self.assertFalse(saved["approved"])
        self.assertEqual(saved["payload"]["source"], "automation_generation")
        self.assertEqual(saved["payload"]["execution_status"], "not_executed")
        self.assertEqual(saved["payload"]["case_diagnostics"][0]["status"], "manual")

    def test_generation_report_revision_conflict_is_recoverable(self) -> None:
        from app.services.workflow_project_service import ProjectConflictError

        payload = {"project_id": "project-1", "test_cases": []}
        response = AutomationResponse(status="skipped", files=[], diagnostics={"generated_case_count": 0})
        with (
            patch("app.routers.automation.project_for", return_value=SimpleNamespace(current_revision=9)),
            patch("app.main.start_workflow_run", return_value="run-conflict"),
            patch("app.main.complete_workflow_run"),
            patch("app.main.record_usage_event", return_value="event-conflict"),
            patch("app.main.generate_playwright_pom", return_value=response),
            patch("app.routers.automation.append_stage_snapshot", side_effect=ProjectConflictError(10)),
            TestClient(app) as client,
        ):
            result = client.post("/automation/playwright", json=payload)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["detail"]["latest_revision"], 10)

    def test_missing_interface_evidence_is_not_logged_as_generated_cases(self) -> None:
        payload = {
            "test_cases": [
                {
                    "id": "TC-1",
                    "title": "Unknown UI",
                    "steps": [{"step": 1, "action": "Open feature", "expected": "Feature visible"}],
                    "automation_status": "To Be Automated",
                }
            ]
        }
        with (
            patch("app.main.start_workflow_run", return_value="run-manual"),
            patch("app.main.complete_workflow_run"),
            patch("app.main.record_usage_event", return_value="event-manual") as event,
            patch("app.agents.automation_agent.genai.Client") as model,
            TestClient(app) as client,
        ):
            response = client.post("/automation/playwright", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "skipped")
        self.assertEqual(response.json()["files"], [])
        self.assertEqual(response.json()["diagnostics"]["manual_case_count"], 1)
        self.assertEqual(event.call_args.kwargs["quantity"], 0)
        model.assert_not_called()

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

    def test_project_execution_preview_persists_matching_counts_and_candidates(self) -> None:
        payload = {
            "test_cases": [
                {
                    "id": "TC-001",
                    "title": "Verify checkout",
                    "steps": [{"step": 1, "action": "Open checkout", "expected": "Checkout is displayed"}],
                    "automation_status": "Automated",
                },
                {
                    "id": "TC-MANUAL",
                    "title": "Review payment receipt",
                    "steps": [{"step": 1, "action": "Review receipt", "expected": "Receipt is correct"}],
                    "automation_status": "Manual",
                },
            ],
            "target_base_url": "https://staging.example.test/app",
            "target_environment": "staging",
            "project_id": "project-1",
            "base_project_revision": 7,
        }
        executable_candidate = ExecutionCandidate(
            id="tc_001",
            source_test_case_id="TC-001",
            title="Verify checkout",
            status="executable",
            spec={"schemaVersion": "1.0", "id": "tc_001", "title": "Verify checkout", "steps": []},
        )
        manual_candidate = ExecutionCandidate(
            id="tc_manual",
            source_test_case_id="TC-MANUAL",
            title="Review payment receipt",
            status="manual",
        )
        service_response = ExecutionPreviewResponse(
            executable=[executable_candidate],
            manual=[manual_candidate],
            summary={"executable": 20, "manual": 0, "unsupported": 9, "invalid": 4},
        )
        project = SimpleNamespace(current_snapshots={"test_cases": SimpleNamespace(snapshot_id="snap-test-v1")})

        with patch("app.main.start_workflow_run", return_value="run-execution-preview-1"):
            with patch("app.main.complete_workflow_run"):
                with patch("app.main.record_usage_event", return_value="event-execution-preview-1") as record_event:
                    with patch("app.main.preview_execution", return_value=service_response):
                        with patch("app.routers.automation.get_project", return_value=project):
                            with patch("app.routers.automation.append_stage_snapshot") as append_snapshot:
                                with TestClient(app) as client:
                                    response = client.post(
                                        "/automation/execution/preview",
                                        json=payload,
                                        headers={"X-Request-ID": "req-preview-staging"},
                                    )

        self.assertEqual(response.status_code, 200)
        expected_counts = {"executable": 1, "manual": 1, "unsupported": 0, "invalid": 0}
        self.assertEqual(response.json()["summary"], expected_counts)
        snapshot_call = append_snapshot.call_args.kwargs
        snapshot_payload = snapshot_call["payload"]
        self.assertEqual(snapshot_payload["summary"], expected_counts)
        self.assertEqual(snapshot_payload["candidate_counts"], expected_counts)
        self.assertEqual(
            {bucket: len(candidates) for bucket, candidates in snapshot_payload["candidates"].items()},
            expected_counts,
        )
        self.assertEqual(
            snapshot_payload["candidates"]["executable"][0],
            {"id": "tc_001", "source_test_case_id": "TC-001", "title": "Verify checkout", "status": "executable"},
        )
        self.assertEqual(snapshot_call["metadata"]["source_snapshot_id"], "snap-test-v1")
        self.assertEqual(
            {key: snapshot_call["metadata"][f"{key}_count"] for key in expected_counts},
            expected_counts,
        )
        event_metadata = record_event.call_args.kwargs["metadata"]
        self.assertEqual(
            {key: event_metadata[f"{key}_count"] for key in expected_counts},
            expected_counts,
        )

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

    def test_project_execution_run_records_environment_source_snapshot_and_idempotency(self) -> None:
        payload = {
            "test_cases": [
                {
                    "id": "TC-001",
                    "title": "Verify checkout",
                    "steps": [{"step": 1, "action": "Open checkout", "expected": "Checkout is displayed"}],
                    "automation_status": "Automated",
                }
            ],
            "selected_test_case_ids": ["TC-001"],
            "target_base_url": "https://staging.example.test/app",
            "target_environment": "staging",
            "project_id": "project-1",
            "base_project_revision": 7,
        }
        service_response = ExecutionRunResponse(
            status="failed",
            run_id="exec_staging",
            artifacts_root="/tmp/exec_staging",
            playwright_report_paths=["/tmp/exec_staging/artifacts/playwright/run/html-report"],
            preview=ExecutionPreviewResponse(),
            summary=ExecutionRunSummary(passed=0, failed=1),
        )
        project = SimpleNamespace(current_snapshots={"test_cases": SimpleNamespace(snapshot_id="snap-test-v1")})
        execution_snapshot = SimpleNamespace(snapshot_id="snap-exec-v1", project_revision=8)
        report_snapshot = SimpleNamespace(snapshot_id="snap-report-v1", project_revision=9)

        with patch("app.main.start_workflow_run", return_value="run-execution-run-1"):
            with patch("app.main.complete_workflow_run"):
                with patch("app.main.record_usage_event", return_value="event-execution-run-1"):
                    with patch("app.main.run_execution", return_value=service_response):
                        with patch("app.routers.automation.get_project", return_value=project):
                            with patch(
                                "app.routers.automation.append_stage_snapshot",
                                side_effect=[execution_snapshot, report_snapshot],
                            ) as append_snapshot:
                                with patch("app.routers.automation.record_execution_run") as record_run:
                                    with TestClient(app) as client:
                                        response = client.post(
                                            "/automation/execution/run",
                                            json=payload,
                                            headers={"X-Request-ID": "req-exec-staging"},
                                        )

        self.assertEqual(response.status_code, 200)
        execution_call = append_snapshot.call_args_list[0].kwargs
        report_call = append_snapshot.call_args_list[1].kwargs
        record_call = record_run.call_args.kwargs
        self.assertEqual(execution_call["source_snapshot_id"], "snap-test-v1")
        self.assertEqual(execution_call["metadata"]["target_environment"], "staging")
        self.assertEqual(execution_call["metadata"]["source_snapshot_id"], "snap-test-v1")
        self.assertEqual(execution_call["idempotency_key"], "automation.execution.run:req-exec-staging:staging")
        self.assertEqual(report_call["source_snapshot_id"], "snap-exec-v1")
        self.assertEqual(report_call["idempotency_key"], "reports.execution_summary:req-exec-staging:staging")
        self.assertEqual(report_call["payload"]["artifacts_root"], "/tmp/exec_staging")
        self.assertEqual(report_call["payload"]["playwright_report_paths"], ["/tmp/exec_staging/artifacts/playwright/run/html-report"])
        self.assertEqual(record_call["target_environment"], "staging")
        self.assertEqual(record_call["target_base_url"], "https://staging.example.test/app")
        self.assertEqual(record_call["artifacts_root"], "/tmp/exec_staging")
        self.assertEqual(record_call["playwright_report_paths"], ["/tmp/exec_staging/artifacts/playwright/run/html-report"])
        self.assertEqual(record_call["source_snapshot_id"], "snap-test-v1")
        self.assertEqual(record_call["selected_test_case_ids"], ["TC-001"])
        self.assertEqual(record_call["idempotency_key"], "automation.execution.run_record:req-exec-staging:staging")


if __name__ == "__main__":
    unittest.main()
