from copy import deepcopy
import os
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_use_case_review_service import PROJECT_ID, PROJECT_REVISION, FakeCollection, FakeDocument, FakeFirestoreClient, _run_transaction, _seed_store

from app.main import app, get_current_user
from app.models import AuthUser
from app.services import approval_continuation as continuation
from app.services.approval_scenario_worker import DEMO_PROJECT_ID
from app.services import requirement_import_service as imports
from app.services.requirement_reconciliation import canonical_requirements


PROJECT_ROOT = f"qa_projects/{PROJECT_ID}"
STATUS_PATH = f"/projects/{PROJECT_ID}/approval-continuations"
APPROVAL_PATH = f"/projects/{PROJECT_ID}/requirements/approve-and-prepare-scenarios"
EMULATOR_HOST = "127.0.0.1:9090"


class _RunCollection(FakeCollection):
    def order_by(self, field, *, direction):
        assert field == "continuation.approval_project_revision"
        assert direction == "DESCENDING"
        return self

    def limit(self, size):
        self.size = size
        return self

    def stream(self):
        rows = [snapshot for snapshot in super().stream() if "approval_project_revision" in snapshot.to_dict().get("continuation", {})]
        return iter(sorted(rows, key=lambda snapshot: snapshot.to_dict()["continuation"]["approval_project_revision"], reverse=True)[: self.size])


class _ProjectDocument(FakeDocument):
    def collection(self, name):
        if name == "orchestrator_runs":
            return _RunCollection(f"{self.path}/{name}", self.store)
        return super().collection(name)


class _ProjectCollection(FakeCollection):
    def document(self, document_id):
        return _ProjectDocument(f"{self.path}/{document_id}", self.store)


class _FirestoreClient(FakeFirestoreClient):
    project = DEMO_PROJECT_ID
    _emulator_host = EMULATOR_HOST

    def collection(self, name):
        if name == "qa_projects":
            return _ProjectCollection(name, self.store)
        return super().collection(name)


class ApprovalContinuationApiTests(TestCase):
    def setUp(self):
        self.actor = AuthUser(sub="reviewer-1", name="QA owner")
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.pop, get_current_user)
        self.store = _seed_store()
        self.firestore = _FirestoreClient(self.store)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "TCG_APPROVAL_SCENARIOS_ENABLED": "true",
                    "FIREBASE_PROJECT_ID": DEMO_PROJECT_ID,
                    "FIRESTORE_EMULATOR_HOST": EMULATOR_HOST,
                    "ADK_SKILLS_ENABLED": "false",
                    "ADK_MEMORY_ENABLED": "false",
                    "GOOGLE_GENAI_USE_VERTEXAI": "false",
                },
            )
        )
        self.enterContext(patch.object(imports, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(patch("app.services.workflow_project_service.get_required_firestore_client", return_value=self.firestore))
        self.enterContext(
            patch("app.services.workflow_project_service.get_required_firestore_collection", return_value=self.firestore.collection("qa_projects"))
        )
        self.enterContext(patch.object(imports, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(continuation, "transactional", side_effect=_run_transaction))
        self.client = self.enterContext(TestClient(app))
        baseline = self.store[f"{PROJECT_ROOT}/snapshots/snapshot-requirements-v1"]
        rows, _ = canonical_requirements(PROJECT_ID, baseline)
        self.payload = {
            "base_project_revision": PROJECT_REVISION,
            "idempotency_key": "approve-scope-request",
            "reviews": [{"requirement_uid": rows[0].requirement_uid, "review_status": "Approved", "quality_flags": []}],
        }

    def approve(self, *, headers=None):
        return self.client.post(APPROVAL_PATH, json=self.payload, headers=headers)

    def status(self, request_id=None):
        return self.client.get(STATUS_PATH, params={"request_id": request_id} if request_id is not None else None)

    def cancel(self, run_id):
        return self.client.post(f"{STATUS_PATH}/{run_id}/cancel")

    def test_approval_and_exact_retry_return_the_same_public_run_and_receipt(self):
        initial = deepcopy(self.store)
        self.assertEqual(self.status().json(), {"enabled": True, "runs": [], "receipt": None})
        self.assertEqual(self.store, initial)
        self.assertEqual(self.firestore.transactions, [])

        response = self.approve(headers={"X-Request-ID": self.payload["idempotency_key"]})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        run = result["continuation"]
        self.assertEqual(result["project"]["current_revision"], PROJECT_REVISION + 1)
        self.assertEqual(run["project_revision"], PROJECT_REVISION + 1)
        self.assertEqual(
            run,
            {
                "run_id": run["run_id"],
                "state": "queued",
                "stage": "prepare_scenarios",
                "project_revision": PROJECT_REVISION + 1,
                "request_id": self.payload["idempotency_key"],
                "reason": None,
                "result_snapshot_id": None,
            },
        )
        self.assertIsNone(result["blocked_reason"])
        self.assertIn(f"{continuation.OUTBOX_COLLECTION}/{run['run_id']}", self.store)

        committed = deepcopy(self.store)
        response = self.approve()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["continuation"], run)
        self.assertEqual(self.firestore.transactions[-1].operations, [])
        receipt_status = self.status(self.payload["idempotency_key"]).json()
        self.assertEqual(
            receipt_status,
            {
                "enabled": True,
                "runs": [run],
                "receipt": {"request_id": self.payload["idempotency_key"], "run_id": run["run_id"], "blocked_reason": None},
            },
        )
        self.assertIsNone(self.status("different-request").json()["receipt"])
        self.payload["reviews"][0]["quality_flags"] = ["Changed on retry"]
        self.assertEqual(self.approve().status_code, 409)
        self.assertEqual(self.store, committed)

    def test_blocked_approval_has_a_recoverable_receipt_without_a_run(self):
        self.store[PROJECT_ROOT]["stage_state"]["context"] = {"current_snapshot_id": "stale-context", "stale": True}
        response = self.approve()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["project"]["stage_state"]["requirements"]["approved"])
        self.assertEqual(response.json()["blocked_reason"], "context_not_current")
        self.assertIsNone(response.json()["continuation"])
        self.assertEqual(
            self.status(self.payload["idempotency_key"]).json(),
            {
                "enabled": True,
                "runs": [],
                "receipt": {"request_id": self.payload["idempotency_key"], "run_id": None, "blocked_reason": "context_not_current"},
            },
        )
        self.assertFalse(any(path.startswith(f"{continuation.OUTBOX_COLLECTION}/") for path in self.store))

    def test_legacy_approval_does_not_queue_or_create_a_continuation_receipt(self):
        response = self.client.patch(f"/projects/{PROJECT_ID}/requirements/reviews", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.status(self.payload["idempotency_key"]).json(), {"enabled": True, "runs": [], "receipt": None})

    def test_disabled_operations_and_outsiders_cannot_change_approval_state(self):
        initial = deepcopy(self.store)
        with patch.dict(os.environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
            self.assertEqual(self.status().json(), {"enabled": False, "runs": [], "receipt": None})
            self.assertEqual(self.approve().status_code, 409)
            self.assertEqual(self.cancel(f"continuation_{'a' * 64}").status_code, 409)
            self.assertEqual(self.store, initial)
            self.assertEqual(self.firestore.transactions, [])

        with patch.object(self.firestore, "_emulator_host", None):
            self.assertFalse(self.status().json()["enabled"])
            self.assertEqual(self.approve().status_code, 409)
            self.assertEqual(self.cancel(f"continuation_{'a' * 64}").status_code, 409)
            self.assertEqual(self.store, initial)
            self.assertEqual(self.firestore.transactions, [])

        self.actor = AuthUser(sub="other-account", name="Other")
        self.assertEqual(self.status(self.payload["idempotency_key"]).status_code, 404)
        self.assertEqual(self.approve().status_code, 404)
        self.assertEqual(self.cancel(f"continuation_{'a' * 64}").status_code, 404)
        self.assertEqual(self.store, initial)

    def test_payload_allows_approval_only_and_rejects_unmatched_request_identity(self):
        before = deepcopy(self.store)
        self.assertEqual(self.approve(headers={"X-Request-ID": "different-request"}).status_code, 422)
        self.payload["reviews"][0]["review_status"] = "Rejected"
        self.assertEqual(self.approve().status_code, 422)
        self.payload["reviews"][0]["review_status"] = "Approved"
        self.payload["settings_fingerprint"] = "a" * 64
        self.assertEqual(self.approve().status_code, 422)
        self.assertEqual(self.store, before)
        self.assertEqual(self.firestore.transactions, [])

    def test_cancellation_returns_the_authoritative_run_and_replays_without_writes(self):
        approval = self.approve()
        self.assertEqual(approval.status_code, 200, approval.text)
        run_id = approval.json()["continuation"]["run_id"]
        response = self.cancel(run_id)
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertEqual(run["state"], "cancelled")
        self.assertEqual(run["reason"], "cancelled_by_owner")
        self.assertNotIn(f"{continuation.OUTBOX_COLLECTION}/{run_id}", self.store)
        self.assertEqual(self.status().json()["runs"], [run])
        committed = deepcopy(self.store)
        self.assertEqual(self.cancel(run_id).json(), run)
        self.assertEqual(self.store, committed)
        self.assertEqual(self.firestore.transactions[-1].operations, [])

    def test_other_continuation_stages_are_hidden_and_cannot_be_cancelled(self):
        approval = self.approve()
        self.assertEqual(approval.status_code, 200, approval.text)
        run_id = approval.json()["continuation"]["run_id"]
        self.store[f"{PROJECT_ROOT}/orchestrator_runs/{run_id}"]["continuation"]["stage"] = "generate_tests"
        before = deepcopy(self.store)
        self.assertEqual(self.status().json()["runs"], [])
        self.assertEqual(self.cancel(run_id).status_code, 404)
        self.assertEqual(self.store, before)

    def test_status_caps_history_and_includes_exact_receipt_target_outside_recent_window(self):
        approval = self.approve()
        self.assertEqual(approval.status_code, 200, approval.text)
        original_id = approval.json()["continuation"]["run_id"]
        original = self.store[f"{PROJECT_ROOT}/orchestrator_runs/{original_id}"]
        self.store[f"{PROJECT_ROOT}/orchestrator_runs/legacy"] = {"run_id": "legacy", "project_revision": 100}
        for revision in range(10, 40):
            run_id = f"continuation_{revision:064x}"
            self.store[f"{PROJECT_ROOT}/orchestrator_runs/{run_id}"] = {
                **original,
                "run_id": run_id,
                "project_revision": revision + 1,
                "request_id": f"request-{revision}",
                "produced_snapshot_ids": {"use_cases": f"scenario-{revision}"},
                "continuation": {
                    **original["continuation"],
                    "approval_project_revision": revision,
                    "state": "awaiting_user",
                },
            }

        rows = self.status().json()["runs"]
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]["result_snapshot_id"], "scenario-39")
        self.assertNotIn(original_id, [run["run_id"] for run in rows])
        rows = self.status(self.payload["idempotency_key"]).json()["runs"]
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[-1]["run_id"], original_id)
