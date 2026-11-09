from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from app.main import _build_grounded_context_from_enrich_input, app, get_current_user
from app.models import AuthUser, EnrichInput, EnrichResponse, Requirement
from app.routers import requirements as routes
from app.services import workflow_project_service as projects
from test_use_case_review_service import FakeFirestoreClient, _run_transaction


class EnrichContractTests(unittest.TestCase):
    def test_build_grounded_context_from_enrich_input_registers_artifacts(self) -> None:
        payload = EnrichInput(
            requirements=[Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")],
            app_link="https://example.test/app",
            prototype_link="https://example.test/prototype",
            diagram_links=["https://example.test/diagram-1"],
            image_links=["https://example.test/image-1", "https://example.test/image-2"],
            notes="Context for local testing",
        )

        grounded_context = _build_grounded_context_from_enrich_input(payload)

        self.assertEqual(len(grounded_context.artifact_sources), 6)
        self.assertTrue(grounded_context.summary)
        self.assertEqual(grounded_context.artifact_sources[0].source_type, "app")
        self.assertEqual(grounded_context.artifact_sources[-1].source_type, "note")

    def test_enrich_response_preserves_input_shape_and_adds_grounded_context(self) -> None:
        payload = EnrichInput(
            requirements=[Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")],
            notes="Context notes",
        )

        response = EnrichResponse(
            **payload.model_dump(exclude={"grounded_context"}),
            grounded_context=_build_grounded_context_from_enrich_input(payload),
        )

        self.assertEqual(len(response.requirements), 1)
        self.assertIsNotNone(response.grounded_context)
        self.assertEqual(response.grounded_context.artifact_sources[0].source_type, "note")


class ContextPublicationTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.firestore = FakeFirestoreClient(self.store)
        self.actor = AuthUser(sub="context-owner", name="Context owner")
        self.enterContext(patch.dict(app.dependency_overrides, {get_current_user: lambda: self.actor}))
        self.enterContext(patch.object(projects, "get_required_firestore_collection", return_value=self.firestore.collection("qa_projects")))
        self.enterContext(patch.object(projects, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(patch.object(projects, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(routes, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(
            patch("app.services.audit_repository.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.firestore.collection(name))
        )
        self.grounder = self.enterContext(patch.object(routes, "_build_grounded_context_from_enrich_input", wraps=_build_grounded_context_from_enrich_input))
        project = projects.create_project(name="Context checks", description=None, actor=self.actor, request_id="seed")
        self.project_id = project.project_id
        self.requirements = [{"id": "REQ-1", "text": "Reserve an available room.", "review_status": "Approved"}]
        self.save("requirements", {"requirements": self.requirements})
        self.save("context", {"notes": "Old notes", "grounded_context": {"summary": "Old grounding"}})
        self.save("use_cases", {"coverage_plan": []})
        self.save("test_cases", {"test_cases": []})
        self.before = projects.get_project(self.project_id, actor=self.actor)
        self.body = {
            "project_id": self.project_id,
            "base_project_revision": self.before.current_revision,
            "requirements": self.requirements,
            "notes": "Updated Context",
            "execution_prerequisites": [
                {
                    "name": "employee",
                    "kind": "test_data",
                    "description": "Named employee",
                    "reference": "fixture:employee",
                    "requirement_ids": [],
                    "status": "configured",
                }
            ],
        }
        self.http = self.enterContext(TestClient(app))

    def save(self, stage, payload):
        return projects.append_stage_snapshot(
            project_id=self.project_id, stage=stage, payload=payload, operation="seed", actor=self.actor, request_id=f"seed-{stage}", approved=True
        )

    def post(self, request_id="context", **changes):
        return self.http.post("/requirements/enrich", headers={"X-Request-ID": request_id}, json={**self.body, **changes})

    def audit(self, collection):
        return [value for path, value in self.store.items() if path.startswith(f"{collection}/")]

    def test_context_and_audit_commit_once_and_exact_retry_returns_saved_grounding(self):
        publish = routes.append_stage_snapshot

        def lose_commit_reply(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic commit reply lost")

        with patch.object(routes, "append_stage_snapshot", side_effect=lose_commit_reply):
            first = self.post()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.post().json(), first.json())
        self.assertIsNone(first.json()["diagram_links"])
        self.assertEqual(first.json()["execution_prerequisites"], self.body["execution_prerequisites"])
        after = projects.get_project(self.project_id, actor=self.actor)
        self.assertEqual(after.current_revision, self.before.current_revision + 1)
        snapshot = after.current_snapshots["context"]
        self.assertEqual(snapshot.payload["execution_prerequisites"], self.body["execution_prerequisites"])
        for stage in ("requirements", "use_cases", "test_cases"):
            self.assertEqual(after.current_snapshots[stage], self.before.current_snapshots[stage])
            self.assertEqual(after.stage_state[stage].stale, stage != "requirements")
        [event] = self.audit("usage_events")
        [workflow] = self.audit("workflow_runs")
        self.assertEqual((event["status"], event["quantity"], workflow["status"]), ("completed", 1, "completed"))
        self.assertEqual(event["event_id"], snapshot.source_event_id)
        self.assertEqual({event["workflow_run_id"], workflow["run_id"]}, {snapshot.workflow_run_id})
        self.assertEqual(self.post(notes="Different input").status_code, 409)
        self.save("context", {"notes": "Newer Context", "grounded_context": {}})
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(len(self.audit("usage_events")), 1)
        self.grounder.assert_called_once()

    def test_preflight_rejects_foreign_archived_and_stale_requests_before_grounding(self):
        owner = self.actor
        self.actor = AuthUser(sub="another-user", name="Another user")
        self.assertEqual(self.post().status_code, 403)
        self.actor = owner
        self.assertEqual(self.post(base_project_revision=self.before.current_revision - 1).status_code, 409)
        projects.update_project(project_id=self.project_id, actor=owner, request_id="archive", status_value="archived")
        self.assertEqual(self.post(base_project_revision=None).status_code, 409)
        self.assertFalse(self.audit("usage_events"))
        self.assertFalse(self.audit("workflow_runs"))
        self.grounder.assert_not_called()

    def test_publication_preserves_baseline_when_explicit_or_legacy_revision_changes_during_grounding(self):
        def advance_project(payload):
            projects.update_project(project_id=self.project_id, actor=self.actor, request_id="edit", name="Project updated during grounding")
            return _build_grounded_context_from_enrich_input(payload)

        self.grounder.side_effect = advance_project
        for revision in (self.before.current_revision, None):
            with self.subTest(revision=revision):
                self.assertEqual(self.post(request_id=f"context-{revision}", base_project_revision=revision).status_code, 409)
        after = projects.get_project(self.project_id, actor=self.actor)
        self.assertEqual(after.current_snapshots, self.before.current_snapshots)
        self.assertEqual(after.stage_state, self.before.stage_state)
        self.assertEqual(after.current_revision, self.before.current_revision + 2)
        self.assertEqual([(event["status"], event["quantity"]) for event in self.audit("usage_events")], [("failed", 0), ("failed", 0)])

    def test_atomic_failure_leaves_previous_context_and_audit_has_no_completed_usage(self):
        self.firestore.fail_on_create_segment = "/snapshots/"
        failure = self.post()
        self.assertEqual(failure.status_code, 503, failure.text)
        transaction = self.firestore.transactions[-1]
        self.assertEqual([path.split("/")[0] for action, path in transaction.operations if action == "set"], ["usage_events", "workflow_runs"])
        self.assertEqual(projects.get_project(self.project_id, actor=self.actor), self.before)
        [event] = self.audit("usage_events")
        [workflow] = self.audit("workflow_runs")
        self.assertEqual((event["status"], event["quantity"], workflow["status"]), ("failed", 0, "failed"))

    def test_standalone_and_project_enrichment_preserve_explicit_grounding(self):
        for project_id in (None, self.project_id):
            with self.subTest(project_id=project_id):
                result = self.post(project_id=project_id, grounded_context={"summary": "Provided evidence"})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["grounded_context"]["summary"], "Provided evidence")
        self.grounder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
