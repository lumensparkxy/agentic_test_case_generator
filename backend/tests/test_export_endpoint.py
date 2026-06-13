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
from app.models import AuthUser


def _test_case() -> dict:
    return {
        "id": "TC-001",
        "title": "Checkout report evidence",
        "description": "Verifies evidence can be reported.",
        "priority": "High",
        "type": "Regression",
        "status": "Ready",
        "steps": [{"step": 1, "action": "Open checkout", "expected": "Checkout is visible"}],
        "expected_result": "Checkout evidence is ready.",
        "automation_status": "Automated",
        "linked_requirement_ids": ["REQ-001"],
        "scenario_refs": ["REQ-001-SCN-01"],
    }


class ExportEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="export-user",
            email="export@example.com",
            name="Export User",
            provider="google.com",
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_project_json_export_records_report_evidence_refs(self) -> None:
        payload = {
            "test_cases": [_test_case()],
            "approved": True,
            "review": {"approved": True, "score": 100, "threshold": 90, "summary": "Approved.", "blocking_issues": []},
            "project_id": "project-1",
            "base_project_revision": 9,
        }
        project = SimpleNamespace(
            current_snapshots={
                "requirements": SimpleNamespace(snapshot_id="snap-req-v2"),
                "use_cases": SimpleNamespace(snapshot_id="snap-use-v1"),
                "impact_analysis": SimpleNamespace(snapshot_id="snap-impact-v1"),
                "test_cases": SimpleNamespace(snapshot_id="snap-test-v2"),
                "execution": SimpleNamespace(snapshot_id="snap-exec-v1"),
            },
            execution_runs=[
                SimpleNamespace(
                    run_id="run-staging",
                    snapshot_id="snap-exec-v1",
                    target_environment="staging",
                    status="passed",
                )
            ],
        )

        with patch("app.routers.export.start_workflow_run", return_value="workflow-report-1"):
            with patch("app.routers.export.complete_workflow_run"):
                with patch("app.routers.export.record_usage_event", return_value="event-export-1"):
                    with patch("app.routers.export.get_project", return_value=project):
                        with patch("app.routers.export.append_stage_snapshot") as append_snapshot:
                            with TestClient(app) as client:
                                response = client.post(
                                    "/export/json",
                                    json=payload,
                                    headers={"X-Request-ID": "req-report-json"},
                                )

        self.assertEqual(response.status_code, 200)
        report_call = append_snapshot.call_args.kwargs
        self.assertEqual(report_call["stage"], "reports")
        self.assertEqual(report_call["operation"], "export.json")
        self.assertEqual(report_call["source_snapshot_id"], "snap-exec-v1")
        self.assertEqual(report_call["base_project_revision"], 9)
        self.assertEqual(report_call["metadata"]["source_snapshot_ids"]["test_cases"], "snap-test-v2")
        self.assertEqual(report_call["metadata"]["source_snapshot_ids"]["execution"], "snap-exec-v1")
        self.assertEqual(report_call["metadata"]["execution_run_ids"], ["run-staging"])
        self.assertEqual(report_call["payload"]["evidence"]["source_snapshot_ids"]["requirements"], "snap-req-v2")
        self.assertEqual(report_call["payload"]["evidence"]["execution_run_ids"], ["run-staging"])
        self.assertTrue(
            any(
                evidence["metadata"]["source"] == "execution_run" and evidence["item_ids"] == ["run-staging"] and evidence["snapshot_id"] == "snap-exec-v1"
                for evidence in report_call["payload"]["evidence"]["evidence_refs"]
            )
        )


if __name__ == "__main__":
    unittest.main()
