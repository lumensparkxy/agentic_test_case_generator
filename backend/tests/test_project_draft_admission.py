from copy import deepcopy
from datetime import datetime, timedelta, timezone
from os import environ
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from google.api_core.exceptions import Aborted, ServiceUnavailable
from test_approval_continuation import _Client
from test_use_case_review_service import _run_transaction

from app.services import approval_continuation as continuation
from app.services import project_draft_admission as admission
from app.services.workflow_project_service import ProjectPermissionError

SOURCE_IDS = {"requirements": "scope-v1", "context": None, "use_cases": "plan-v1", "test_cases": "suite-v1"}
OPERATION = "test_cases.review"
DEMO_HOST = "127.0.0.1:44124"


class DraftAdmissionTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.client = _Client({})
        self.client.project = admission.DEMO_PROJECT_ID
        self.client._emulator_host = DEMO_HOST
        self.enterContext(
            patch.dict(
                environ,
                {
                    "TCG_APPROVAL_SCENARIOS_ENABLED": "true",
                    "FIREBASE_PROJECT_ID": admission.DEMO_PROJECT_ID,
                    "FIRESTORE_EMULATOR_HOST": DEMO_HOST,
                    "GOOGLE_GENAI_USE_VERTEXAI": "false",
                    "MODEL_NAME": "gemini-3.8-flash",
                    "ADK_SKILLS_ENABLED": "false",
                    "ADK_MEMORY_ENABLED": "false",
                },
            )
        )
        for module in (admission, continuation):
            self.enterContext(patch.object(module, "_utcnow", side_effect=lambda: self.now))
            self.enterContext(patch.object(module, "transactional", side_effect=_run_transaction))
        self.project = self.seed("p1")

    def seed(self, project_id):
        doc = self.client.collection("qa_projects").document(project_id)
        doc.set(
            {
                "project_id": doc.id,
                "owner_user_id": "owner",
                "status": "active",
                "current_revision": 8,
                "stage_state": {stage: {"current_snapshot_id": snapshot_id} for stage, snapshot_id in SOURCE_IDS.items() if snapshot_id},
            }
        )
        return doc

    def claim(self, request_id="assess-exact", *, project_id=None, sources=SOURCE_IDS, revision=8):
        return admission.claim_draft_attempt(
            self.client,
            project_id=project_id or self.project.id,
            actor_id="owner",
            request_id=request_id,
            operation=OPERATION,
            source_snapshot_ids=sources,
            base_revision=revision,
            max_requests=2,
            deadline_seconds=120,
        )

    def enqueue(self):
        transaction = self.client.transaction()
        run_id = continuation.enqueue_approval(
            transaction,
            self.project,
            client=self.client,
            stage="prepare_scenarios",
            actor_id="owner",
            approval_id=SOURCE_IDS["requirements"],
            request_id="approve-scope",
            resulting_revision=8,
            source_snapshot_ids={stage: SOURCE_IDS[stage] for stage in ("requirements", "context")},
            requirement_ids=["REQ-1"],
            options=continuation.ContinuationOptions(settings_fingerprint="a" * 64),
        )
        transaction.commit()
        return run_id

    def claim_worker(self, run_id, lease_seconds=60):
        return continuation.claim_stage(self.client, project_id=self.project.id, run_id=run_id, lease_seconds=lease_seconds)

    def attempt(self, claim):
        return self.project.collection("project_draft_attempts").document(claim.operation_id).get().to_dict()

    def lease(self):
        return continuation.project_lease_ref(self.project).get().to_dict()

    def state(self):
        return admission.read_draft_attempt(self.client, self.project.id, "owner")

    def release(self, claim, *, actor_id="owner", fence=None):
        return admission.release_draft_attempt(self.client, self.project.id, actor_id, claim.operation_id, claim.fence if fence is None else fence)

    def receipt(self, *, request_id="assess-exact", source="suite-v1", revision=8):
        return admission.read_draft_attempt_result(
            self.client,
            project_id=self.project.id,
            actor_id="owner",
            request_id=request_id,
            operation=OPERATION,
            source_snapshot_id=source,
            base_revision=revision,
        )

    def snapshot(self, **overrides):
        return {
            "snapshot_id": "reviewed-suite-v2",
            "project_id": self.project.id,
            "actor_user_id": "owner",
            "request_id": "assess-exact",
            "stage": "test_cases",
            "operation": OPERATION,
            "project_revision": 9,
            "source_snapshot_id": "suite-v1",
            "metadata": {"source_snapshot_ids": {"requirements": "scope-v1", "use_cases": "plan-v1"}},
            **overrides,
        }

    def publish(self, claim, snapshot):
        def commit(transaction):
            claim.finish_on_commit(transaction, snapshot)
            transaction.create(self.project.collection("snapshots").document(snapshot["snapshot_id"]), snapshot)
            transaction.update(self.project, {"current_revision": snapshot["project_revision"]})

        _run_transaction(commit)(self.client.transaction())

    def assert_conflict(self, code, operation):
        with self.assertRaises(HTTPException) as error:
            operation()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], code)

    def test_flag_off_skips_client_lookup_and_demo_mismatch_fails_closed(self):
        with patch.object(admission, "get_required_firestore_client", return_value=self.client) as lookup:
            self.assertIs(admission.draft_admission_client(), self.client)
            lookup.assert_called_once()
            lookup.reset_mock()
            self.assertIs(admission.draft_admission_client(self.client), self.client)
            with patch.dict(environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
                self.assertIsNone(admission.draft_admission_client())
            with patch.dict(environ, {"FIRESTORE_EMULATOR_HOST": "localhost.evil.test:44124"}):
                self.assertIsNone(admission.draft_admission_client(read_only=True))
                with self.assertRaises(HTTPException) as malformed:
                    admission.draft_admission_client()
                self.assertEqual(malformed.exception.status_code, 503)
            self.client._emulator_host = None
            self.assertIsNone(admission.draft_admission_client(self.client, read_only=True))
            with self.assertRaises(HTTPException) as wrong_client:
                admission.draft_admission_client(self.client)
            self.assertEqual(wrong_client.exception.status_code, 503)
            lookup.assert_not_called()

    def test_both_claim_orders_share_existing_worker_fence(self):
        run_id = self.enqueue()
        worker = self.claim_worker(run_id, lease_seconds=1)
        self.assertIsNotNone(worker)
        self.assert_conflict("project_draft_busy", self.claim)
        self.now += timedelta(seconds=2)
        draft = self.claim()
        self.assertGreater(draft.fence, worker.fence)
        self.assertIsNone(self.claim_worker(run_id))
        with self.assertRaises(continuation.ContinuationConflict):
            continuation.mark_dispatched(self.client, worker)
        other_project = self.seed("p2")
        other = self.claim(project_id=other_project.id)
        self.assertEqual(other.fence, 1)

    def test_expired_unstarted_reclaims_same_identity_and_revokes_old_fence(self):
        first = self.claim()
        self.now += timedelta(seconds=61)
        self.assertIsNone(self.state())
        replacement = self.claim()
        self.assertEqual(replacement.operation_id, first.operation_id)
        self.assertGreater(replacement.fence, first.fence)
        self.assert_conflict("project_draft_claim_lost", first.reserve)
        self.assert_conflict("project_draft_release_mismatch", lambda: self.release(first))
        first.stop()
        self.assertEqual(self.state()["fence"], replacement.fence)
        replacement.stop()
        self.assertIsNone(self.state())
        self.assertEqual(self.attempt(replacement)["state"], "unstarted")
        self.assertGreater(self.claim().fence, replacement.fence)

    def test_reserved_dispatch_blocks_even_after_expiry_until_exact_owner_ack(self):
        draft = self.claim()
        self.assertEqual(draft.reserve(), 25)
        self.assertEqual(draft.reserve(), 25)
        self.assert_conflict("project_draft_budget_unavailable", draft.reserve)
        self.assertEqual(self.attempt(draft)["provider_requests_reserved"], 2)
        self.assertEqual(self.state()["request_id"], "assess-exact")
        self.now += timedelta(seconds=61)
        before_read = deepcopy(self.client.store)
        self.assertEqual(self.state()["state"], "interrupted")
        self.assertEqual(self.client.store, before_read)
        run_id = self.enqueue()
        self.assertIsNone(self.claim_worker(run_id))
        self.assert_conflict("project_draft_busy", lambda: self.claim("different-request"))
        with self.assertRaises(ProjectPermissionError):
            self.release(draft, actor_id="other-user")
        self.assertTrue(self.release(draft))
        self.assertEqual(self.attempt(draft)["reason"], "provider_outcome_acknowledged")
        self.assertEqual(self.attempt(draft)["provider_requests_reserved"], 2)
        worker = self.claim_worker(run_id)
        self.assertGreater(worker.fence, draft.fence)
        self.assertTrue(self.release(draft))
        self.assertEqual(self.lease()["run_id"], worker.run_id)
        self.assert_conflict("project_draft_recovery_required", self.claim)

    def test_running_release_is_cancel_and_revokes_checkpoints_and_publish(self):
        draft = self.claim()
        draft.reserve()
        self.assertTrue(self.release(draft))
        self.assertEqual(self.attempt(draft)["reason"], "cancelled_by_owner")
        self.assert_conflict("project_draft_claim_lost", lambda: draft.validate(self.client.transaction()))
        self.assert_conflict("project_draft_claim_lost", lambda: self.publish(draft, self.snapshot()))
        self.assertIsNone(self.receipt())

    def test_current_source_or_owner_change_prevents_provider_and_publishing(self):
        for update in (
            {"current_revision": 9},
            {"status": "archived"},
            {"stage_state": {stage: {"current_snapshot_id": key, "stale": stage == "use_cases"} for stage, key in SOURCE_IDS.items() if key}},
        ):
            with self.subTest(update=update):
                original = deepcopy(self.client.store)
                draft = self.claim()
                draft.reserve()
                self.project.update(update)
                self.assert_conflict("project_draft_claim_lost", draft.reserve)
                self.assert_conflict("project_draft_claim_lost", lambda: self.publish(draft, self.snapshot()))
                draft.stop()
                self.assertEqual(self.state()["state"], "interrupted")
                self.assertTrue(self.release(draft))
                self.client.store.clear()
                self.client.store.update(original)

    def test_fenced_result_and_release_are_atomic_and_replay_historically(self):
        draft = self.claim()
        draft.reserve()
        for invalid in (
            {"request_id": "other"},
            {"actor_user_id": "other"},
            {"source_snapshot_id": "other"},
            {"project_revision": 10},
            {"metadata": {"source_snapshot_ids": {"requirements": "scope-v1", "use_cases": "other"}}},
        ):
            with self.subTest(invalid=invalid):
                self.assert_conflict("project_draft_result_mismatch", lambda: self.publish(draft, self.snapshot(**invalid)))
        before = deepcopy(self.client.store)
        self.client.fail_on_create_segment = "/snapshots/"
        with self.assertRaises(RuntimeError):
            self.publish(draft, self.snapshot())
        self.assertEqual(self.client.store, before)
        self.client.fail_on_create_segment = None
        self.publish(draft, self.snapshot())
        self.assertIsNone(self.state())
        self.assertEqual(self.receipt(), "reviewed-suite-v2")
        replay = self.claim()
        self.assertEqual(replay.result_snapshot_id, "reviewed-suite-v2")
        self.assert_conflict("project_draft_claim_lost", replay.reserve)
        self.assert_conflict("project_draft_request_mismatch", lambda: self.receipt(source="other-suite"))
        self.assert_conflict("project_draft_request_mismatch", lambda: self.claim(revision=9))
        self.project.collection("snapshots").document("reviewed-suite-v2").update({"project_id": "mismatch"})
        with self.assertRaises(HTTPException) as uncertain:
            self.receipt()
        self.assertEqual(uncertain.exception.status_code, 503)

    def test_unknown_dispatch_acknowledgement_failure_does_not_allow_another_request(self):
        draft = self.claim()

        def commit_then_fail(callback):
            def run(transaction):
                _run_transaction(callback)(transaction)
                raise ServiceUnavailable("synthetic lost reservation response")

            return run

        with patch.object(admission, "transactional", side_effect=commit_then_fail):
            with self.assertRaises(ServiceUnavailable):
                draft.reserve()
        self.assertEqual(self.attempt(draft)["provider_requests_reserved"], 2)
        self.assert_conflict("project_draft_budget_unavailable", draft.reserve)
        draft.stop()
        self.assertEqual(self.state()["state"], "interrupted")
        self.assertIsNone(self.claim_worker(self.enqueue()))

    def test_transaction_contention_reports_conflict_without_disguising_outage(self):
        def fail_with(cause):
            def transaction(_):
                raise ValueError("transaction attempts exhausted") from cause

            return transaction

        with patch.object(admission, "transactional", return_value=fail_with(Aborted("synthetic contention"))):
            self.assert_conflict("project_draft_busy", self.claim)
        with patch.object(admission, "transactional", return_value=fail_with(ServiceUnavailable("synthetic outage"))):
            with self.assertRaises(ValueError):
                self.claim()
