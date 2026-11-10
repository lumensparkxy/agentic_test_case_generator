from copy import deepcopy
import os
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_use_case_review_service import (
    NOW,
    PROJECT_ID,
    PROJECT_REVISION,
    SNAPSHOT_ID,
    FakeCollection,
    FakeDocument,
    FakeFirestoreClient,
    _run_transaction,
    _seed_store,
)

from app.main import app, get_current_user
from app.models import AuthUser
from app.routers import approval_continuations as routes
from app.services import approval_continuation as continuation
from app.services.approval_scenario_worker import DEMO_PROJECT_ID
from app.services import project_draft_admission as admission
from app.services import requirement_import_service as imports
from app.services import use_case_review_service as scenario_review
from app.services.requirement_reconciliation import canonical_requirements


PROJECT_ROOT = f"qa_projects/{PROJECT_ID}"
STATUS_PATH = f"/projects/{PROJECT_ID}/approval-continuations"
APPROVAL_PATH = f"/projects/{PROJECT_ID}/requirements/approve-and-prepare-scenarios"
TEST_APPROVAL_PATH = f"/projects/{PROJECT_ID}/use-cases/approve-and-generate-tests"
SAVED_PLAN_GENERATION_PATH = f"{STATUS_PATH}/generate-tests-from-saved-approval"
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
                    "PARALLEL_TEST_CASE_MAX_WORKERS": "3",
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
        self.enterContext(patch.object(admission, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(scenario_review, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(patch.object(scenario_review, "transactional", side_effect=_run_transaction))
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

    def status(self, request_id=None, *, stage=None):
        params = {}
        if request_id is not None:
            params["request_id"] = request_id
        if stage is not None:
            params["stage"] = stage
        return self.client.get(STATUS_PATH, params=params)

    def approve_tests(self, payload, request_id):
        return self.client.post(TEST_APPROVAL_PATH, json=payload, headers={"X-Request-ID": request_id})

    def prepared_plan(self):
        first = self.approve()
        self.assertEqual(first.status_code, 200, first.text)
        parent_id = first.json()["continuation"]["run_id"]
        parent = self.store[f"{PROJECT_ROOT}/orchestrator_runs/{parent_id}"]
        requirement_id = parent["continuation"]["source_snapshot_ids"]["requirements"]
        revision = parent["project_revision"] + 1
        self.store[PROJECT_ROOT]["current_revision"] = revision
        self.store[PROJECT_ROOT]["stage_state"]["use_cases"].update(stale=False, approved=False)
        self.store[f"{PROJECT_ROOT}/snapshots/{SNAPSHOT_ID}"].update(
            operation="use_cases.generate",
            project_revision=revision,
            actor_user_id=self.actor.sub,
            request_id=parent_id,
            source_snapshot_id=requirement_id,
            metadata={"source_requirements_snapshot_id": requirement_id, "source_context_omitted": True},
        )
        self.store[f"{PROJECT_ROOT}/snapshots/{SNAPSHOT_ID}"]["payload"]["coverage_plan"][0]["requirement_text"] = "Checkout"
        parent.update(project_revision=revision, produced_snapshot_ids={"use_cases": SNAPSHOT_ID})
        parent["continuation"].update(
            state="awaiting_user",
            attempts=1,
            provider_requests_reserved=parent["continuation"]["options"]["max_provider_requests"],
            dispatch={"fence": 1, "at": NOW},
        )
        del self.store[f"{continuation.OUTBOX_COLLECTION}/{parent_id}"]
        return parent, {"snapshot_id": SNAPSHOT_ID, "base_project_revision": revision, "decision": "approve"}

    def ordinary_approved_plan(self):
        scope = self.client.patch(f"/projects/{PROJECT_ID}/requirements/reviews", json=self.payload)
        self.assertEqual(scope.status_code, 200, scope.text)
        project = self.store[PROJECT_ROOT]
        requirements_id = project["stage_state"]["requirements"]["current_snapshot_id"]
        project["stage_state"]["use_cases"].update(stale=False, approved=False)
        plan = self.store[f"{PROJECT_ROOT}/snapshots/{SNAPSHOT_ID}"]
        plan.update(
            operation="use_cases.generate",
            project_revision=project["current_revision"],
            actor_user_id=self.actor.sub,
            request_id="continuation_manual",
            source_snapshot_id=requirements_id,
            metadata={"source_requirements_snapshot_id": requirements_id, "source_context_omitted": True},
        )
        plan["payload"]["coverage_plan"][0]["requirement_text"] = "Checkout"
        for scenario in plan["payload"]["coverage_plan"][0]["scenarios"]:
            scenario.update(requirement_id="REQ-1", objective=scenario["title"])
        plan["payload"]["requirement_analysis"] = [{"requirement_id": "REQ-1", "requirement_text": "Checkout"}]
        approval = self.client.post(
            f"/projects/{PROJECT_ID}/use-cases/reviews",
            json={"snapshot_id": SNAPSHOT_ID, "base_project_revision": project["current_revision"], "decision": "approve"},
            headers={"X-Request-ID": "ordinary-plan-approval"},
        )
        self.assertEqual(approval.status_code, 200, approval.text)
        return {
            "snapshot_id": SNAPSHOT_ID,
            "base_project_revision": approval.json()["project_revision"],
            "review_id": approval.json()["review"]["review_id"],
        }

    def enroll_saved_plan(self, payload, request_id="generate-from-saved-plan"):
        return self.client.post(SAVED_PLAN_GENERATION_PATH, json=payload, headers={"X-Request-ID": request_id})

    def cancel(self, run_id):
        return self.client.post(f"{STATUS_PATH}/{run_id}/cancel")

    def test_approval_and_exact_retry_return_the_same_public_run_and_receipt(self):
        initial = deepcopy(self.store)
        self.assertEqual(
            self.status().json(),
            {"enabled": True, "tests_enabled": True, "saved_review_enabled": True, "saved_review_attempt": None, "runs": [], "receipt": None},
        )
        self.assertEqual(self.store, initial)
        self.assertFalse(any(transaction.operations for transaction in self.firestore.transactions))

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
                "source_snapshot_id": result["project"]["stage_state"]["requirements"]["current_snapshot_id"],
                "result_snapshot_id": None,
                "retry_available": False,
                "provider_usage_unconfirmed": False,
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
                "tests_enabled": True,
                "saved_review_enabled": True,
                "saved_review_attempt": None,
                "runs": [run],
                "receipt": {"request_id": self.payload["idempotency_key"], "run_id": run["run_id"], "blocked_reason": None},
            },
        )
        self.assertIsNone(self.status("different-request").json()["receipt"])
        self.payload["reviews"][0]["quality_flags"] = ["Changed on retry"]
        self.assertEqual(self.approve().status_code, 409)
        self.assertEqual(self.store, committed)

    def test_retry_restores_the_exact_approved_plan_stage_without_another_approval(self):
        _, payload = self.prepared_plan()
        payload["template"] = {"name": "Specific template", "format": "table", "fields": ["id", "title"]}
        response = self.approve_tests(payload, "approve-child")
        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["continuation"]["run_id"]
        path = f"{STATUS_PATH}/{run_id}/retry"
        claim = continuation.claim_stage(self.firestore, project_id=PROJECT_ID, run_id=run_id)
        continuation.stop_stage(self.firestore, claim, reason="test_case_generation_failed")
        failed = self.status("approve-child", stage="generate_tests").json()
        self.assertTrue(failed["runs"][0]["retry_available"])
        before = deepcopy(self.store)
        owner = self.actor
        self.actor = AuthUser(sub="outsider", name="Other")
        self.assertEqual(self.client.post(path).status_code, 404)
        self.actor = owner
        self.assertEqual(self.store, before)

        result = self.client.post(path)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["run_id"], run_id)
        self.assertEqual(result.json()["state"], "queued")
        self.assertFalse(result.json()["retry_available"])
        after = deepcopy(self.store)
        changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        run_path = f"{PROJECT_ROOT}/orchestrator_runs/{run_id}"
        self.assertEqual(changed, {run_path, f"{continuation.OUTBOX_COLLECTION}/{run_id}"})
        self.assertEqual(after[run_path]["continuation"]["options"], before[run_path]["continuation"]["options"])
        self.assertEqual(self.status("approve-child", stage="generate_tests").json()["receipt"], failed["receipt"])
        self.assertEqual(self.client.post(path).json(), result.json())
        self.assertEqual(self.store, after)

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
                "tests_enabled": True,
                "saved_review_enabled": True,
                "saved_review_attempt": None,
                "runs": [],
                "receipt": {"request_id": self.payload["idempotency_key"], "run_id": None, "blocked_reason": "context_not_current"},
            },
        )
        self.assertFalse(any(path.startswith(f"{continuation.OUTBOX_COLLECTION}/") for path in self.store))

    def test_legacy_approval_does_not_queue_or_create_a_continuation_receipt(self):
        response = self.client.patch(f"/projects/{PROJECT_ID}/requirements/reviews", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.status(self.payload["idempotency_key"]).json(),
            {"enabled": True, "tests_enabled": True, "saved_review_enabled": True, "saved_review_attempt": None, "runs": [], "receipt": None},
        )

    def test_disabled_operations_and_outsiders_cannot_change_approval_state(self):
        initial = deepcopy(self.store)
        with patch.dict(os.environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
            self.assertEqual(
                self.status().json(),
                {"enabled": False, "tests_enabled": False, "saved_review_enabled": False, "saved_review_attempt": None, "runs": [], "receipt": None},
            )
            self.assertEqual(self.approve().status_code, 409)
            self.assertEqual(self.cancel(f"continuation_{'a' * 64}").status_code, 409)
            self.assertEqual(self.store, initial)
            self.assertEqual(self.firestore.transactions, [])

        with patch.object(self.firestore, "_emulator_host", None):
            status = self.status().json()
            self.assertFalse(status["enabled"])
            self.assertFalse(status["saved_review_enabled"])
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
        self.assertFalse(run["provider_usage_unconfirmed"])
        self.assertNotIn(f"{continuation.OUTBOX_COLLECTION}/{run_id}", self.store)
        self.assertEqual(self.status().json()["runs"], [run])
        committed = deepcopy(self.store)
        self.assertEqual(self.cancel(run_id).json(), run)
        self.assertEqual(self.store, committed)
        self.assertEqual(self.firestore.transactions[-1].operations, [])

    def test_release_preserves_unconfirmed_provider_usage_on_the_original_receipt(self):
        approval = self.approve().json()["continuation"]
        run_id = approval["run_id"]
        request_id = approval["request_id"]
        claim = continuation.claim_stage(self.firestore, project_id=PROJECT_ID, run_id=run_id)
        continuation.mark_dispatched(self.firestore, claim, provider_requests=7)
        continuation.stop_stage(self.firestore, claim, reason="scenario_preparation_failed")
        interrupted = self.status(request_id).json()
        self.assertEqual(interrupted["runs"][0]["state"], "interrupted")
        self.assertTrue(interrupted["runs"][0]["provider_usage_unconfirmed"])
        run_path = f"{PROJECT_ROOT}/orchestrator_runs/{run_id}"
        before = deepcopy(self.store)

        response = self.cancel(run_id)
        self.assertEqual(response.status_code, 200, response.text)
        released = response.json()
        self.assertEqual(released["state"], "cancelled")
        self.assertEqual(released["reason"], "cancelled_by_owner")
        self.assertTrue(released["provider_usage_unconfirmed"])
        self.assertFalse(released["retry_available"])
        self.assertIsNone(released["result_snapshot_id"])
        status = self.status(request_id).json()
        self.assertEqual(status["receipt"], interrupted["receipt"])
        self.assertEqual(status["runs"], [released])
        self.assertEqual(self.store[PROJECT_ROOT], before[PROJECT_ROOT])
        self.assertEqual(self.store[run_path]["continuation"]["dispatch"], before[run_path]["continuation"]["dispatch"])
        self.assertEqual(self.store[run_path]["continuation"]["provider_requests_reserved"], 7)
        self.assertNotIn(f"{continuation.OUTBOX_COLLECTION}/{run_id}", self.store)
        with self.assertRaises(continuation.ContinuationConflict):
            continuation.renew_claim(self.firestore, claim)
        committed = deepcopy(self.store)
        self.assertEqual(self.cancel(run_id).json(), released)
        self.assertEqual(self.client.post(f"{STATUS_PATH}/{run_id}/retry").status_code, 409)
        self.assertEqual(self.store, committed)

    def test_other_continuation_stages_are_hidden_and_cannot_be_cancelled(self):
        approval = self.approve()
        self.assertEqual(approval.status_code, 200, approval.text)
        run_id = approval.json()["continuation"]["run_id"]
        self.store[f"{PROJECT_ROOT}/orchestrator_runs/{run_id}"]["continuation"]["stage"] = "execute_browser"
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

    def test_second_approval_returns_the_exact_child_and_disambiguates_same_request_identity(self):
        parent, payload = self.prepared_plan()
        request_id = self.payload["idempotency_key"]
        response = self.approve_tests(payload, request_id)
        self.assertEqual(response.status_code, 200, response.text)
        outcome = response.json()
        child = outcome["continuation"]
        self.assertEqual(
            child,
            {
                "run_id": child["run_id"],
                "state": "queued",
                "stage": "generate_tests",
                "project_revision": payload["base_project_revision"] + 1,
                "request_id": request_id,
                "reason": None,
                "source_snapshot_id": SNAPSHOT_ID,
                "result_snapshot_id": None,
                "retry_available": False,
                "provider_usage_unconfirmed": False,
            },
        )
        self.assertEqual(outcome["review"]["decision"], "approve")
        self.assertTrue(outcome["use_cases_state"]["approved"])
        self.assertNotIn("require_prepared_journey", outcome["review"])
        self.assertNotIn("continuation_run_id", outcome["review"])
        self.assertIsNone(outcome["blocked_reason"])
        self.assertIn(f"{continuation.OUTBOX_COLLECTION}/{child['run_id']}", self.store)
        committed_parent = self.store[f"{PROJECT_ROOT}/orchestrator_runs/{parent['run_id']}"]
        budget = committed_parent["continuation"]["journey_budget"]
        self.assertEqual(budget["child_run_id"], child["run_id"])
        self.assertEqual(budget["scenario_requests_reserved"] + budget["test_requests_allocated"], budget["provider_request_limit"])

        status = self.status(request_id, stage="generate_tests").json()
        self.assertEqual([row["stage"] for row in status["runs"]], ["generate_tests", "prepare_scenarios"])
        self.assertEqual(status["receipt"], {"request_id": request_id, "run_id": child["run_id"], "blocked_reason": None})
        self.assertEqual(status["runs"][1]["result_snapshot_id"], SNAPSHOT_ID)
        self.assertEqual(self.status(request_id).json()["receipt"]["run_id"], parent["run_id"])
        before = deepcopy(self.store)
        with patch.object(routes, "test_case_continuation_options", return_value=continuation.ContinuationOptions(settings_fingerprint="f" * 64)):
            replay = self.approve_tests(payload, request_id)
        self.assertEqual(replay.status_code, 200, replay.text)
        for key in ("review", "project_revision", "use_cases_state", "continuation", "blocked_reason"):
            self.assertEqual(replay.json()[key], outcome[key])
        self.assertEqual(self.approve_tests({**payload, "comment": "Different retry"}, request_id).status_code, 409)
        self.assertEqual(self.store, before)

    def test_scenario_approval_binds_an_explicit_template_to_the_receipt(self):
        _, payload = self.prepared_plan()
        template = {"name": "Expense – audit", "format": "table", "fields": ["id", "expected_result", "steps"]}
        request_id = "scenario-approval-template"
        selected = {**payload, "template": template}
        initial = deepcopy(self.store)
        oversized = {**payload, "template": {**template, "name": "監" * 1600}}
        response = self.approve_tests(oversized, request_id)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.store, initial)
        response = self.approve_tests(selected, request_id)
        self.assertEqual(response.status_code, 200, response.text)
        child = response.json()["continuation"]
        intent = self.store[f"{PROJECT_ROOT}/orchestrator_runs/{child['run_id']}"]["continuation"]
        self.assertEqual(intent["options"]["template"], template)
        self.assertNotIn("template", child)
        before = deepcopy(self.store)
        replay = self.approve_tests(selected, request_id)
        self.assertEqual(replay.status_code, 200, replay.text)
        for key in ("review", "project_revision", "use_cases_state", "continuation", "blocked_reason"):
            self.assertEqual(replay.json()[key], response.json()[key])
        for changed in (payload, {**payload, "template": {**template, "fields": list(reversed(template["fields"]))}}):
            with self.subTest(changed=changed):
                self.assertEqual(self.approve_tests(changed, request_id).status_code, 409)
        self.assertEqual(self.store, before)

    def test_blocked_test_generation_recovers_the_saved_scenario_approval(self):
        _, payload = self.prepared_plan()
        self.store[f"{PROJECT_ROOT}/snapshots/{SNAPSHOT_ID}"]["request_id"] = "not-a-prepared-result"
        request_id = "scenario-approval-blocked"
        response = self.approve_tests(payload, request_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["use_cases_state"]["approved"])
        self.assertIsNone(response.json()["continuation"])
        self.assertEqual(response.json()["blocked_reason"], "journey_budget_missing")
        self.assertEqual(
            self.status(request_id, stage="generate_tests").json()["receipt"],
            {"request_id": request_id, "run_id": None, "blocked_reason": "journey_budget_missing"},
        )
        self.assertIsNone(self.status(request_id).json()["receipt"])

    def test_spent_journey_does_not_schedule_a_second_test_generation(self):
        _, payload = self.prepared_plan()
        response = self.approve_tests(payload, "first-scenario-approval")
        self.assertEqual(response.status_code, 200, response.text)
        child = response.json()["continuation"]
        next_payload = {**payload, "base_project_revision": response.json()["project_revision"]}
        response = self.approve_tests(next_payload, "another-scenario-approval")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["use_cases_state"]["approved"])
        self.assertEqual(response.json()["blocked_reason"], "journey_budget_exhausted")
        self.assertIsNone(response.json()["continuation"])
        status = self.status("another-scenario-approval", stage="generate_tests").json()
        self.assertEqual([run["run_id"] for run in status["runs"] if run["stage"] == "generate_tests"], [child["run_id"]])
        self.assertEqual(status["receipt"]["blocked_reason"], "journey_budget_exhausted")

    def test_ordinary_scenario_approval_keeps_the_legacy_api_and_schedules_nothing(self):
        parent, payload = self.prepared_plan()
        request_id = "ordinary-scenario-approval"
        response = self.client.post(f"/projects/{PROJECT_ID}/use-cases/reviews", json=payload, headers={"X-Request-ID": request_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["use_cases_state"]["approved"])
        self.assertNotIn("continuation", response.json())
        self.assertIsNone(self.status(request_id, stage="generate_tests").json()["receipt"])
        self.assertEqual([row["run_id"] for row in self.status().json()["runs"]], [parent["run_id"]])
        self.assertFalse(any(path.startswith(f"{continuation.OUTBOX_COLLECTION}/") for path in self.store))

    def test_saved_plan_generation_uses_original_approval_and_recovers_the_exact_run(self):
        target = self.ordinary_approved_plan()
        before = deepcopy(self.store)
        self.assertEqual(self.status().json()["saved_plan_generation"], target)
        self.assertEqual(self.store, before)
        payload = {**target, "template": {"name": "Checkout audit", "format": "table", "fields": ["id", "steps"]}}

        response = self.enroll_saved_plan(payload)

        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()["continuation"]
        run_id = run["run_id"]
        self.assertEqual((run["stage"], run["state"], run["source_snapshot_id"]), ("generate_tests", "queued", SNAPSHOT_ID))
        self.assertEqual({key: self.store[key] for key in before}, before)
        stored = self.store[f"{PROJECT_ROOT}/orchestrator_runs/{run_id}"]["continuation"]
        self.assertEqual(stored["approval_id"], target["review_id"])
        self.assertEqual(stored["options"]["max_provider_requests"], 24)
        self.assertEqual(stored["options"]["template"], payload["template"])
        self.assertEqual([key for key in self.store if key.startswith(f"{continuation.OUTBOX_COLLECTION}/")], [f"{continuation.OUTBOX_COLLECTION}/{run_id}"])
        status = self.status("generate-from-saved-plan", stage="generate_tests").json()
        self.assertNotIn("saved_plan_generation", status)
        self.assertEqual(status["receipt"]["run_id"], run_id)
        self.assertEqual(status["runs"], [run])

        enqueued = deepcopy(self.store)
        self.assertEqual(self.enroll_saved_plan(payload).json(), response.json())
        self.assertEqual(self.enroll_saved_plan(target).status_code, 409)
        duplicate = self.enroll_saved_plan(payload, "other-tab-key")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "saved_plan_generation_exists")
        reapproval = {"snapshot_id": SNAPSHOT_ID, "base_project_revision": target["base_project_revision"], "decision": "approve"}
        for route in (TEST_APPROVAL_PATH, f"/projects/{PROJECT_ID}/use-cases/reviews"):
            conflicting_review = self.client.post(route, json=reapproval, headers={"X-Request-ID": "generate-from-saved-plan"})
            self.assertEqual(conflicting_review.status_code, 409, conflicting_review.text)
        self.assertEqual(self.store, enqueued)

    def test_saved_plan_generation_blocks_inexact_sources_and_prepared_journeys(self):
        target = self.ordinary_approved_plan()
        plan = f"{PROJECT_ROOT}/snapshots/{SNAPSHOT_ID}"
        review = f"{PROJECT_ROOT}/use_case_reviews/{target['review_id']}"
        before = deepcopy(self.store)
        parent_id = continuation.continuation_run_id(PROJECT_ID, self.actor.sub, "prepare_scenarios", before[plan]["source_snapshot_id"])
        mutations = {
            "stale scope": lambda: self.store[PROJECT_ROOT]["stage_state"]["requirements"].update(stale=True),
            "already served": lambda: self.store[PROJECT_ROOT]["stage_state"]["test_cases"].update(current_snapshot_id="existing-suite"),
            "rebound plan": lambda: self.store[plan].update(operation="project.sync.rebind"),
            "prepared plan": lambda: self.store[plan].update(request_id=parent_id),
            "partial scope": lambda: self.store[plan]["payload"].update(coverage_plan=[]),
            "missing analysis": lambda: self.store[plan]["payload"].update(requirement_analysis=[]),
            "missing decision": lambda: self.store.pop(review),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.store.clear()
                self.store.update(deepcopy(before))
                mutate()
                unavailable = deepcopy(self.store)
                self.assertNotIn("saved_plan_generation", self.status().json())
                rejected = self.enroll_saved_plan(target)
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual(rejected.json()["detail"]["code"], "saved_plan_generation_unavailable")
                self.assertEqual(self.store, unavailable)

    def test_test_generation_has_its_own_enabled_guard_and_owner_only_cancel(self):
        _, payload = self.prepared_plan()
        request_id = "scenario-approved-for-generation"
        initial = deepcopy(self.store)
        with patch.object(routes, "approval_test_cases_enabled", return_value=False):
            status = self.status().json()
            self.assertTrue(status["enabled"])
            self.assertFalse(status["tests_enabled"])
            self.assertEqual(self.approve_tests(payload, request_id).status_code, 409)
            self.assertEqual(self.store, initial)
        with patch.object(self.firestore, "_emulator_host", None):
            self.assertFalse(self.status().json()["tests_enabled"])
            self.assertEqual(self.approve_tests(payload, request_id).status_code, 409)
            self.assertEqual(self.store, initial)

        self.assertEqual(self.approve_tests({**payload, "decision": "request_changes", "comment": "A change"}, request_id).status_code, 422)
        self.assertEqual(self.approve_tests(payload, "x" * 161).status_code, 422)
        self.assertEqual(self.store, initial)
        approval = self.approve_tests(payload, request_id)
        self.assertEqual(approval.status_code, 200, approval.text)
        child_id = approval.json()["continuation"]["run_id"]
        committed = deepcopy(self.store)
        with patch.object(routes, "approval_test_cases_enabled", return_value=False):
            self.assertEqual(self.cancel(child_id).status_code, 409)
        with patch.object(self, "actor", AuthUser(sub="other", name="Other")):
            self.assertEqual(self.status(request_id, stage="generate_tests").status_code, 404)
            self.assertEqual(self.approve_tests(payload, request_id).status_code, 404)
            self.assertEqual(self.cancel(child_id).status_code, 404)
        self.assertEqual(self.store, committed)
        cancelled = self.cancel(child_id)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["stage"], "generate_tests")
        self.assertEqual(cancelled.json()["state"], "cancelled")

    def test_saved_review_status_and_release_are_owner_only_and_fence_bound(self):
        operation_id = f"draft_{'b' * 64}"
        path = f"{STATUS_PATH}/saved-review/{operation_id}/release"
        attempt = {
            "operation_id": operation_id,
            "fence": 13,
            "state": "interrupted",
            "operation": "test_cases.review",
            "source_snapshot_id": "pending-suite",
            "request_id": "review-request",
            "reason": "provider_outcome_unconfirmed",
        }
        with (
            patch.object(routes, "read_draft_attempt", return_value=attempt) as read,
            patch.object(routes, "release_draft_attempt", return_value=True) as release,
        ):
            response = self.status()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["saved_review_enabled"])
            self.assertEqual(
                response.json()["saved_review_attempt"],
                {field: attempt[field] for field in ("operation_id", "fence", "state", "source_snapshot_id", "request_id")},
            )
            read.assert_called_once_with(self.firestore, PROJECT_ID, self.actor.sub)
            with patch.object(self, "actor", AuthUser(sub="other-account", name="Other")):
                self.assertEqual(self.status().status_code, 404)
                self.assertEqual(self.client.post(path, json={"fence": 13}).status_code, 404)
            with patch.dict(os.environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
                self.assertEqual(self.client.post(path, json={"fence": 13}).status_code, 409)
            self.assertEqual(self.client.post(path, json={"fence": "13"}).status_code, 422)
            self.assertEqual(self.client.post(path, json={"fence": 13, "extra": "value"}).status_code, 422)
            release.assert_not_called()
            response = self.client.post(path, json={"fence": 13})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"released": True})
            release.assert_called_once_with(self.firestore, PROJECT_ID, self.actor.sub, operation_id, 13)

    def test_test_generation_result_projects_the_exact_saved_suite(self):
        _, payload = self.prepared_plan()
        request_id = "scenario-approved-for-result"
        approval = self.approve_tests(payload, request_id)
        self.assertEqual(approval.status_code, 200, approval.text)
        child_id = approval.json()["continuation"]["run_id"]
        child = self.store[f"{PROJECT_ROOT}/orchestrator_runs/{child_id}"]
        child.update(produced_snapshot_ids={"test_cases": "saved-exact-suite"}, project_revision=child["project_revision"] + 1)
        child["continuation"].update(state="completed")
        before = deepcopy(self.store)
        status = self.status(request_id, stage="generate_tests").json()
        self.assertEqual(status["receipt"]["run_id"], child_id)
        run = status["runs"][0]
        self.assertEqual((run["source_snapshot_id"], run["result_snapshot_id"]), (SNAPSHOT_ID, "saved-exact-suite"))
        self.assertEqual(run["state"], "completed")
        self.assertEqual(self.store, before)
