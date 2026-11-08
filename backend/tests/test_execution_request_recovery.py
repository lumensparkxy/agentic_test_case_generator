from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Event
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_workflow_project_service as fixtures
from app.main import app, get_current_user
from app.models import ExecutionCandidate, ExecutionPreviewResponse, ExecutionRunResponse
from app.services.workflow_project_service import append_stage_snapshot, create_project, get_project


class ExecutionRequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowProjectServiceTests.setUp(self)
        self.addCleanup(fixtures.WorkflowProjectServiceTests.tearDown, self)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.pop, get_current_user)
        self.project = create_project(name="Retry-safe run", description=None, actor=self.actor, request_id="create")
        self.prefix = f"/projects/{self.project.project_id}"
        case = {"id": "TC-1", "title": "Confirm receipt", "steps": [{"step": 1, "action": "Open checkout", "expected": "Receipt appears"}]}
        for stage, data in (("requirements", {"requirements": []}), ("test_cases", {"test_cases": [case]})):
            self.snapshot = append_stage_snapshot(
                project_id=self.project.project_id,
                stage=stage,
                payload=data,
                operation="generate",
                actor=self.actor,
                request_id=stage,
                approved=True,
            )
        self.body = {
            "project_id": self.project.project_id,
            "base_project_revision": self.snapshot.project_revision,
            "test_cases": [case],
            "target_environment": "staging",
            "target_base_url": "https://example.test/",
            "selected_test_case_ids": ["candidate-1"],
        }
        self.headers = {"X-Request-ID": "run-intent-1"}
        self.receipt_path = f"{self.prefix}/execution-requests/run-intent-1"
        self.preview = ExecutionPreviewResponse(
            executable=[ExecutionCandidate(id="candidate-1", source_test_case_id="TC-1", title=case["title"], status="executable")]
        )
        self.enterContext(patch("app.main.preview_execution", return_value=self.preview))
        self.enterContext(patch("app.main.start_workflow_run", return_value="workflow-run"))
        self.enterContext(patch("app.main.complete_workflow_run"))
        self.usage = self.enterContext(patch("app.main.record_usage_event", return_value="usage-event"))

    def test_overlapping_request_and_completed_replay_launch_once(self):
        entered, release = Event(), Event()

        def run(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Synthetic browser release timed out")
            return ExecutionRunResponse(status="failed", run_id="exec-saved", preview=self.preview)

        with patch("app.main.run_execution", side_effect=run) as runner, TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, "/automation/execution/run", json=self.body, headers=self.headers)
            try:
                self.assertTrue(entered.wait(8), "The admitted request never reached the runtime")
                self.assertEqual(client.get(self.receipt_path).json()["status"], "pending")
                duplicate = client.post("/automation/execution/run", json=self.body, headers=self.headers)
                self.assertEqual(duplicate.status_code, 409)
                self.assertEqual(duplicate.json()["detail"]["receipt"]["status"], "pending")
                different = client.post("/automation/execution/run", json={**self.body, "target_environment": "other"}, headers=self.headers)
                self.assertEqual(different.status_code, 409)
                self.assertEqual(different.json()["detail"]["code"], "execution_request_conflict")
            finally:
                release.set()
            response = first.result(timeout=8)
            self.assertEqual(response.status_code, 200, response.text)
            completed = client.get(self.receipt_path).json()
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["run_id"], "exec-saved")
            self.assertEqual(completed["source_snapshot_id"], self.snapshot.snapshot_id)
            replay = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(replay.status_code, 409)
            self.assertEqual(replay.json()["detail"]["receipt"], completed)
            exact = client.get(f"{self.prefix}/execution-runs/{completed['run_record_id']}")
            self.assertEqual(exact.status_code, 200, exact.text)
            self.assertEqual(exact.json()["run_id"], completed["run_id"])
            original_actor = self.actor
            self.actor = original_actor.model_copy(update={"sub": "another-user"})
            self.assertEqual(client.get(self.receipt_path).status_code, 403)
            self.assertEqual(client.post("/automation/execution/run", json=self.body, headers=self.headers).status_code, 403)
            self.actor = original_actor
        runner.assert_called_once()
        self.usage.assert_called_once()

    def test_unconfirmed_transaction_rolls_back_evidence_and_never_relaunches(self):
        def run(*args, **kwargs):
            self.transaction_client.fail_on_create_segment = "/execution_runs/"
            return ExecutionRunResponse(status="passed", run_id="exec-uncertain", preview=self.preview)

        with patch("app.main.run_execution", side_effect=run) as runner, TestClient(app) as client:
            response = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(client.get(self.receipt_path).json()["status"], "unconfirmed")
            retry = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(retry.status_code, 409)
        runner.assert_called_once()
        project = get_project(self.project.project_id, actor=self.actor)
        self.assertNotIn("execution", project.current_snapshots)
        self.assertEqual(project.execution_runs, [])
