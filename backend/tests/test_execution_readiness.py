from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution_fixtures import case, project_fixture

from app.config import ExecutionSettings
from app.contracts.execution import ExecutionCandidate, ExecutionPreviewInput, ExecutionPreviewResponse, ExecutionRunResponse
from app.main import app, get_current_user
from app.models import AuthUser
from app.services.execution_readiness import assess_execution, selected_candidates
from app.services.workflow_project_service import ProjectPermissionError


def request_fixture():
    return {
        "project_id": "project-1",
        "base_project_revision": 7,
        "test_cases": [case()],
        "target_base_url": "http://127.0.0.1:8199",
        "selected_test_case_ids": ["candidate-1"],
    }


def preview_fixture():
    return ExecutionPreviewResponse(executable=[ExecutionCandidate(id="candidate-1", source_test_case_id="TC-1", title="Show checkout", status="executable")])


class ExecutionReadinessTests(unittest.TestCase):
    def test_target_must_be_explicit_or_configured_not_implicit_fallback(self):
        payload = ExecutionPreviewInput.model_validate({**request_fixture(), "target_base_url": None})
        missing = assess_execution(payload, project=project_fixture(), settings=ExecutionSettings())
        self.assertFalse(missing.run_allowed)
        self.assertEqual(missing.blockers[0].code, "target_required")
        configured = assess_execution(
            payload, project=project_fixture(), settings=ExecutionSettings(default_base_url="https://example.test/", default_base_url_configured=True)
        )
        self.assertTrue(configured.run_allowed)
        self.assertEqual(configured.target_source, "configured_default")
        self.assertEqual(configured.target_base_url, "https://example.test/")
        invalid = assess_execution(
            payload, project=project_fixture(), settings=ExecutionSettings(default_base_url="not a URL", default_base_url_configured=True)
        )
        self.assertFalse(invalid.run_allowed)
        self.assertIn("invalid_target", [block.code for block in invalid.blockers])

    def test_selection_never_silently_runs_all_or_unsupported_candidates(self):
        preview = preview_fixture()
        self.assertEqual(selected_candidates(preview, ["TC-1"]), ["candidate-1"])
        self.assertEqual(selected_candidates(preview, ["candidate-1", "candidate-1"]), ["candidate-1"])
        for selection in ([], ["manual-1"], ["missing"]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                selected_candidates(preview, selection)
        preview.executable.append(ExecutionCandidate(id="candidate-2", source_test_case_id="TC-1", title="Other checkout", status="executable"))
        with self.assertRaises(ValueError):
            selected_candidates(preview, ["TC-1"])
        self.assertEqual(selected_candidates(preview, ["candidate-2"]), ["candidate-2"])


class ExecutionAdmissionTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="qa-user", email="qa@example.test", name="QA", provider="google.com")
        self.addCleanup(app.dependency_overrides.clear)
        for target, kwargs in (
            ("app.main.start_workflow_run", {"return_value": "audit-run"}),
            ("app.main.complete_workflow_run", {}),
            ("app.main.record_usage_event", {"return_value": "event-1"}),
            ("app.routers.automation.get_execution_settings", {"return_value": ExecutionSettings()}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_all_blocked_requests_are_rejected_before_runtime_invocation(self):
        variants = [
            "revision",
            "missing_revision",
            "unapproved_requirements",
            "unapproved_use_cases",
            "old_human_review",
            "unapproved_test_cases",
            "stale_test_cases",
            "stale_requirements",
            "stale_use_cases",
            "forged_cases",
            "incomplete",
            "missing_snapshot",
            "different_snapshot",
            "old_source",
            "target",
            "archived",
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                payload, project = request_fixture(), project_fixture()
                if variant == "revision":
                    payload["base_project_revision"] = 6
                elif variant == "missing_revision":
                    del payload["base_project_revision"]
                elif variant.startswith("unapproved_"):
                    project.stage_state[variant.removeprefix("unapproved_")].approved = False
                elif variant.startswith("stale_"):
                    project.stage_state[variant.removeprefix("stale_")].stale = True
                elif variant == "old_human_review":
                    project.stage_state["use_cases"].metadata["latest_human_review"]["snapshot_id"] = "old"
                elif variant == "forged_cases":
                    payload["test_cases"][0]["steps"][0]["action"] = "Open /forged"
                elif variant == "incomplete":
                    project.current_snapshots["test_cases"].payload["generation_tasks"] = [{"reason": "Missing cancellation"}]
                elif variant == "missing_snapshot":
                    project.current_snapshots.clear()
                elif variant == "different_snapshot":
                    project.stage_state["test_cases"].current_snapshot_id = "other"
                elif variant == "old_source":
                    project.current_snapshots["test_cases"].metadata["source_requirements_snapshot_id"] = "old"
                elif variant == "target":
                    del payload["target_base_url"]
                elif variant == "archived":
                    project.status = "archived"
                payload["readiness"] = {"run_allowed": True}
                with (
                    patch("app.routers.automation.get_project", return_value=project),
                    patch("app.main.run_execution") as runtime,
                    patch("app.main.preview_execution") as preview,
                    TestClient(app) as client,
                ):
                    response = client.post("/automation/execution/run", json=payload)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertFalse(response.json()["detail"]["readiness"]["run_allowed"])
                runtime.assert_not_called()
                preview.assert_not_called()

    def test_unauthorized_project_never_reaches_preview_or_runtime(self):
        with (
            patch("app.routers.automation.get_project", side_effect=ProjectPermissionError("project-1")),
            patch("app.main.run_execution") as runtime,
            patch("app.main.preview_execution") as preview,
            TestClient(app) as client,
        ):
            response = client.post("/automation/execution/run", json=request_fixture())
        self.assertEqual(response.status_code, 403)
        runtime.assert_not_called()
        preview.assert_not_called()

    def test_invalid_selection_never_invokes_runtime(self):
        for selected in ([], ["unsupported"], ["unknown"]):
            with (
                self.subTest(selected=selected),
                patch("app.routers.automation.get_project", return_value=project_fixture()),
                patch("app.main.preview_execution", return_value=preview_fixture()),
                patch("app.main.run_execution") as runtime,
                TestClient(app) as client,
            ):
                response = client.post("/automation/execution/run", json={**request_fixture(), "selected_test_case_ids": selected})
                self.assertEqual(response.status_code, 409, response.text)
                runtime.assert_not_called()

    def test_draft_preview_reports_blockers_but_remains_available(self):
        project = project_fixture()
        project.stage_state["test_cases"].approved = False
        persisted = type("Snapshot", (), {"project_revision": 8})()
        with (
            patch("app.routers.automation.get_project", return_value=project),
            patch("app.main.preview_execution", return_value=preview_fixture()),
            patch("app.routers.automation.append_stage_snapshot", return_value=persisted),
            patch("app.main.run_execution") as runtime,
            TestClient(app) as client,
        ):
            response = client.post("/automation/execution/preview", json=request_fixture())
        self.assertEqual(response.status_code, 200, response.text)
        readiness = response.json()["readiness"]
        self.assertFalse(readiness["run_allowed"])
        self.assertEqual(readiness["project_revision"], 8)
        self.assertEqual(readiness["source_snapshot_id"], "tests-1")
        self.assertIn("unapproved_test_cases", [item["code"] for item in readiness["blockers"]])
        runtime.assert_not_called()

    def test_disabled_execution_and_no_eligible_candidates_never_invoke_runtime(self):
        for enabled in (False, True):
            with (
                self.subTest(enabled=enabled),
                patch("app.routers.automation.get_execution_settings", return_value=ExecutionSettings(enabled=enabled)),
                patch("app.routers.automation.get_project", return_value=project_fixture()),
                patch("app.main.preview_execution", return_value=ExecutionPreviewResponse()),
                patch("app.main.run_execution") as runtime,
                TestClient(app) as client,
            ):
                response = client.post("/automation/execution/run", json=request_fixture())
                self.assertEqual(response.status_code, 409)
                expected = "no_executable_candidates" if enabled else "execution_disabled"
                self.assertIn(expected, [item["code"] for item in response.json()["detail"]["readiness"]["blockers"]])
                runtime.assert_not_called()

    def test_current_approved_run_uses_captured_source_and_selected_ids(self):
        project = project_fixture()
        response = ExecutionRunResponse(status="passed", run_id="exec-1", preview=preview_fixture())
        snapshots = [
            type("Snapshot", (), {"snapshot_id": "exec-snapshot", "project_revision": 8})(),
            type("Snapshot", (), {"snapshot_id": "report-snapshot", "project_revision": 9})(),
        ]
        with (
            patch("app.routers.automation.get_project", return_value=project) as read,
            patch("app.main.preview_execution", return_value=preview_fixture()),
            patch("app.main.run_execution", return_value=response) as runtime,
            patch("app.routers.automation.append_stage_snapshot", side_effect=snapshots) as persist,
            patch("app.routers.automation.record_execution_run"),
            TestClient(app) as client,
        ):
            result = client.post("/automation/execution/run", json=request_fixture())
        self.assertEqual(result.status_code, 200, result.text)
        runtime.assert_called_once()
        read.assert_called_once()
        self.assertEqual(runtime.call_args.kwargs["selected_test_case_ids"], ["candidate-1"])
        self.assertEqual(persist.call_args_list[0].kwargs["source_snapshot_id"], "tests-1")
        self.assertEqual(result.json()["run_id"], "exec-1")
        stale_project = deepcopy(project)
        stale_project.current_revision = 9
        with patch("app.routers.automation.get_project", return_value=stale_project), patch("app.main.run_execution") as runtime, TestClient(app) as client:
            stale = client.post("/automation/execution/run", json=request_fixture())
        self.assertEqual(stale.status_code, 409)
        runtime.assert_not_called()
