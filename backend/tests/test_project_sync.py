from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_workflow_project_service as fixtures
from fastapi import HTTPException
from app.contracts.project_sync import AcceptProjectFinding, RequirementCorrection
from app.contracts.quality_feedback import QualityFindingInput
from app.contracts.knowledge import KnowledgeMutation
from app.services import workflow_project_service as projects, project_sync as sync
from app.services.quality_feedback import capture_finding
from app.services.guidance_runtime import prepare_run
from app.services.guidance_service import guidance_scope, guidance_text, submit_with_guidance
from app.services.behaviour_maintenance import maintenance_scope
from app.services.knowledge_service import mutate_knowledge


class ProjectSyncEndpointTests(unittest.TestCase):
    def test_source_conflicts_and_validation_keep_their_http_status(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routers.project_sync import router, get_current_user
        from app.models import AuthUser

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="test-owner", email="owner@example.com", name="Test owner")
        with TestClient(app) as client:
            for status, detail in ((409, "Project changed. Reload before updating feedback."), (422, "Resolve the business rule explicitly")):
                with self.subTest(status=status), patch.object(sync, "accept_finding", side_effect=HTTPException(status, detail)):
                    response = client.post(
                        "/projects/test-project/changes/accept",
                        headers={"X-Request-ID": "test-request"},
                        json={"base_project_revision": 1, "finding_id": "finding-1", "decision": "Maximum is six"},
                    )
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.json(), {"detail": detail})


class ProjectSyncTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowProjectServiceTests.setUp(self)
        self.enterContext(patch.dict("os.environ", {"ADK_MEMORY_ENABLED": "false", "ADK_SKILLS_ENABLED": "false"}))
        from test_use_case_review_service import _run_transaction

        self.enterContext(patch("app.services.guidance_runtime.get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(patch("app.services.guidance_runtime.transactional", side_effect=_run_transaction))
        from app.models import TestCase

        self.enterContext(
            patch.object(sync, "persist_test_case_versions", side_effect=lambda **kwargs: [TestCase.model_validate(c) for c in kwargs["current_test_cases"]])
        )
        self.enterContext(patch.object(sync, "record_usage_event", return_value="usage"))
        self.enterContext(patch.object(sync, "record_billing_consumption"))

    tearDown = fixtures.WorkflowProjectServiceTests.tearDown

    def fixture(self):
        project = projects.create_project(name="Sync", description=None, actor=self.actor, request_id="create")
        self.pid = project.project_id
        requirements = [{"id": "R1", "text": "Limit is 8", "review_status": "Approved"}, {"id": "R2", "text": "Show receipt", "review_status": "Approved"}]
        self.preserved = {
            "id": "TC2",
            "title": "Receipt",
            "linked_requirement_ids": ["R2"],
            "steps": [{"step": 1, "action": "Submit", "expected": "Receipt appears"}],
        }
        for stage, payload in (
            ("requirements", {"requirements": requirements}),
            ("context", {"requirements": requirements, "notes": "Existing context"}),
            (
                "use_cases",
                {
                    "requirement_analysis": [
                        {"requirement_id": "R1", "requirement_text": "Limit is 8"},
                        {"requirement_id": "R2", "requirement_text": "Show receipt"},
                    ],
                    "coverage_plan": [
                        {
                            "requirement_id": "R2",
                            "requirement_text": "Show receipt",
                            "scenarios": [{"id": "S2", "requirement_id": "R2", "title": "Receipt", "objective": "Show receipt"}],
                        }
                    ],
                },
            ),
            (
                "test_cases",
                {
                    "requirement_analysis": [
                        {"requirement_id": "R1", "requirement_text": "Limit is 8"},
                        {"requirement_id": "R2", "requirement_text": "Show receipt"},
                    ],
                    "generation_evidence": {"request_id": "baseline-generation"},
                    "iteration_history": [{"approved": True, "summary": "Baseline approved"}],
                    "test_cases": [
                        {"id": "TC1", "title": "Limit", "linked_requirement_ids": ["R1"], "steps": [{"step": 1, "action": "Enter 8", "expected": "Accepted"}]},
                        self.preserved,
                    ],
                },
            ),
        ):
            projects.append_stage_snapshot(
                project_id=self.pid, actor=self.actor, request_id=stage, operation="fixture", stage=stage, payload=payload, approved=True
            )
        project = projects.get_project(self.pid, actor=self.actor)
        finding = capture_finding(
            self.pid,
            self.actor,
            QualityFindingInput(
                base_project_revision=project.current_revision,
                source_snapshot_id=project.current_snapshots["requirements"].snapshot_id,
                category="ambiguous_requirement",
                summary="Limit changed",
                expected_behavior="Limit is 6",
                requirement_ids=["R1"],
            ),
            "finding",
        )
        self.payload = AcceptProjectFinding(
            base_project_revision=finding["project_revision"],
            finding_id=finding["finding"]["id"],
            decision="Maximum is six",
            corrections=[RequirementCorrection(requirement_id="R1", previous_text="Limit is 8", text="Limit is 6")],
        )
        return projects.get_project(self.pid, actor=self.actor)

    def planner(self, payload, **kwargs):
        self.assertIn("Maximum is six", guidance_text())
        self.assertEqual(payload.requirements[0].text, "Limit is 6")
        return {
            "requirement_analysis": [{"requirement_id": "R1", "requirement_text": "Limit is 6"}],
            "coverage_plan": [
                {
                    "requirement_id": "R1",
                    "requirement_text": "Limit is 6",
                    "scenarios": [{"id": "S1", "requirement_id": "R1", "title": "Limit", "objective": "Validate six"}],
                }
            ],
        }

    def generator(self, payload, **kwargs):
        self.assertIn("Maximum is six", guidance_text())
        return {
            "test_cases": [
                {
                    "id": "new",
                    "title": "Accept six",
                    "linked_requirement_ids": ["R1"],
                    "scenario_refs": ["S1"],
                    "steps": [{"step": 1, "action": "Enter 6 and submit", "expected": "Confirmation appears"}],
                }
            ],
            "approved": False,
            "generation_evidence": {"request_id": kwargs["request_id"]},
        }

    def test_refreshed_tests_do_not_keep_old_analysis_or_review_evidence(self):
        before = self.fixture()
        with (
            patch.object(sync, "enforce_billing_access"),
            patch.object(sync, "generate_use_cases", side_effect=self.planner),
            patch.object(sync, "generate_test_cases", side_effect=self.generator),
        ):
            result = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
        self.assertEqual(result.project_sync["status"], "ready_for_review", result.project_sync)
        payload = result.current_snapshots["test_cases"].payload
        self.assertEqual(payload["requirement_analysis"], result.current_snapshots["use_cases"].payload["requirement_analysis"])
        self.assertEqual({r["requirement_id"]: r["requirement_text"] for r in payload["requirement_analysis"]}, {"R1": "Limit is 6", "R2": "Show receipt"})
        self.assertEqual(payload["iteration_history"], [])
        self.assertEqual(payload["generation_evidence"]["request_id"], f"{result.project_sync['id']}:test_cases:1")
        self.assertEqual(next(c for c in payload["test_cases"] if c["id"] == "TC2"), self.preserved)
        old = projects.get_project_stage_snapshot(self.pid, before.current_snapshots["test_cases"].snapshot_id, actor=self.actor)
        self.assertEqual(old.payload["generation_evidence"]["request_id"], "baseline-generation")

    def test_accept_refresh_retry_preservation_and_mandatory_worker_inputs(self):
        before = self.fixture()
        original = deepcopy(self.store)
        with (
            patch.dict("os.environ", {"ADK_MEMORY_ENABLED": "false", "ADK_SKILLS_ENABLED": "false"}),
            patch.object(sync, "enforce_billing_access"),
            patch.object(sync, "generate_use_cases", side_effect=self.planner) as planner,
            patch.object(sync, "generate_test_cases", side_effect=self.generator),
        ):
            result = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
            self.assertEqual(result.project_sync["status"], "ready_for_review", result.project_sync)
            self.assertEqual(result.project_sync["review"]["preserved"], 1)
            preserved = next(c for c in result.current_snapshots["test_cases"].payload["test_cases"] if c["id"] == "TC2")
            for key in ("id", "title", "linked_requirement_ids"):
                self.assertEqual(preserved[key], self.preserved[key])
            self.assertEqual(preserved["steps"][0]["expected"], self.preserved["steps"][0]["expected"])
            self.assertFalse(result.stage_state["test_cases"].approved)
            self.assertFalse(result.stage_state["use_cases"].approved)
            self.assertEqual(result.current_snapshots["requirements"].payload["requirements"][0]["review_status"], "Needs Review")
            retry = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
            self.assertEqual(retry.current_revision, result.current_revision)
            self.assertEqual(planner.call_count, 1)
            from concurrent.futures import ThreadPoolExecutor

            with guidance_scope(prepare_run("automation", self.actor, self.pid, "automation")), ThreadPoolExecutor() as executor:
                self.assertIn("Maximum is six", submit_with_guidance(executor, guidance_text).result())
        path = f"qa_projects/{self.pid}/snapshots/{before.current_snapshots['test_cases'].snapshot_id}"
        self.assertEqual(self.store[path], original[path])

    def test_demo_accept_and_exact_replay_preserve_correction_without_dispatch(self):
        before = self.fixture()
        with (
            patch.object(sync, "draft_admission_client", return_value=self.transaction_client),
            patch.object(sync, "enforce_billing_access") as billing,
            patch.object(sync, "_generate_stage") as dispatch,
        ):
            accepted = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
            before_replay = deepcopy(self.store)
            self.assertEqual(sync.accept_finding(self.pid, self.actor, self.payload, "accept"), accepted)
            with self.assertRaises(HTTPException) as deferred:
                sync.resume_sync(self.pid, self.actor, accepted.project_sync["id"])
            billing.assert_not_called()
            dispatch.assert_not_called()

        self.assertEqual(deferred.exception.status_code, 409)
        self.assertEqual(deferred.exception.detail["code"], "project_draft_refresh_deferred")
        self.assertEqual(self.store, before_replay)
        self.assertEqual(accepted.current_revision, before.current_revision + 1)
        self.assertEqual(accepted.project_sync["status"], "pending")
        self.assertEqual(accepted.project_sync["attempts"], 0)
        self.assertEqual(accepted.current_snapshots["requirements"].payload["requirements"][0]["text"], "Limit is 6")
        self.assertEqual(accepted.current_snapshots["test_cases"], before.current_snapshots["test_cases"])
        self.assertTrue(accepted.stage_state["test_cases"].stale)

    def test_partial_resumes_only_failed_stage(self):
        self.fixture()
        with (
            patch.object(sync, "enforce_billing_access"),
            patch.object(sync, "generate_use_cases", side_effect=self.planner) as planner,
            patch.object(sync, "generate_test_cases", side_effect=RuntimeError("provider down")),
        ):
            partial = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
            self.assertEqual(partial.project_sync["status"], "partial")
            self.assertEqual(partial.project_sync["stages"]["use_cases"], "draft")
            old = partial.current_snapshots["test_cases"].snapshot_id
        with (
            patch.object(sync, "enforce_billing_access"),
            patch.object(sync, "generate_use_cases") as planner,
            patch.object(sync, "generate_test_cases", side_effect=self.generator),
        ):
            completed = sync.resume_sync(self.pid, self.actor, partial.project_sync["id"])
            self.assertEqual(completed.project_sync["status"], "ready_for_review", completed.project_sync)
            planner.assert_not_called()
            self.assertNotEqual(old, completed.current_snapshots["test_cases"].snapshot_id)

    def test_inflight_result_is_fenced_after_new_source(self):
        self.fixture()
        manifest = prepare_run("test_cases", self.actor, self.pid, "old")
        projects.append_stage_snapshot(
            project_id=self.pid, actor=self.actor, request_id="new", operation="edit", stage="context", payload={"notes": "New source"}
        )
        with guidance_scope(manifest), self.assertRaises(HTTPException) as error:
            projects.append_stage_snapshot(
                project_id=self.pid, actor=self.actor, request_id="old", operation="generate", stage="test_cases", payload={"test_cases": []}
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_conflicting_source_and_cross_owner_rejected(self):
        self.fixture()
        stale = self.payload.model_copy(deep=True)
        stale.corrections[0].previous_text = "Limit is 7"
        with self.assertRaises(HTTPException):
            sync.accept_finding(self.pid, self.actor, stale, "accept")
        other = self.actor.model_copy(update={"sub": "other"})
        with self.assertRaises(HTTPException) as error:
            sync.accept_finding(self.pid, other, self.payload, "accept")
        self.assertIn(error.exception.status_code, (403, 404))

    def test_runtime_cannot_publish_behaviour_or_escalate_to_maintenance(self):
        self.fixture()
        for action in ("approve", "revise", "promote", "select", "delete"):
            with self.assertRaises(HTTPException) as error:
                mutate_knowledge(self.actor, self.pid, KnowledgeMutation(action=action), "runtime")
            self.assertEqual(error.exception.status_code, 403)
        with patch.dict("os.environ", {"AGENT_MAINTENANCE_ENABLED": "true"}):
            with self.assertRaises(HTTPException), maintenance_scope(self.actor):
                pass
            maintainer = self.actor.model_copy(update={"roles": ["agent_maintainer"]})
            with guidance_scope(prepare_run("test_cases", self.actor, self.pid, "runtime")), self.assertRaises(HTTPException), maintenance_scope(maintainer):
                pass
            with maintenance_scope(maintainer):
                from app.services.behaviour_maintenance import check_knowledge_mutation

                check_knowledge_mutation(maintainer, self.pid, "approve")

    def test_concurrent_source_change_supersedes_refresh_without_publishing_old_tests(self):
        before = self.fixture()

        def change_source(payload, **kwargs):
            projects.append_stage_snapshot(
                project_id=self.pid, actor=self.actor, request_id="concurrent", operation="edit", stage="context", payload={"notes": "New source"}
            )
            return self.planner(payload, **kwargs)

        with patch.object(sync, "enforce_billing_access"), patch.object(sync, "generate_use_cases", side_effect=change_source), self.assertRaises(Exception):
            sync.accept_finding(self.pid, self.actor, self.payload, "accept")
        current = projects.get_project(self.pid, actor=self.actor)
        self.assertEqual(current.project_sync["status"], "superseded")
        self.assertEqual(current.current_snapshots["test_cases"].snapshot_id, before.current_snapshots["test_cases"].snapshot_id)

    def test_scripts_receive_same_facts_and_remain_unexecuted_drafts(self):
        project = self.fixture()
        projects.append_stage_snapshot(
            project_id=self.pid,
            actor=self.actor,
            request_id="script",
            operation="automation.playwright.generate",
            stage="reports",
            payload={"artifact_kind": "generated_code", "automation_input": {"test_cases": []}, "files": ["old.py"]},
        )
        self.payload.base_project_revision = projects.get_project(self.pid, actor=self.actor).current_revision
        from app.models import AutomationResponse

        def scripts(payload):
            self.assertIn("Maximum is six", guidance_text())
            return AutomationResponse(status="generated", files=["synthetic.py"])

        with (
            patch.object(sync, "enforce_billing_access"),
            patch.object(sync, "generate_use_cases", side_effect=self.planner),
            patch.object(sync, "generate_test_cases", side_effect=self.generator),
            patch.object(sync, "generate_playwright_pom", side_effect=scripts),
        ):
            result = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
        self.assertEqual(result.project_sync["status"], "ready_for_review", result.project_sync)
        self.assertEqual(result.current_snapshots["reports"].payload["execution_status"], "not_executed")
        self.assertFalse(result.stage_state["reports"].approved)
        self.assertEqual(result.execution_runs, [])

    def test_runtime_api_cannot_publish_even_with_a_forged_role_header(self):
        from fastapi.testclient import TestClient
        from app.main import app, get_current_user

        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        with patch.dict("os.environ", {"AGENT_MAINTENANCE_ENABLED": "true"}), TestClient(app) as client:
            for path in ("/projects/p/knowledge", "/maintenance/projects/p/knowledge"):
                response = client.post(path, json={"action": "approve", "entry_id": "lesson"}, headers={"X-Request-ID": "forged", "X-Role": "agent_maintainer"})
                self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
