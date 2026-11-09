from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys
from threading import Event
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_workflow_project_service as fixtures
from test_use_case_review_service import FakeTransaction
from app.main import app, get_current_user
from app.models import ExecutionCandidate, ExecutionIssue, ExecutionPreviewResponse, ExecutionRunResponse
from app.services.workflow_project_service import append_stage_snapshot, create_project, get_project, update_project


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
        self.enterContext(
            patch(
                "app.services.audit_repository.get_optional_firestore_collection",
                side_effect=lambda name, **kwargs: self.transaction_client.collection(name),
            )
        )

    def run_response(self, run_id="exec-saved", status="passed"):
        return ExecutionRunResponse(
            status=status,
            run_id=run_id,
            preview=self.preview.model_copy(deep=True),
            summary={status: 1},
            results=[{"id": "candidate-1", "source_test_case_id": "TC-1", "title": "Confirm receipt", "status": status}],
            artifacts_root="/tmp/fixture-artifacts",
            playwright_report_paths=["/tmp/fixture-artifacts/playwright-report"],
        )

    def project_detail(self):
        return get_project(self.project.project_id, actor=self.actor)

    def audit(self, collection):
        return [row for path, row in self.store.items() if path.startswith(f"{collection}/")]

    def assert_only_run_audit(self, status, quantity):
        [usage] = self.audit("usage_events")
        [workflow] = self.audit("workflow_runs")
        self.assertEqual((usage["status"], usage["quantity"], workflow["status"]), (status, quantity, status))
        self.assertEqual(usage["workflow_run_id"], workflow["run_id"])
        return usage

    def test_overlapping_request_and_completed_replay_launch_once(self):
        entered, release = Event(), Event()

        def run(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Synthetic browser release timed out")
            outcome = self.run_response(status="flaky")
            outcome.results[0].issues = [ExecutionIssue(code="playwright.error", message="First attempt timed out")]
            return outcome

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
            self.assertEqual(response.json()["run_record_id"], completed["run_record_id"])
            self.assertFalse(completed["historical"])
            self.assertEqual(completed["source_snapshot_id"], self.snapshot.snapshot_id)
            replay = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(replay.status_code, 409)
            self.assertEqual(replay.json()["detail"]["receipt"], completed)
            exact = client.get(f"{self.prefix}/execution-runs/{completed['run_record_id']}")
            self.assertEqual(exact.status_code, 200, exact.text)
            self.assertEqual(exact.json()["run_id"], completed["run_id"])
            self.assertEqual(exact.json()["status"], "flaky")
            self.assertEqual(exact.json()["summary"]["flaky"], 1)
            self.assertEqual(exact.json()["results"][0]["status"], "flaky")
            self.assertEqual(exact.json()["results"][0]["issues"][0]["message"], "First attempt timed out")
            original_actor = self.actor
            self.actor = original_actor.model_copy(update={"sub": "another-user"})
            self.assertEqual(client.get(self.receipt_path).status_code, 403)
            self.assertEqual(client.post("/automation/execution/run", json=self.body, headers=self.headers).status_code, 403)
            self.actor = original_actor
        runner.assert_called_once()
        audit = self.assert_only_run_audit("completed", 1)
        saved = self.project_detail()
        self.assertEqual(saved.current_revision, self.body["base_project_revision"] + 2)
        execution = saved.current_snapshots["execution"]
        report = saved.current_snapshots["reports"]
        self.assertFalse(execution.approved)
        self.assertFalse(report.approved)
        self.assertEqual(audit["metadata"]["flaky_count"], 1)
        self.assertTrue(any("/quality_findings/" in path for path in self.store))
        self.assertEqual(report.source_snapshot_id, execution.snapshot_id)
        self.assertEqual(report.source_event_id, execution.source_event_id)
        self.assertEqual(execution.source_event_id, audit["event_id"])
        self.assertEqual(saved.execution_runs[0].source_event_id, audit["event_id"])

    def test_unconfirmed_transaction_rolls_back_evidence_and_never_relaunches(self):
        def run(*args, **kwargs):
            self.transaction_client.fail_on_create_segment = "/execution_runs/"
            return self.run_response("exec-uncertain")

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
        self.assert_only_run_audit("failed", 0)

    def test_report_fault_rolls_back_run_and_completion_audit(self):
        create = FakeTransaction.create
        staged = []

        def reject_report(transaction, reference, data):
            if data.get("operation") == "reports.execution_summary":
                staged.extend(transaction.operations)
                raise RuntimeError("Synthetic report write failure")
            return create(transaction, reference, data)

        before = self.project_detail()
        with (
            patch.object(FakeTransaction, "create", reject_report),
            patch("app.main.run_execution", return_value=self.run_response()) as runtime,
            TestClient(app) as client,
        ):
            result = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(client.get(self.receipt_path).json()["status"], "unconfirmed")
            self.assertEqual(client.post("/automation/execution/run", json=self.body, headers=self.headers).status_code, 409)
        runtime.assert_called_once()
        self.assertEqual(self.project_detail(), before)
        for segment in ("usage_events/", "workflow_runs/", "/execution_runs/", "/execution_requests/", "/snapshots/"):
            self.assertTrue(any(segment in path for _, path in staged), segment)
        self.assert_only_run_audit("failed", 0)

    def test_completed_run_after_context_change_is_saved_with_its_original_suite(self):
        new_project = None
        diagnostic = "検証結果 🧪 " * 37_000

        def run(*args, **kwargs):
            nonlocal new_project
            append_stage_snapshot(
                project_id=self.project.project_id,
                stage="context",
                payload={"notes": "Changed after browser admission"},
                operation="requirements.enrich",
                actor=self.actor,
                request_id="changed-context",
                approved=True,
            )
            new_project = self.project_detail()
            outcome = self.run_response(status="flaky")
            outcome.results[0].issues = [ExecutionIssue(code="fixture", message=diagnostic)]
            return outcome

        with patch("app.main.run_execution", side_effect=run) as runtime, TestClient(app) as client:
            response = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            receipt = client.get(self.receipt_path).json()
            self.assertTrue(receipt["historical"])
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(response.json()["run_record_id"], receipt["run_record_id"])
            detail = client.get(f"{self.prefix}/execution-runs/{receipt['run_record_id']}").json()
            self.assertEqual(detail["source_snapshot_id"], self.snapshot.snapshot_id)
            self.assertEqual(detail["project_revision"], self.body["base_project_revision"])
            self.assertTrue(detail["historical"])
            self.assertEqual(detail["status"], "flaky")
            self.assertEqual(detail["summary"]["flaky"], 1)
            self.assertEqual(detail["results"][0]["status"], "flaky")
            self.assertEqual(detail["results"][0]["issues"][0]["message"], diagnostic)
            self.assertEqual(client.post("/automation/execution/run", json=self.body, headers=self.headers).status_code, 409)
        runtime.assert_called_once()
        saved = self.project_detail()
        self.assertEqual(saved.current_revision, new_project.current_revision)
        self.assertEqual(saved.stage_state, new_project.stage_state)
        self.assertEqual(saved.current_snapshots, new_project.current_snapshots)
        self.assertFalse(any("/quality_findings/" in path for path in self.store))
        self.assertTrue(any("/execution_result_parts/" in path for path in self.store))
        self.assertEqual(self.assert_only_run_audit("completed", 1)["event_id"], saved.execution_runs[0].source_event_id)

    def test_completed_run_after_archive_keeps_archive_and_retains_exact_result(self):
        def run(*args, **kwargs):
            update_project(project_id=self.project.project_id, actor=self.actor, request_id="archive", status_value="archived")
            return self.run_response()

        with patch("app.main.run_execution", side_effect=run), TestClient(app) as client:
            response = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["historical"])
        saved = self.project_detail()
        self.assertEqual(saved.status, "archived")
        self.assertEqual(saved.current_revision, self.body["base_project_revision"] + 1)
        self.assertNotIn("reports", saved.current_snapshots)
        self.assertEqual(saved.execution_runs[0].run_id, "exec-saved")

    def test_lost_commit_reply_reconciles_completed_receipt_without_failed_audit(self):
        commit = FakeTransaction.commit
        lost_replies = []

        def lose_reply(transaction):
            run_publication = any("/execution_runs/" in path and action == "create" for action, path in transaction.operations)
            commit(transaction)
            if run_publication:
                lost_replies.append(True)
                raise RuntimeError("Synthetic transaction reply lost")

        with (
            patch.object(FakeTransaction, "commit", lose_reply),
            patch("app.main.run_execution", return_value=self.run_response()) as runtime,
            TestClient(app) as client,
        ):
            response = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            before = deepcopy(self.store)
            replay = client.post("/automation/execution/run", json=self.body, headers=self.headers)
            self.assertEqual(replay.status_code, 409)
            self.assertEqual(replay.json()["detail"]["receipt"]["run_record_id"], response.json()["run_record_id"])
            self.assertEqual(before, self.store)
        runtime.assert_called_once()
        self.assertEqual(lost_replies, [True])
        self.assert_only_run_audit("completed", 1)
