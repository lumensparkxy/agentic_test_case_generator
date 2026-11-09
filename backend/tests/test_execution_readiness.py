from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution_fixtures import case, project_fixture

from app.config import ExecutionSettings
from app.contracts.execution import ExecutionCandidate, ExecutionPreviewInput, ExecutionPreviewResponse, ExecutionRunResponse
from app.contracts.projects import QaProjectStageSnapshot, QaProjectStageState
from app.main import app, get_current_user
from app.models import AuthUser
from app.routers import automation as routes
from app.services import workflow_project_service as projects
from app.services.execution_readiness import assess_execution, selected_candidates
from app.services.workflow_project_service import ProjectPermissionError
from test_use_case_review_service import FakeFirestoreClient, _run_transaction


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


def persisted_preview(**kwargs):
    return SimpleNamespace(snapshot_id="saved-execution", project_revision=8, workflow_run_id=kwargs["workflow_run_id"], metadata=kwargs["metadata"])


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
            ("app.routers.automation.find_idempotent_stage_snapshot", {"return_value": None}),
            ("app.routers.automation.get_required_firestore_client", {"return_value": object()}),
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
        with (
            patch("app.routers.automation.get_project", return_value=project),
            patch("app.main.preview_execution", return_value=preview_fixture()),
            patch("app.routers.automation.append_stage_snapshot", side_effect=persisted_preview),
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

    def test_run_rechecks_unsupported_expectation_even_with_other_executable_cases(self):
        supported = case()
        supported["steps"] = [{"step": 1, "action": "Open /checkout", "expected": 'heading "Checkout" is visible'}]
        blocked = {
            **supported,
            "id": "TC-AUDIT",
            "steps": [
                *supported["steps"],
                {
                    "step": 2,
                    "action": 'Click the "Submit" button',
                    "expected": "An immutable audit record is created in the database for this submission.",
                },
            ],
        }
        cases = [supported, blocked]
        with (
            patch("app.routers.automation.get_project", return_value=project_fixture(cases)),
            patch("app.routers.automation.append_stage_snapshot", side_effect=persisted_preview),
            patch("app.main.run_execution") as runtime,
            TestClient(app) as client,
        ):
            payload = {**request_fixture(), "test_cases": cases}
            preview = client.post("/automation/execution/preview", json=payload)
            self.assertEqual(preview.status_code, 200, preview.text)
            evidence = preview.json()
            self.assertEqual(evidence["summary"], {"executable": 1, "manual": 0, "unsupported": 1, "invalid": 0})
            self.assertEqual(evidence["unsupported"][0]["source_test_case_id"], "TC-AUDIT")
            self.assertTrue(evidence["readiness"]["run_allowed"])
            for candidate_id in ("tc_audit", "TC-AUDIT"):
                with self.subTest(candidate_id=candidate_id):
                    response = client.post("/automation/execution/run", json={**payload, "selected_test_case_ids": ["tc_1", candidate_id]})
                    self.assertEqual(response.status_code, 409, response.text)
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

    def test_origin_binding_uses_verified_saved_context_for_preview_and_launch(self):
        source = "http://127.0.0.1:8198"
        target = "http://127.0.0.1:8199"
        authored = {
            **case(),
            "steps": [{"step": 1, "action": f'Open "{source}/checkout?order=1#confirm"', "expected": 'exact heading "Checkout" is visible'}],
        }
        project = project_fixture([authored])
        project.current_snapshots["context"] = QaProjectStageSnapshot(
            snapshot_id="saved-context",
            project_id=project.project_id,
            stage="context",
            version=1,
            project_revision=6,
            operation="requirements.enrich",
            approved=True,
            payload={"app_link": source},
            created_at=project.created_at,
        )
        project.stage_state["context"] = QaProjectStageState(current_snapshot_id="saved-context", approved=True)
        response = ExecutionRunResponse(status="passed", run_id="exec-target", preview=preview_fixture())
        payload = {**request_fixture(), "test_cases": [authored], "selected_test_case_ids": ["tc_1"]}

        with (
            patch("app.routers.automation.get_project", return_value=project),
            patch("app.routers.automation.append_stage_snapshot", side_effect=persisted_preview),
            patch(
                "app.routers.automation.publish_execution_outcome",
                return_value=SimpleNamespace(run_record_id="saved-run", historical=False),
            ),
            patch("app.main.run_execution", return_value=response) as runtime,
            TestClient(app) as client,
        ):
            # Older suites can use the still-current context that preceded them.
            for source_link in (None, "saved-context"):
                if source_link:
                    project.current_snapshots["test_cases"].metadata["source_context_snapshot_id"] = source_link
                preview = client.post("/automation/execution/preview", json=payload)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.json()["executable"][0]["spec"]["steps"][0], f'Given I open "{target}/checkout?order=1#confirm"')
            allowed = client.post("/automation/execution/run", json=payload)
            self.assertEqual(allowed.status_code, 200, allowed.text)
            runtime.assert_called_once()
            self.assertEqual(runtime.call_args.kwargs["source_app_url"], source)
            self.assertEqual(runtime.call_args.kwargs["target_base_url"], f"{target}/")
            self.assertEqual(runtime.call_args.kwargs["selected_test_case_ids"], ["tc_1"])

            project.stage_state["context"].approved = False
            preview = client.post("/automation/execution/preview", json=payload)
            self.assertEqual(preview.json()["unsupported"][0]["unsupported_steps"][0]["reason_code"], "execution.target_origin_mismatch")
            rejected = client.post("/automation/execution/run", json=payload)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            runtime.assert_called_once()

    def test_current_approved_run_uses_captured_source_and_selected_ids(self):
        project = project_fixture()
        response = ExecutionRunResponse(status="passed", run_id="exec-1", preview=preview_fixture())
        with (
            patch("app.routers.automation.get_project", return_value=project) as read,
            patch("app.main.preview_execution", return_value=preview_fixture()),
            patch("app.main.run_execution", return_value=response) as runtime,
            patch(
                "app.routers.automation.publish_execution_outcome",
                return_value=SimpleNamespace(run_record_id="saved-run", historical=False),
            ) as persist,
            TestClient(app) as client,
        ):
            result = client.post("/automation/execution/run", json=request_fixture())
        self.assertEqual(result.status_code, 200, result.text)
        runtime.assert_called_once()
        read.assert_called_once()
        self.assertEqual(runtime.call_args.kwargs["selected_test_case_ids"], ["candidate-1"])
        self.assertEqual(persist.call_args.args[0].preview.readiness.source_snapshot_id, "tests-1")
        self.assertEqual(persist.call_args.kwargs["selected_ids"], ["candidate-1"])
        self.assertEqual(result.json()["run_id"], "exec-1")
        stale_project = deepcopy(project)
        stale_project.current_revision = 9
        with patch("app.routers.automation.get_project", return_value=stale_project), patch("app.main.run_execution") as runtime, TestClient(app) as client:
            stale = client.post("/automation/execution/run", json=request_fixture())
        self.assertEqual(stale.status_code, 409)
        runtime.assert_not_called()


class ExecutionPreviewPublicationTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.firestore = FakeFirestoreClient(self.store)
        self.actor = AuthUser(sub="preview-owner", name="Preview owner")
        self.enterContext(patch.dict(app.dependency_overrides, {get_current_user: lambda: self.actor}))
        self.enterContext(patch.object(projects, "get_required_firestore_collection", return_value=self.firestore.collection("qa_projects")))
        self.enterContext(patch.object(projects, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(patch.object(projects, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(routes, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(patch.object(routes, "get_execution_settings", return_value=ExecutionSettings()))
        self.enterContext(
            patch("app.services.audit_repository.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.firestore.collection(name))
        )
        self.compiler = self.enterContext(patch("app.main.preview_execution", wraps=routes.preview_execution))
        self.project_id = projects.create_project(name="Preview publication checks", description=None, actor=self.actor, request_id="seed").project_id
        self.cases = [{**case(), "steps": [{"step": 1, "action": "Open /checkout", "expected": 'exact heading "Checkout" is visible'}]}]
        self.save("requirements", {"requirements": [{"id": "REQ-1", "text": "A checkout page is visible.", "review_status": "Approved"}]})
        self.suite = self.save("test_cases", {"test_cases": self.cases, "generation_tasks": []})
        self.save("execution", {"target_environment": "prior", "summary": {"executable": 0}}, source_snapshot_id=self.suite.snapshot_id)
        self.save("reports", {"source": "seed"})
        self.before = self.project()
        self.body = {
            "project_id": self.project_id,
            "base_project_revision": self.before.current_revision,
            "test_cases": self.cases,
            "target_base_url": "https://preview.example.test/",
            "target_environment": "qa",
        }
        self.http = self.enterContext(TestClient(app))

    def save(self, stage, payload, **kwargs):
        return projects.append_stage_snapshot(
            project_id=self.project_id, stage=stage, payload=payload, operation="seed", actor=self.actor, request_id=f"seed-{stage}", approved=True, **kwargs
        )

    def project(self):
        return projects.get_project(self.project_id, actor=self.actor)

    def post(self, request_id="preview", **changes):
        return self.http.post("/automation/execution/preview", headers={"X-Request-ID": request_id}, json={**self.body, **changes})

    def audit(self, collection):
        return [value for path, value in self.store.items() if path.startswith(f"{collection}/")]

    def assert_audit(self, status, quantity):
        [event] = self.audit("usage_events")
        [workflow] = self.audit("workflow_runs")
        self.assertEqual((event["status"], event["quantity"], workflow["status"]), (status, quantity, status))
        self.assertEqual(event["workflow_run_id"], workflow["run_id"])
        return event

    def test_exact_preview_replay_preserves_source_target_revision_and_one_linked_audit(self):
        first = self.post()
        self.assertEqual(first.status_code, 200, first.text)
        document = first.json()
        self.assertEqual(document["summary"]["executable"], 1)
        self.assertTrue(document["readiness"]["run_allowed"])
        self.assertEqual(self.post().json(), document)
        saved = self.project()
        self.assertEqual(saved.current_revision, self.before.current_revision + 1)
        snapshot = saved.current_snapshots["execution"]
        self.assertEqual(snapshot.payload["readiness"]["project_revision"], saved.current_revision)
        self.assertEqual(snapshot.source_snapshot_id, self.suite.snapshot_id)
        self.assertEqual(snapshot.payload["target_environment"], "qa")
        self.assertNotIn("spec", snapshot.payload["candidates"]["executable"][0])
        self.assertEqual(snapshot.payload["target_base_url"], "https://preview.example.test/")
        self.assertEqual(saved.stage_state["reports"], self.before.stage_state["reports"])
        self.assertEqual(saved.current_snapshots["reports"], self.before.current_snapshots["reports"])
        self.assertEqual(self.assert_audit("completed", 1)["event_id"], snapshot.source_event_id)
        self.assertEqual(self.post(target_base_url="https://changed.example.test").status_code, 409)
        self.assertEqual(self.post(target_environment="changed").status_code, 409)
        self.assertEqual(self.post(test_cases=[{**self.cases[0], "automation_status": "Manual"}]).status_code, 409)
        self.assertEqual(self.compiler.call_count, 2)
        self.save("execution", {"target_environment": "newer"}, source_snapshot_id=self.suite.snapshot_id)
        self.assertEqual(self.post().status_code, 409)
        self.assert_audit("completed", 1)

    def test_replay_rejects_candidate_evidence_that_changed_since_publication(self):
        self.assertEqual(self.post().status_code, 200)
        self.compiler.return_value = ExecutionPreviewResponse()
        before = deepcopy(self.store)
        result = self.post()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn("Execution readiness has changed", result.text)
        self.assertEqual(self.store, before)

    def test_preview_rejects_forged_missing_revision_archived_and_foreign_inputs_before_compiling(self):
        self.assertEqual(self.post(test_cases=[{**self.cases[0], "automation_status": "Manual"}]).status_code, 409)
        self.assertEqual(self.post(base_project_revision=None).status_code, 409)
        self.assertEqual(self.post(base_project_revision=self.before.current_revision - 1).status_code, 409)
        owner = self.actor
        self.actor = AuthUser(sub="foreign-user", name="Foreign user")
        self.assertEqual(self.post().status_code, 403)
        self.actor = owner
        self.assertEqual(self.project(), self.before)
        archived = projects.update_project(project_id=self.project_id, actor=owner, request_id="archive", status_value="archived")
        self.assertEqual(self.post(base_project_revision=archived.current_revision).status_code, 409)
        self.compiler.assert_not_called()
        self.assertFalse(self.audit("usage_events"))
        self.assertFalse(self.audit("workflow_runs"))

    def test_stale_revision_after_compilation_keeps_previous_preview_without_completed_usage(self):
        def advance(*args, **kwargs):
            projects.update_project(project_id=self.project_id, actor=self.actor, request_id="rename", name="Changed during preview")
            return routes.preview_execution(*args, **kwargs)

        self.compiler.side_effect = advance
        response = self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.project().current_snapshots, self.before.current_snapshots)
        self.assert_audit("failed", 0)

    def test_preview_and_audit_roll_back_together_after_snapshot_write_failure(self):
        self.firestore.fail_on_create_segment = "/snapshots/"
        response = self.post()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.project(), self.before)
        transaction = self.firestore.transactions[-1]
        writes = [path.split("/")[0] for action, path in transaction.operations if action == "set"]
        self.assertEqual(writes[:2], ["usage_events", "workflow_runs"])
        self.assert_audit("failed", 0)
