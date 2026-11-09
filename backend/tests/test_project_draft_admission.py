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


class _AdmissionFixture:
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


class DraftAdmissionTests(_AdmissionFixture, TestCase):
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


SYNC_CHANGE = "change_" + "a" * 24
SYNC_SOURCES = {"requirements": "scope-v1", "context": "context-v1", "use_cases": "plan-v1", "test_cases": "suite-v1", "reports": None}


class ProjectSyncAdmissionTests(_AdmissionFixture, TestCase):
    # Use the same clock and independent continuation worker fixture.
    def setUp(self):
        super().setUp()
        self.project.update(
            {
                "project_sync": {
                    "id": SYNC_CHANGE,
                    "project_revision": 8,
                    "stages": {"use_cases": "pending", "test_cases": "pending", "automation": "not_requested"},
                    "expected_snapshots": {stage: source for stage, source in SYNC_SOURCES.items() if source is not None},
                },
                "stage_state": {
                    stage: {"current_snapshot_id": source, "stale": stage in {"use_cases", "test_cases"}}
                    for stage, source in SYNC_SOURCES.items()
                    if source is not None
                },
            }
        )

    def sync_claim(self, stage="use_cases", request_id="accept-change", *, sources=SYNC_SOURCES, revision=8, fingerprint="b" * 64, cap=None):
        return admission.claim_draft_attempt(
            self.client,
            project_id=self.project.id,
            actor_id="owner",
            request_id=request_id,
            operation=f"project.sync.{stage}",
            source_snapshot_ids=sources,
            base_revision=revision,
            max_requests=cap if cap is not None else (7 if stage == "use_cases" else 24),
            deadline_seconds=240,
            change_id=SYNC_CHANGE,
            settings_fingerprint=fingerprint,
        )

    def sync_state(self):
        return admission.read_project_sync_admission(self.client, self.project.id, "owner")

    def release_sync(self, claim):
        return admission.release_draft_attempt(self.client, self.project.id, "owner", claim.operation_id, claim.fence, project_sync=True)

    def sync_snapshot(self, claim, stage="use_cases", *, sources=SYNC_SOURCES, revision=8):
        return {
            "snapshot_id": "new-plan" if stage == "use_cases" else "new-suite",
            "project_id": self.project.id,
            "actor_user_id": "owner",
            "request_id": claim.request_id,
            "stage": stage,
            "operation": "project.sync.draft",
            "project_revision": revision + 1,
            "source_snapshot_id": sources["requirements" if stage == "use_cases" else "use_cases"],
            "metadata": {"change_id": SYNC_CHANGE, "source_snapshot_ids": {stage: source for stage, source in sources.items() if source}},
        }

    def publish_sync(self, claim, snapshot, *, revision=8, sources=SYNC_SOURCES, fingerprint="b" * 64, fail=False):
        stage = snapshot["stage"]
        project = self.project.get().to_dict()
        sync = project["project_sync"]
        stage_state = project["stage_state"]
        sync.update(project_revision=revision + 1)
        sync["stages"][stage] = "draft"
        sync["expected_snapshots"][stage] = snapshot["snapshot_id"]
        receipts = sync.setdefault("stage_receipts", {})
        receipts[stage] = {
            "snapshot_id": snapshot["snapshot_id"],
            "request_id": claim.request_id,
            "base_revision": revision,
            "result_revision": revision + 1,
            "source_snapshot_ids": {stage: source for stage, source in sources.items() if source},
            "settings_fingerprint": fingerprint,
        }
        stage_state[stage] = {"current_snapshot_id": snapshot["snapshot_id"], "stale": False}

        def commit(transaction):
            finish = claim.prepare_publication(transaction, snapshot)
            # Simulate a billing read and write before the callback. The fake
            # rejects any subsequent read in a transaction with staged writes.
            billing = self.project.collection("billing_stub").document(stage)
            billing.get(transaction=transaction)
            transaction.create(billing, {"event_id": f"usage-{stage}"})
            finish()
            if fail:
                raise RuntimeError("synthetic publication failure")
            transaction.create(self.project.collection("snapshots").document(snapshot["snapshot_id"]), snapshot)
            transaction.update(self.project, {"current_revision": revision + 1, "project_sync": sync, "stage_state": stage_state})

        _run_transaction(commit)(self.client.transaction())

    def test_sync_reuses_canonical_identity_before_dispatch_and_remembers_alias_after_release(self):
        first = self.sync_claim()
        self.assertEqual(self.sync_state()["attempt"]["change_id"], SYNC_CHANGE)
        self.assertIsNone(self.state(), "saved-review status must not expose Sync attempts")
        self.assert_conflict("project_draft_release_mismatch", lambda: self.release(first))
        self.now += timedelta(seconds=61)
        self.assertEqual(self.sync_state(), {"blocked": False, "attempt": None})
        alias = self.sync_claim(request_id="resume-known-unstarted")
        self.assertEqual((alias.operation_id, alias.request_id), (first.operation_id, first.request_id))
        self.assertGreater(alias.fence, first.fence)
        self.assert_conflict("project_draft_claim_lost", first.reserve)
        self.assert_conflict("project_draft_release_mismatch", lambda: self.release_sync(first))
        alias.reserve()
        self.assertEqual(self.attempt(alias)["provider_requests_reserved"], 7)
        self.now += timedelta(seconds=61)
        self.assertEqual(self.sync_state()["attempt"]["state"], "interrupted")
        self.assert_conflict("project_draft_recovery_required", lambda: self.sync_claim(request_id="unauthorized-retry"))
        self.assertTrue(self.release_sync(alias))
        for old_key in (first.request_id, "resume-known-unstarted"):
            self.assert_conflict("project_draft_recovery_required", lambda: self.sync_claim(request_id=old_key))
        fresh = self.sync_claim(request_id="deliberate-retry")
        self.assertNotEqual(fresh.operation_id, first.operation_id)
        self.assertEqual(fresh.request_id, "deliberate-retry")
        self.assertGreater(fresh.fence, alias.fence)

    def test_sync_publication_replay_requires_exact_checkpoint_before_next_stage(self):
        first = self.sync_claim()
        first.reserve()
        result = self.sync_snapshot(first)
        bad = {**result, "metadata": {**result["metadata"], "change_id": "wrong"}}
        self.assert_conflict("project_draft_result_mismatch", lambda: self.publish_sync(first, bad))
        before = deepcopy(self.client.store)
        with self.assertRaisesRegex(RuntimeError, "synthetic publication failure"):
            self.publish_sync(first, result, fail=True)
        self.assertEqual(self.client.store, before)
        self.publish_sync(first, result)
        self.assertEqual(first.read_result(), "new-plan")
        self.assertEqual(self.sync_claim(request_id="different-replay-key").result_snapshot_id, "new-plan")
        self.assertEqual(self.sync_state(), {"blocked": False, "attempt": None})
        sources = {**SYNC_SOURCES, "use_cases": "new-plan"}
        raw = self.project.get().to_dict()["project_sync"]
        bad_checkpoint = deepcopy(raw)
        bad_checkpoint["stage_receipts"]["use_cases"]["request_id"] = "foreign"
        self.project.update({"project_sync": bad_checkpoint})
        with self.assertRaises(HTTPException) as mismatch:
            self.sync_claim("test_cases", sources=sources, revision=9)
        self.assertEqual(mismatch.exception.status_code, 503)
        self.project.update({"project_sync": raw})
        second = self.sync_claim("test_cases", sources=sources, revision=9)
        self.assertEqual(second.request_id, first.request_id)
        self.assertNotEqual(second.operation_id, first.operation_id)
        second.reserve()
        self.assertEqual(self.attempt(second)["provider_requests_reserved"], 24)
        self.publish_sync(second, self.sync_snapshot(second, "test_cases", sources=sources, revision=9), sources=sources, revision=9)
        self.assertEqual(second.read_result(), "new-suite")

    def test_sync_sources_and_settings_cannot_drift_and_release_survives_supersession(self):
        with self.assertRaises(ValueError):
            self.sync_claim(cap=8)
        with self.assertRaises(ValueError):
            self.sync_claim(sources={**SYNC_SOURCES, "context": None})
        self.assert_conflict("project_draft_sources_changed", lambda: self.sync_claim(sources={**SYNC_SOURCES, "reports": "wrong"}))
        self.assert_conflict("project_draft_sources_changed", lambda: self.sync_claim("test_cases"))
        first = self.sync_claim()
        self.assert_conflict("project_draft_request_mismatch", lambda: self.sync_claim(fingerprint="c" * 64))
        first.reserve()
        project = self.project.get().to_dict()
        self.project.update({"current_revision": 9, "project_sync": {**project["project_sync"], "id": "change_" + "f" * 24}})
        self.assert_conflict("project_draft_claim_lost", first.reserve)
        self.assert_conflict("project_draft_claim_lost", lambda: self.publish_sync(first, self.sync_snapshot(first)))
        self.assertEqual(self.sync_state()["attempt"]["state"], "interrupted")
        self.assertTrue(self.release_sync(first))
        self.assertEqual(self.attempt(first)["reason"], "provider_outcome_acknowledged")

    def test_sync_and_assessment_never_release_each_others_attempts(self):
        states = self.project.get().to_dict()["stage_state"]
        states["use_cases"]["stale"] = False
        states["test_cases"]["stale"] = False
        self.project.update({"stage_state": states})
        assessment = self.claim(sources={stage: source for stage, source in SYNC_SOURCES.items() if stage != "reports"})
        self.assertEqual(self.sync_state(), {"blocked": True, "attempt": None})
        self.assert_conflict("project_draft_busy", self.sync_claim)
        self.assert_conflict("project_draft_release_mismatch", lambda: self.release_sync(assessment))
        self.assertTrue(self.release(assessment))
        sync = self.sync_claim()
        self.assert_conflict(
            "project_draft_busy",
            lambda: self.claim("different-assessment", sources={stage: source for stage, source in SYNC_SOURCES.items() if stage != "reports"}),
        )
        self.assert_conflict("project_draft_release_mismatch", lambda: self.release(sync))
