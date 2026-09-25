from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
for directory in (BACKEND, BACKEND / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_use_case_review_service import FakeFirestoreClient, _run_transaction
from test_workflow_project_service import FakeCollection
from app.main import app, get_current_user
from app.models import AuthUser
from app.services import use_case_generation_service as service
from app.services.workflow_project_service import (
    ProjectConflictError,
    ProjectPermissionError,
    append_stage_snapshot,
    create_project,
    get_project,
    update_project,
)


def generated(requirements):
    return {
        "requirement_analysis": [{"requirement_id": r.id, "requirement_text": r.text} for r in requirements],
        "coverage_plan": [
            {
                "requirement_id": r.id,
                "requirement_text": r.text,
                "scenarios": [{"id": f"{r.id}-SCN-01", "requirement_id": r.id, "title": f"Check {r.id}", "objective": f"Verify {r.text}"}],
            }
            for r in requirements
        ],
        "approved": True,
        "review": {"approved": True},
        "workflow_diagnostics": {"status": "completed", "used_fallback": False},
    }


class UseCaseGenerationTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        patches = {
            "app.services.workflow_project_service.get_required_firestore_collection": {"return_value": FakeCollection("qa_projects", self.store)},
            "app.services.workflow_project_service.get_required_firestore_client": {"return_value": FakeFirestoreClient(self.store)},
            "app.services.workflow_project_service.transactional": {"side_effect": _run_transaction},
            "app.services.use_case_generation_service.enforce_billing_access": {},
            "app.services.use_case_generation_service.start_workflow_run": {"return_value": "run-generation"},
            "app.services.use_case_generation_service.complete_workflow_run": {},
            "app.services.use_case_generation_service.record_usage_event": {},
        }
        self.mocks = {}
        for target, kwargs in patches.items():
            p = patch(target, **kwargs)
            self.mocks[target.rsplit(".", 1)[-1]] = p.start()
            self.addCleanup(p.stop)
        p = patch.dict("os.environ", {"ADK_SKILLS_ENABLED": "false", "ADK_MEMORY_ENABLED": "false"})
        p.start()
        self.addCleanup(p.stop)
        project = create_project(name="Generation", description=None, actor=self.actor, request_id="create")
        self.project_id = project.project_id
        requirements = [{"id": f"REQ-{n:03d}", "text": f"Requirement {n}", "review_status": "Approved"} for n in range(1, 11)]
        self.save("requirements", {"requirements": requirements}, approved=True)
        self.save("context", {"notes": "Use the saved context", "grounded_context": {"summary": "Saved grounding"}}, approved=True)
        old = generated([service.Requirement.model_validate(r) for r in requirements[:8]])
        self.save("use_cases", old, approved=True)
        self.save("test_cases", {"test_cases": [{"id": "TC-existing", "title": "Keep me"}]}, approved=True)
        self.save("execution", {"runs": ["run-existing"]}, approved=True)
        self.save("reports", {"report": "Keep report"}, approved=True)
        self.project = get_project(self.project_id, actor=self.actor)
        self.old_snapshot = self.project.current_snapshots["use_cases"]
        self.before = deepcopy(self.store)
        p = patch.object(service, "generate_use_cases", side_effect=lambda payload, **kwargs: generated(payload.requirements))
        self.model = p.start()
        self.addCleanup(p.stop)

    def save(self, stage, payload, **kwargs):
        return append_stage_snapshot(
            project_id=self.project_id, stage=stage, payload=payload, actor=self.actor, request_id=f"seed-{stage}", operation="seed", **kwargs
        )

    def run_generation(self, **kwargs):
        return service.generate_project_use_cases(
            project_id=self.project_id, actor=self.actor, request_id="generate-1", base_project_revision=self.project.current_revision, **kwargs
        )

    def test_regenerates_all_ten_and_preserves_downstream_and_history(self):
        after = self.run_generation()
        payload = self.model.call_args.args[0]
        self.assertEqual(len(payload.requirements), 10)
        self.assertFalse(payload.coverage_plan)
        self.assertFalse(payload.requirement_analysis)
        self.assertEqual(payload.context.notes, "Use the saved context")
        self.assertEqual(len(payload.context.requirements), 10)
        new = after.current_snapshots["use_cases"]
        self.assertEqual(new.version, 2)
        self.assertEqual(len(new.payload["coverage_plan"]), 10)
        self.assertFalse(new.approved)
        self.assertFalse(after.stage_state["use_cases"].approved)
        self.assertFalse(after.stage_state["use_cases"].stale)
        self.assertEqual(new.source_snapshot_id, self.project.current_snapshots["requirements"].snapshot_id)
        self.assertEqual(new.metadata["guidance"]["stage"], "use_cases")
        for stage in ["test_cases", "execution", "reports"]:
            self.assertEqual(after.current_snapshots[stage], self.project.current_snapshots[stage])
            self.assertTrue(after.stage_state[stage].stale)
        path = f"qa_projects/{self.project_id}/snapshots/{self.old_snapshot.snapshot_id}"
        self.assertEqual(self.store[path], self.before[path])
        self.mocks["enforce_billing_access"].assert_called_once_with(current_user=self.actor, billing_key="testcases.generate")
        self.mocks["record_usage_event"].assert_called_once()

    def test_bad_inputs_fail_before_model(self):
        project_path = f"qa_projects/{self.project_id}"
        mutations = [
            lambda: self.store[project_path].update(status="archived"),
            lambda: self.store[project_path]["stage_state"]["requirements"].update(approved=False),
            lambda: self.store[project_path]["stage_state"]["requirements"].update(stale=True),
            lambda: self.store[project_path].update(current_revision=self.project.current_revision + 1),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.store.clear()
                self.store.update(deepcopy(self.before))
                mutate()
                with self.assertRaises((HTTPException, ProjectConflictError)):
                    self.run_generation()
        self.model.assert_not_called()
        self.mocks["enforce_billing_access"].assert_not_called()

    def test_other_owner_and_unapproved_row_cannot_generate(self):
        with self.assertRaises(ProjectPermissionError):
            service.generate_project_use_cases(
                project_id=self.project_id,
                actor=AuthUser(sub="other", email="other@example.com", name="Other"),
                request_id="other",
                base_project_revision=self.project.current_revision,
            )
        requirements = self.project.current_snapshots["requirements"]
        path = f"qa_projects/{self.project_id}/snapshots/{requirements.snapshot_id}"
        self.store[path]["payload"]["requirements"][0]["review_status"] = "Needs Review"
        with self.assertRaises(HTTPException):
            self.run_generation()
        self.model.assert_not_called()

    def test_failed_incomplete_or_malformed_output_preserves_snapshot(self):
        valid = generated([service.Requirement.model_validate(r) for r in self.project.current_snapshots["requirements"].payload["requirements"]])
        invalid = []
        for status in ["failed", "fallback"]:
            item = deepcopy(valid)
            item["workflow_diagnostics"]["status"] = status
            invalid.append(item)
        for field, value in [("used_fallback", True), ("failed_shard_count", 1), ("timed_out", True)]:
            item = deepcopy(valid)
            item["workflow_diagnostics"][field] = value
            invalid.append(item)
        for field in ["requirement_analysis", "coverage_plan"]:
            item = deepcopy(valid)
            item[field] = item[field][:8]
            invalid.append(item)
        item = deepcopy(valid)
        item["coverage_plan"][0]["scenarios"] = []
        invalid.append(item)
        item = deepcopy(valid)
        item["coverage_plan"][0]["scenarios"][0]["requirement_id"] = "unknown"
        invalid.append(item)
        invalid.append({"coverage_plan": "invalid"})
        for result in invalid:
            with self.subTest(result=result):
                self.model.side_effect = None
                self.model.return_value = result
                with self.assertRaises(HTTPException):
                    self.run_generation()
                self.assertEqual(self.store, self.before)
        self.mocks["record_usage_event"].assert_not_called()

    def test_advisory_reviewer_timeout_retains_valid_plan_as_new_unapproved_snapshot(self):
        valid = generated([service.Requirement.model_validate(r) for r in self.project.current_snapshots["requirements"].payload["requirements"]])
        valid["workflow_diagnostics"].update(status="partial", timed_out=True, failure_reason="semantic_review_timeout")
        self.model.side_effect = None
        self.model.return_value = valid
        after = self.run_generation()
        self.assertFalse(after.stage_state["use_cases"].approved)
        self.assertNotEqual(after.current_snapshots["use_cases"].snapshot_id, self.old_snapshot.snapshot_id)
        self.assertEqual(len(after.current_snapshots["use_cases"].payload["coverage_plan"]), 10)

    def test_model_exception_billing_and_guidance_failures_preserve_snapshot(self):
        for target in ["generate_use_cases", "enforce_billing_access", "prepare_run"]:
            with self.subTest(target=target), patch.object(service, target, side_effect=HTTPException(503, "Unavailable")):
                with self.assertRaises(HTTPException):
                    self.run_generation()
                self.assertEqual(self.store, self.before)

    def test_concurrent_edit_wins_and_generation_does_not_publish(self):
        def edit_then_generate(payload, **kwargs):
            update_project(
                project_id=self.project_id, actor=self.actor, request_id="other-edit", name="New name", base_project_revision=self.project.current_revision
            )
            return generated(payload.requirements)

        self.model.side_effect = edit_then_generate
        with self.assertRaises(ProjectConflictError):
            self.run_generation()
        after = get_project(self.project_id, actor=self.actor)
        self.assertEqual(after.name, "New name")
        self.assertEqual(after.current_snapshots, self.project.current_snapshots)
        self.mocks["record_usage_event"].assert_not_called()

    def test_unexpected_model_exception_has_generation_error_and_preserves_snapshot(self):
        self.model.side_effect = RuntimeError("Provider failure")
        with self.assertRaises(HTTPException) as raised:
            self.run_generation()
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("generation failed", raised.exception.detail)
        self.assertEqual(self.store, self.before)

    def test_endpoint_requires_auth_revision_and_rejects_client_artifacts(self):
        client = TestClient(app)
        url = f"/projects/{self.project_id}/use-cases/generate"
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        for payload in [{}, {"base_project_revision": self.project.current_revision, "requirements": []}]:
            self.assertEqual(client.post(url, json=payload).status_code, 422)
        app.dependency_overrides.clear()
        self.assertEqual(client.post(url, json={"base_project_revision": self.project.current_revision}).status_code, 401)
        self.model.assert_not_called()

    def test_endpoint_success_and_conflict(self):
        client = TestClient(app)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        url = f"/projects/{self.project_id}/use-cases/generate"
        payload = {"base_project_revision": self.project.current_revision}
        response = client.post(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["stage_state"]["use_cases"]["approved"])
        self.assertEqual(client.post(url, json=payload).status_code, 409)
        self.assertEqual(self.model.call_count, 1)
