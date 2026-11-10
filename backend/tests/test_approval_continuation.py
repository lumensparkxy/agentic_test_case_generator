from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from google.api_core.exceptions import Aborted, ServiceUnavailable
from test_use_case_review_service import FakeCollection, FakeDocument, FakeFirestoreClient, _run_transaction

from app.contracts.orchestrator import OrchestratorRunRecord
from app.services import approval_continuation as service
from app.services.workflow_project_service import ProjectPermissionError


class _Document(FakeDocument):
    def get(self, *, transaction=None, field_paths=None):
        if transaction is not None and transaction.pending:
            raise RuntimeError("Firestore transaction reads must precede writes")
        return super().get(transaction=transaction, field_paths=field_paths)

    def collection(self, name):
        return _Collection(f"{self.path}/{name}", self.store)


class _Collection(FakeCollection):
    def document(self, document_id):
        return _Document(f"{self.path}/{document_id}", self.store)


class _Client(FakeFirestoreClient):
    def collection(self, name):
        return _Collection(name, self.store)


class ApprovalContinuationTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.enterContext(patch.object(service, "_utcnow", side_effect=lambda: self.now))
        self.enterContext(patch.object(service, "transactional", side_effect=_run_transaction))
        self.client = _Client({})
        self.project = self.client.collection("qa_projects").document("project-1")
        self.project.set(
            {
                "project_id": self.project.id,
                "owner_user_id": "owner",
                "status": "active",
                "current_revision": 8,
                "stage_state": {
                    "requirements": {"current_snapshot_id": "scope-v1", "approved": False},
                    "context": {"current_snapshot_id": "context-v1"},
                    "use_cases": {
                        "current_snapshot_id": "plan-v1",
                        "approved": True,
                        "metadata": {"latest_human_review": {"review_id": "review-v1", "snapshot_id": "plan-v1", "decision": "approve"}},
                    },
                },
            }
        )

    def enqueue(self, *, stage="prepare_scenarios", revision=8, approval_id=None, options=None):
        sources = {"requirements": "scope-v1", "context": "context-v1"}
        if stage == "generate_tests":
            sources["use_cases"] = "plan-v1"
        transaction = self.client.transaction()
        run_id = service.enqueue_approval(
            transaction,
            self.project,
            client=self.client,
            stage=stage,
            actor_id="owner",
            approval_id=approval_id or ("scope-v1" if stage == "prepare_scenarios" else "review-v1"),
            request_id="request-1",
            resulting_revision=revision,
            source_snapshot_ids=sources,
            requirement_ids=["REQ-2", "REQ-1"],
            options=options or service.ContinuationOptions(settings_fingerprint="f" * 64, max_provider_requests=2),
        )
        transaction.commit()
        return run_id

    def claim(self, run_id, *, seconds=1):
        return service.claim_stage(self.client, project_id=self.project.id, run_id=run_id, lease_seconds=seconds)

    def run_record(self, run_id):
        return self.project.collection("orchestrator_runs").document(run_id).get().to_dict()

    def outbox(self, run_id):
        return self.client.collection(service.OUTBOX_COLLECTION).document(run_id).get().to_dict()

    def snapshot(self, claim, *, stage="use_cases", revision=9):
        source = "scope-v1" if stage == "use_cases" else "plan-v1"
        return {
            "snapshot_id": "result-v1",
            "project_id": self.project.id,
            "stage": stage,
            "operation": "use_cases.generate" if stage == "use_cases" else "testcases.generate",
            "actor_user_id": "owner",
            "request_id": claim.stage_request_id,
            "project_revision": revision,
            "source_snapshot_id": source,
            "metadata": {"source_requirements_snapshot_id": "scope-v1", "source_context_snapshot_id": "context-v1"},
        }

    def test_enqueue_binds_exact_approval_with_a_private_compatible_record(self):
        run_id = self.enqueue()
        self.assertEqual(run_id, service.continuation_run_id(self.project.id, "owner", "prepare_scenarios", "scope-v1"))
        run = self.run_record(run_id)
        self.assertEqual(run["continuation"]["requirement_ids"], ["REQ-2", "REQ-1"])
        self.assertEqual(run["continuation"]["state"], "queued")
        self.assertEqual(run["project_revision"], 8)
        self.assertEqual(self.outbox(run_id), {"project_id": self.project.id, "run_id": run_id, "available_at": self.now})
        history = OrchestratorRunRecord.model_validate(run).model_dump()
        self.assertEqual(history["metadata"], {})
        self.assertNotIn("continuation", history)

    def test_only_one_project_claim_and_unstarted_expiry_advances_the_fence(self):
        first = self.enqueue()
        second = self.enqueue(stage="generate_tests")
        original = self.claim(first)
        self.assertIsNone(self.claim(first))
        self.assertIsNone(self.claim(second))
        self.now += timedelta(seconds=2)
        replacement = self.claim(first)
        self.assertGreater(replacement.fence, original.fence)
        self.assertEqual(replacement.stage_request_id, original.stage_request_id)
        self.assertEqual(self.run_record(first)["continuation"]["attempts"], 0)
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, original)
        with self.assertRaises(service.ContinuationConflict):
            service.renew_claim(self.client, original)

    def test_possible_paid_dispatch_blocks_other_runs_until_explicit_cancellation(self):
        first = self.enqueue()
        second = self.enqueue(stage="generate_tests")
        claim = self.claim(first)
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, claim, provider_requests=3)
        service.mark_dispatched(self.client, claim, provider_requests=2)
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, claim)
        self.now += timedelta(seconds=2)
        self.assertIsNone(self.claim(second))
        self.assertIsNone(self.claim(first))
        intent = self.run_record(first)["continuation"]
        self.assertEqual((intent["state"], intent["reason"]), ("interrupted", "provider_outcome_unconfirmed"))
        self.assertEqual((intent["attempts"], intent["provider_requests_reserved"]), (1, 2))
        self.assertIsNone(self.outbox(first))
        self.assertIsNone(self.claim(second))
        self.assertTrue(service.cancel_stage(self.client, project_id=self.project.id, run_id=first, actor_id="owner"))
        self.assertEqual(self.run_record(first)["continuation"]["dispatch"], intent["dispatch"])
        replacement = self.claim(second)
        self.assertGreater(replacement.fence, claim.fence)
        with self.assertRaises(service.ContinuationConflict):
            service.prepare_result_publication(self.client.transaction(), self.client, claim, self.snapshot(claim))

    def test_changed_sources_revoke_dispatch_but_preserve_unknown_attempt(self):
        first = self.enqueue()
        claim = self.claim(first)
        service.mark_dispatched(self.client, claim)
        self.project.update({"current_revision": 9})
        second = self.enqueue(stage="generate_tests", revision=9)
        self.assertIsNone(self.claim(second))
        self.assertIsNone(self.claim(first))
        self.assertEqual(self.run_record(first)["continuation"]["reason"], "sources_changed_provider_outcome_unconfirmed")
        self.assertIsNone(self.claim(second))
        with self.assertRaises(service.ContinuationConflict):
            service.prepare_result_publication(self.client.transaction(), self.client, claim, self.snapshot(claim))

    def test_stale_sources_and_mismatched_plan_review_never_admit_a_claim(self):
        plan_run = self.enqueue(stage="generate_tests", approval_id="earlier-review")
        self.assertIsNone(self.claim(plan_run))
        self.assertEqual(self.run_record(plan_run)["continuation"]["state"], "superseded")

        scope_run = self.enqueue()
        claim = self.claim(scope_run)
        project = self.project.get().to_dict()
        project["stage_state"]["context"]["stale"] = True
        self.project.set(project)
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, claim)
        self.assertIsNone(self.claim(scope_run))
        self.assertEqual(self.run_record(scope_run)["continuation"]["state"], "superseded")
        self.assertIsNone(self.outbox(scope_run))

    def test_cancellation_checks_owner_and_is_idempotent(self):
        run_id = self.enqueue()
        claim = self.claim(run_id)
        before = deepcopy(self.client.store)
        with self.assertRaises(ProjectPermissionError):
            service.cancel_stage(self.client, project_id=self.project.id, run_id=run_id, actor_id="other")
        self.assertEqual(self.client.store, before)
        for _ in range(2):
            self.assertTrue(service.cancel_stage(self.client, project_id=self.project.id, run_id=run_id, actor_id="owner"))
        self.assertEqual(self.run_record(run_id)["continuation"]["state"], "cancelled")
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, claim)

    def test_renewal_respects_deadline_and_storage_failure_does_not_reissue_work(self):
        run_id = self.enqueue(options=service.ContinuationOptions(settings_fingerprint="f" * 64, deadline_seconds=2))
        claim = self.claim(run_id)
        service.renew_claim(self.client, claim, lease_seconds=60)
        self.assertEqual(self.outbox(run_id)["available_at"], self.now + timedelta(seconds=2))
        before = deepcopy(self.client.store)
        with patch.object(_Document, "get", side_effect=RuntimeError("Firestore unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Firestore unavailable"):
                service.mark_dispatched(self.client, claim)
        self.assertEqual(self.client.store, before)
        self.now += timedelta(seconds=2)
        self.assertIsNone(self.claim(run_id))
        self.assertEqual(self.run_record(run_id)["continuation"]["reason"], "deadline_exceeded")

    def test_undispatched_expiry_can_retry_once_with_the_original_request_and_budget(self):
        options = service.ContinuationOptions(settings_fingerprint="f" * 64, max_provider_requests=2, deadline_seconds=2)
        run_id = self.enqueue(options=options)
        original = self.claim(run_id)
        self.now += timedelta(seconds=2)
        self.assertIsNone(self.claim(run_id))
        expired = self.run_record(run_id)
        self.assertTrue(service.can_retry_undispatched_stage(expired))
        args = dict(project_id=self.project.id, run_id=run_id, actor_id="owner", options=options)
        service.retry_undispatched_stage(self.client, **args)
        queued = deepcopy(self.client.store)
        service.retry_undispatched_stage(self.client, **args)
        self.assertEqual(self.client.store, queued)
        retried = self.run_record(run_id)
        self.assertEqual(retried["request_id"], expired["request_id"])
        self.assertEqual(retried["project_revision"], expired["project_revision"])
        self.assertEqual(retried["continuation"]["options"], expired["continuation"]["options"])
        self.assertFalse(service.can_retry_undispatched_stage(retried))
        claim = self.claim(run_id)
        self.assertEqual(claim.stage_request_id, original.stage_request_id)
        self.assertGreater(claim.fence, original.fence)
        self.assertEqual(claim.deadline_at, self.now + timedelta(seconds=2))
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, original)
        self.now += timedelta(seconds=2)
        self.assertIsNone(self.claim(run_id))
        service.retry_undispatched_stage(self.client, **args)
        self.assertIsNone(self.outbox(run_id))
        self.assertEqual(self.run_record(run_id)["continuation"]["attempts"], 0)

    def test_retry_refuses_changed_source_settings_and_possible_dispatch(self):
        options = service.ContinuationOptions(settings_fingerprint="f" * 64, max_provider_requests=2)
        run_id = self.enqueue(options=options)
        claim = self.claim(run_id)
        args = dict(project_id=self.project.id, run_id=run_id, actor_id="owner", options=options)
        service.mark_dispatched(self.client, claim)
        service.stop_stage(self.client, claim, reason="scenario_preparation_failed")
        before = deepcopy(self.client.store)
        with self.assertRaises(service.ContinuationConflict):
            service.retry_undispatched_stage(self.client, **args)
        self.assertEqual(self.client.store, before)
        service.cancel_stage(self.client, project_id=self.project.id, run_id=run_id, actor_id="owner")
        run_id = self.enqueue(stage="generate_tests", options=options)
        claim = self.claim(run_id)
        service.stop_stage(self.client, claim, reason="test_case_generation_failed")
        args.update(run_id=run_id)
        before = deepcopy(self.client.store)
        with self.assertRaisesRegex(service.ContinuationConflict, "different generation settings"):
            service.retry_undispatched_stage(self.client, **{**args, "options": options.model_copy(update={"settings_fingerprint": "a" * 64})})
        self.assertEqual(self.client.store, before)
        self.project.update({"current_revision": 9})
        before = deepcopy(self.client.store)
        with self.assertRaises(service.ContinuationConflict):
            service.retry_undispatched_stage(self.client, **args)
        self.assertEqual(self.client.store, before)

    def test_contention_leaves_outbox_due_but_unknown_storage_error_is_visible(self):
        run_id = self.enqueue()
        before = deepcopy(self.client.store)

        def exhausted(_):
            raise ValueError("transaction retry exhausted") from Aborted("transaction contention")

        with patch.object(service, "transactional", return_value=exhausted):
            self.assertIsNone(self.claim(run_id))
        self.assertEqual(self.client.store, before)

        def unavailable(_):
            raise ServiceUnavailable("unknown commit outcome")

        with patch.object(service, "transactional", return_value=unavailable):
            with self.assertRaises(ServiceUnavailable):
                self.claim(run_id)
        self.assertIsNotNone(self.claim(run_id))

    def test_lost_dispatch_reply_cannot_authorize_a_second_provider_attempt(self):
        run_id = self.enqueue()
        claim = self.claim(run_id)

        def commit_then_lose_reply(callback):
            def execute(transaction):
                _run_transaction(callback)(transaction)
                raise ServiceUnavailable("Dispatch commit response lost")

            return execute

        with patch.object(service, "transactional", side_effect=commit_then_lose_reply):
            with self.assertRaises(ServiceUnavailable):
                service.mark_dispatched(self.client, claim)
        with self.assertRaises(service.ContinuationConflict):
            service.mark_dispatched(self.client, claim)
        self.now += timedelta(seconds=2)
        self.assertIsNone(self.claim(run_id))
        self.assertEqual(self.run_record(run_id)["continuation"]["attempts"], 1)
        self.assertEqual(self.run_record(run_id)["continuation"]["state"], "interrupted")

    def test_result_advances_with_the_exact_snapshot_in_the_publication_transaction(self):
        for stage, result_stage, expected_state in (
            ("prepare_scenarios", "use_cases", "awaiting_user"),
            ("generate_tests", "test_cases", "completed"),
        ):
            with self.subTest(stage=stage):
                revision = self.project.get().to_dict()["current_revision"]
                run_id = self.enqueue(stage=stage, revision=revision)
                claim = self.claim(run_id)
                service.mark_dispatched(self.client, claim)
                snapshot = self.snapshot(claim, stage=result_stage, revision=revision + 1)
                with self.assertRaises(service.ContinuationConflict):
                    service.prepare_result_publication(self.client.transaction(), self.client, claim, {**snapshot, "request_id": "other-request"})

                transaction = self.client.transaction()
                apply_result = service.prepare_result_publication(transaction, self.client, claim, snapshot)
                self.project.get(transaction=transaction)  # Additional billing reads can still run here.
                transaction.create(self.project.collection("snapshots").document(snapshot["snapshot_id"]), snapshot)
                transaction.update(self.project, {"current_revision": revision + 1})
                apply_result()
                self.assertEqual(self.run_record(run_id)["continuation"]["state"], "running")
                transaction.rollback()
                self.assertEqual(self.run_record(run_id)["continuation"]["state"], "running")

                transaction = self.client.transaction()
                snapshot["snapshot_id"] = f"{result_stage}-result"
                apply_result = service.prepare_result_publication(transaction, self.client, claim, snapshot)
                transaction.create(self.project.collection("snapshots").document(snapshot["snapshot_id"]), snapshot)
                transaction.update(self.project, {"current_revision": revision + 1})
                apply_result()
                transaction.commit()
                record = self.run_record(run_id)
                self.assertEqual(record["continuation"]["state"], expected_state)
                self.assertEqual(record["project_revision"], revision + 1)
                self.assertEqual(record["produced_snapshot_ids"], {result_stage: snapshot["snapshot_id"]})
                self.assertIsNone(self.outbox(run_id))
