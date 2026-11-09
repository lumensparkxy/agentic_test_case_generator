"""Worker-bound generation uses the existing atomic suite publication path."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.agents.substantive_review import apply_assessment, bind_reviewed_inputs, review_input, validate_assessment
from app.contracts.quality_feedback import ExecutionPrerequisite
from app.models import AuthUser
from app.routers import testcases as routes
from app.services import approval_continuation as continuation
from app.services.approved_test_case_source import ApprovedTestCaseSource
from app.services.test_case_review_progress import GenerationReviewProgress
from app.services.workflow_project_service import append_stage_snapshot, get_project
import test_generation_source_snapshot as fixtures
from test_use_case_review_service import FakeDocument, _run_transaction


class BoundTestCaseContinuationTests(unittest.TestCase):
    def setUp(self):
        fixtures.GenerationPublicationTests.setUp(self)
        self.result.test_cases[0].steps[0].test_data = "  Order–01  "
        self.generate.return_value = self.result.model_dump()
        self.enterContext(patch.object(continuation, "transactional", side_effect=_run_transaction))
        self.guard = Mock(side_effect=self._prepare_publication)
        self.source = None
        self.claim = None

    def _record_values(self, collection):
        return [value for path, value in self.store.items() if path.startswith(collection + "/")]

    def _current_project(self):
        return get_project(self.project.project_id, actor=self.actor)

    def _append(self, stage, payload, request_id, **kwargs):
        return append_stage_snapshot(project_id=self.project.project_id, stage=stage, payload=payload, actor=self.actor, request_id=request_id, **kwargs)

    def _bind_worker(self, *, prior_suite=False):
        plan_rows = [group.model_dump(mode="json") for group in self.result.coverage_plan]
        requirements = [row.model_dump(mode="json") for row in self.payload.requirements]
        previous = self._current_project()
        if prior_suite:
            [prior_case] = routes.persist_test_case_versions(
                current_test_cases=self.result.test_cases,
                actor=self.actor,
                request_id="existing-suite",
                workflow_run_id=None,
                source_event_id=None,
                operation="testcases.generate",
                approved=True,
            )
            self._append(
                "test_cases",
                {"approved": True, "test_cases": [prior_case.model_dump(mode="json")]},
                "existing-suite",
                operation="testcases.generate",
                source_snapshot_id=previous.current_snapshots["use_cases"].snapshot_id,
                approved=True,
            )
        requirements_snapshot = self._append(
            "requirements",
            {"requirements": requirements, "approved": True},
            "approved-requirements",
            operation="requirements.review",
            approved=True,
        )
        context_snapshot = self._append(
            "context",
            {"requirements": requirements, "notes": "Use a synthetic order fixture."},
            "context-inputs",
            operation="requirements.enrich",
            source_snapshot_id=requirements_snapshot.snapshot_id,
            approved=True,
        )
        analysis = [{"requirement_id": row["id"], "requirement_text": row["text"]} for row in requirements]
        plan_snapshot = self._append(
            "use_cases",
            {"coverage_plan": plan_rows, "requirement_analysis": analysis},
            "prepared-plan",
            operation="use_cases.generate",
            source_snapshot_id=requirements_snapshot.snapshot_id,
            metadata={
                "source_requirements_snapshot_id": requirements_snapshot.snapshot_id,
                "source_context_snapshot_id": context_snapshot.snapshot_id,
            },
            approved=True,
        )
        review_id = "exact-scenario-approval"
        self.store[self.project_path]["stage_state"]["use_cases"]["metadata"]["latest_human_review"] = {
            "decision": "approve",
            "snapshot_id": plan_snapshot.snapshot_id,
            "review_id": review_id,
            "reviewer_user_id": self.actor.sub,
            "resulting_project_revision": plan_snapshot.project_revision,
        }
        ids = tuple(row["id"] for row in requirements)
        self.source = ApprovedTestCaseSource(
            requirements_snapshot_id=requirements_snapshot.snapshot_id,
            context_snapshot_id=context_snapshot.snapshot_id,
            use_case_snapshot_id=plan_snapshot.snapshot_id,
            scenario_review_id=review_id,
            requirement_ids=ids,
        )
        self.payload = self.payload.model_copy(update={"requirements": [], "base_project_revision": plan_snapshot.project_revision})
        doc = self.client.collection("qa_projects").document(self.project.project_id)
        transaction = self.client.transaction()
        self.run_id = continuation.enqueue_approval(
            transaction,
            doc,
            client=self.client,
            stage="generate_tests",
            actor_id=self.actor.sub,
            approval_id=review_id,
            request_id="approval-request",
            resulting_revision=plan_snapshot.project_revision,
            source_snapshot_ids={
                "requirements": requirements_snapshot.snapshot_id,
                "context": context_snapshot.snapshot_id,
                "use_cases": plan_snapshot.snapshot_id,
            },
            requirement_ids=list(ids),
            options=continuation.ContinuationOptions(settings_fingerprint="d" * 64),
        )
        transaction.commit()
        self.claim = continuation.claim_stage(self.client, project_id=self.project.project_id, run_id=self.run_id)
        self.assertIsNotNone(self.claim)
        continuation.mark_dispatched(self.client, self.claim)

        document_get = FakeDocument.get

        def reject_read_after_write(document, *, transaction=None, **kwargs):
            if transaction is not None:
                self.assertFalse(transaction.pending, f"Firestore read after a staged write: {document.path}")
            return document_get(document, transaction=transaction, **kwargs)

        self.enterContext(patch.object(FakeDocument, "get", reject_read_after_write))

    def _prepare_publication(self, transaction, client, snapshot):
        self.assertIs(client, self.client)
        self.assertFalse(transaction.pending)
        return continuation.prepare_result_publication(transaction, client, self.claim, snapshot)

    def _generate(self, *, actor=None, payload=None, source=None):
        return routes.generate_bound_test_cases(
            payload or self.payload,
            actor=actor or self.actor,
            request_id=self.claim.stage_request_id,
            approved_source=source or self.source,
            stage_publication_guard=self.guard,
        )

    def _assert_no_debit_or_new_versions(self, *, previous_versions=()):
        self.assertEqual(self.store[self.account_path]["balance_units"], 80)
        self.assertEqual(self.store[self.allocation_path]["consumed_units"], 9)
        self.assertEqual(self._record_values("test_case_sets"), list(previous_versions))
        for collection in ("billing_consumption", "billing_wallet_ledger"):
            self.assertEqual(self._record_values(collection), [])
        self.assertFalse(any(item["quantity"] for item in self._record_values("usage_events")))

    def _generate_with_setup_blocked(self, payload, *, reviewed_context):
        case = self.result.test_cases[0].model_copy(
            update={"execution_prerequisites": [ExecutionPrerequisite(name="orderAccount", kind="credential", description="Provision an order account")]}
        )
        data, digest, _ = review_input([case], payload.requirements, reviewed_context)
        output = json.dumps(
            {
                "input_hash": digest,
                "obligations": [
                    {
                        "requirement_id": payload.requirements[0].id,
                        "source_quote": "Save the confirmed order",
                        "obligation": "Save the confirmed order",
                        "status": "covered",
                        "reason": "The action submits the order and the step checks for confirmation.",
                        "evidence": [{"test_case_id": case.id, **case.steps[0].model_dump(include={"step", "action", "expected"})}],
                    }
                ],
                "case_grounding": [{"test_case_id": case.id, "reason": "Use the declared account prerequisite.", "prerequisites": []}],
            }
        )
        assessment = bind_reviewed_inputs(validate_assessment(output, data, digest), data)
        self.assertEqual(assessment["design_status"], "assessed_complete")
        self.assertEqual(assessment["execution_status"], "blocked")
        return apply_assessment(
            {
                **self.result.model_dump(mode="json"),
                "test_cases": [case],
                "requirement_analysis": [item.model_dump(mode="json") for item in payload.requirement_analysis],
            },
            assessment,
        )

    def test_bound_generation_records_exact_design_proof_without_promoting_legacy_approval(self):
        self._bind_worker()
        self.generate.side_effect = lambda payload, **kwargs: self._generate_with_setup_blocked(payload, reviewed_context=payload.context)
        generated = self._generate()
        current = self._current_project()
        suite = current.current_snapshots["test_cases"]
        self.assertFalse(generated.approved)
        self.assertFalse(current.stage_state["test_cases"].approved)
        self.assertEqual(suite.payload["substantive_assessment"]["design_status"], "assessed_complete")
        self.assertTrue(suite.payload["generation_evidence"]["design_precheck"]["passed"])
        saved = self.store[f"{self.project_path}/snapshots/{suite.snapshot_id}"]
        self.assertEqual(saved["metadata"]["suite_design_proof"]["suite_snapshot_id"], suite.snapshot_id)
        self.assertEqual(current.suite_quality.design_status, "complete")
        self.assertEqual(current.suite_quality.declared_setup, "blocked")
        self.assertEqual(current.suite_quality.human_acceptance, "unavailable")
        self.assertTrue(suite.payload["test_cases"][0]["artifact_version_id"])
        self.assertEqual(suite.payload["test_cases"][0]["steps"][0]["test_data"], "  Order–01  ")
        self.assertEqual(self._generate(), generated)
        self.generate.assert_called_once()
        self.assertEqual(len(self._record_values("billing_consumption")), 1)

    def test_bound_generation_cannot_reuse_assessment_of_different_context(self):
        self._bind_worker()
        self.generate.side_effect = lambda payload, **kwargs: self._generate_with_setup_blocked(
            payload, reviewed_context=payload.context.model_copy(update={"notes": "Different Context provided to assessment."})
        )
        generated = self._generate()
        current = self._current_project()
        suite = current.current_snapshots["test_cases"]
        self.assertEqual(generated.substantive_assessment["design_status"], "assessed_complete")
        self.assertTrue(generated.generation_evidence.design_precheck.passed)
        self.assertNotEqual(generated.substantive_assessment["reviewed_source_binding"]["context_hash"], suite.metadata["source_context_hash"])
        self.assertNotIn("suite_design_proof", self.store[f"{self.project_path}/snapshots/{suite.snapshot_id}"]["metadata"])
        self.assertEqual(current.suite_quality.design_status, "unknown")

    def test_strict_path_refuses_unbound_fallback_before_generation_or_billing(self):
        self._bind_worker()
        before = deepcopy(self.store)
        wrong_source = replace(self.source, scenario_review_id="earlier-human-review")
        with self.assertRaisesRegex(ValueError, "requires a publication guard"):
            routes.generate_bound_test_cases(self.payload, actor=self.actor, request_id=self.claim.stage_request_id, approved_source=self.source)
        with (
            patch.object(routes, "_project_generation_source", side_effect=AssertionError("Legacy draft fallback")) as fallback,
            self.assertRaises(HTTPException) as blocked,
        ):
            self._generate(source=wrong_source)
        self.assertEqual(blocked.exception.status_code, 409)
        fallback.assert_not_called()
        self.generate.assert_not_called()
        self.billing.assert_not_called()
        self.guard.assert_not_called()
        self.assertEqual(self.store, before)

    def test_publication_commit_reply_loss_and_historical_replay_charge_once(self):
        self._bind_worker()
        publish = routes._append_project_generation_snapshots
        lookup = routes._test_case_publication_receipt
        lookups = 0

        def lose_publication_reply(**kwargs):
            publish(**kwargs)
            raise HTTPException(503, "Synthetic commit reply lost")

        def lose_first_receipt_read(*args, **kwargs):
            nonlocal lookups
            lookups += 1
            if lookups == 2:
                raise HTTPException(503, "Synthetic receipt read unavailable")
            return lookup(*args, **kwargs)

        with (
            patch.object(routes, "_append_project_generation_snapshots", side_effect=lose_publication_reply),
            patch.object(routes, "_test_case_publication_receipt", side_effect=lose_first_receipt_read),
            self.assertRaises(HTTPException) as unavailable,
        ):
            self._generate()
        self.assertEqual(unavailable.exception.detail["code"], "generation_publication_unconfirmed")

        published = self._current_project()
        suite = published.current_snapshots["test_cases"]
        saved = self._generate()
        self.assertEqual(published.current_revision, self.payload.base_project_revision + 1)
        self.assertEqual(published.current_snapshots["use_cases"].snapshot_id, self.source.use_case_snapshot_id)
        self.assertEqual(suite.source_snapshot_id, self.source.use_case_snapshot_id)
        self.assertEqual(suite.metadata["source_requirements_snapshot_id"], self.source.requirements_snapshot_id)
        self.assertEqual(suite.metadata["source_context_snapshot_id"], self.source.context_snapshot_id)
        self.assertEqual(suite.payload["test_cases"][0]["artifact_version_id"], saved.test_cases[0].artifact_version_id)

        generated_input = self.generate.call_args.args[0]
        self.assertEqual([row.id for row in generated_input.requirements], list(self.source.requirement_ids))
        self.assertEqual(generated_input.context.notes, "Use a synthetic order fixture.")
        source_inputs = self.generate.call_args.kwargs
        self.assertEqual(source_inputs["source_coverage_plan"], published.current_snapshots["use_cases"].payload["coverage_plan"])
        self.assertIsInstance(source_inputs["review_progress"], GenerationReviewProgress)

        [usage] = self._record_values("usage_events")
        [debit] = self._record_values("billing_consumption")
        [ledger] = self._record_values("billing_wallet_ledger")
        self.assertEqual((usage["status"], usage["quantity"], ledger["units_delta"]), ("completed", 1, -7))
        self.assertEqual(debit["source_event_id"], suite.source_event_id)
        self.assertEqual(self.store[self.account_path]["balance_units"], 73)
        self.assertEqual(self.store[self.allocation_path]["consumed_units"], 16)
        run_path = f"{self.project_path}/orchestrator_runs/{self.run_id}"
        run = self.store[run_path]
        self.assertEqual(run["continuation"]["state"], "completed")
        self.assertEqual(run["produced_snapshot_ids"], {"test_cases": suite.snapshot_id})
        snapshot_path = f"{self.project_path}/snapshots/{suite.snapshot_id}"
        publication_transaction = next(tx for tx in self.client.transactions if ("create", snapshot_path) in tx.operations)
        committed_paths = {path for _, path in publication_transaction.operations}
        self.assertIn(run_path, committed_paths)
        self.assertIn(self.account_path, committed_paths)
        self.assertIn(self.allocation_path, committed_paths)
        self.assertTrue(any(path.startswith("test_case_sets/") for path in committed_paths))

        self._append("context", {"notes": "A new input revision"}, "later-context", operation="requirements.enrich")
        self.assertTrue(self._current_project().stage_state["test_cases"].stale)
        before_stale_replay = deepcopy(self.store)
        self.assertEqual(self._generate().model_dump(), saved.model_dump())
        self.assertEqual(self.store, before_stale_replay)

        later = self._append("test_cases", suite.payload, "later-suite", operation="test_cases.review", source_snapshot_id=suite.source_snapshot_id)
        self.assertNotEqual(later.snapshot_id, suite.snapshot_id)
        before_historical_replay = deepcopy(self.store)
        self.assertEqual(self._generate().model_dump(), saved.model_dump())
        with self.assertRaises(HTTPException) as mismatch:
            self._generate(source=replace(self.source, scenario_review_id="another-approval"))
        self.assertEqual(mismatch.exception.status_code, 409)
        with self.assertRaises(HTTPException) as forbidden:
            self._generate(actor=AuthUser(sub="other-user", name="Other reviewer"))
        self.assertEqual(forbidden.exception.status_code, 403)
        self.assertEqual(self.store, before_historical_replay)
        self.generate.assert_called_once()
        self.billing.assert_called_once()
        self.guard.assert_called_once()

    def test_incomplete_replacement_leaves_the_existing_suite_and_versions_unchanged(self):
        self._bind_worker(prior_suite=True)
        prior = self._current_project()
        prior_versions = deepcopy(self._record_values("test_case_sets"))
        self.assertTrue(prior.current_snapshots["test_cases"].payload["test_cases"][0]["artifact_version_id"])
        self.assertTrue(prior_versions)
        self.generate.return_value = {
            **self.result.model_dump(),
            "approved": False,
            "generation_tasks": [{"requirement_ids": ["REQ-1"], "scenario_refs": ["SCN-1"], "reason": "Missing persistence assertion"}],
        }
        with self.assertRaises(HTTPException) as incomplete:
            self._generate()
        self.assertEqual(incomplete.exception.status_code, 422)
        self.assertIn("existing suite was preserved", incomplete.exception.detail)
        current = self._current_project()
        self.assertEqual(current.current_revision, prior.current_revision)
        self.assertEqual(current.current_snapshots["test_cases"], prior.current_snapshots["test_cases"])
        self.guard.assert_not_called()
        self._assert_no_debit_or_new_versions(previous_versions=prior_versions)

    def test_cancelled_claim_cannot_publish_or_charge_after_model_returns(self):
        self._bind_worker()
        append = routes.append_stage_snapshot

        def cancel_before_commit(**kwargs):
            self.assertTrue(continuation.cancel_stage(self.client, project_id=self.project.project_id, run_id=self.run_id, actor_id=self.actor.sub))
            return append(**kwargs)

        with patch.object(routes, "append_stage_snapshot", side_effect=cancel_before_commit), self.assertRaises(HTTPException):
            self._generate()
        self.generate.assert_called_once()
        self.guard.assert_called_once()
        self.assertNotIn("test_cases", self._current_project().current_snapshots)
        self.assertEqual(self.store[f"{self.project_path}/orchestrator_runs/{self.run_id}"]["continuation"]["state"], "cancelled")
        self._assert_no_debit_or_new_versions()

    def test_expiry_after_debit_is_staged_rolls_back_publication_and_charge(self):
        self._bind_worker()

        def expire_before_advance(transaction, client, snapshot):
            advance = self._prepare_publication(transaction, client, snapshot)

            def advance_after_expiry():
                self.assertTrue(any(reference.path == self.account_path for _, reference, _ in transaction.pending))
                self.assertEqual(self.store[self.account_path]["balance_units"], 80)
                after_lease = continuation._utcnow() + timedelta(minutes=5)
                with patch.object(continuation, "_utcnow", return_value=after_lease):
                    advance()

            return advance_after_expiry

        self.guard.side_effect = expire_before_advance
        with self.assertRaises(HTTPException):
            self._generate()
        self.generate.assert_called_once()
        self.guard.assert_called_once()
        self.assertNotIn("test_cases", self._current_project().current_snapshots)
        self.assertEqual(self.store[f"{self.project_path}/orchestrator_runs/{self.run_id}"]["continuation"]["state"], "running")
        self._assert_no_debit_or_new_versions()

    def test_context_change_during_generation_blocks_publication_and_debit(self):
        self._bind_worker()

        def change_context(*args, **kwargs):
            self._append("context", {"notes": "A newer source"}, "changed-context", operation="requirements.enrich")
            return self.result.model_dump()

        self.generate.side_effect = change_context
        with self.assertRaises(HTTPException) as conflict:
            self._generate()
        self.assertEqual(conflict.exception.status_code, 409)
        self.guard.assert_not_called()
        self.assertNotIn("test_cases", self._current_project().current_snapshots)
        self._assert_no_debit_or_new_versions()

    def test_review_outage_saves_exact_cases_and_private_review_progress(self):
        self._bind_worker()
        reason = "Independent reviewer unavailable."
        group_key = "saved-group"
        group_evidence = '{"status":"complete"}'

        def generate_with_reviewer_outage(*args, review_progress, **kwargs):
            review_progress.load("review-fingerprint", [group_key])
            review_progress.save(group_key, group_evidence)
            return {
                **self.result.model_dump(),
                "approved": False,
                "review": {"approved": False, "score": 100, "threshold": 90, "blocking_issues": [reason]},
                "workflow_diagnostics": {"status": "partial"},
                "substantive_assessment": {
                    "status": "unknown",
                    "reason": reason,
                    "diagnostics": {"failure_reason": "provider_unavailable"},
                },
            }

        self.generate.side_effect = generate_with_reviewer_outage
        response = self._generate()
        self.assertFalse(response.approved)
        suite = self._current_project().current_snapshots["test_cases"]
        self.assertEqual(suite.payload["substantive_assessment"]["status"], "unknown")
        self.assertTrue(suite.metadata["review_retry_available"])
        [step] = suite.payload["test_cases"][0]["steps"]
        self.assertEqual(step, response.test_cases[0].steps[0].model_dump(mode="json"))
        self.assertEqual(step["test_data"], "  Order–01  ")
        progress = self.store[f"{self.project_path}/test_case_review_progress/current"]
        self.assertEqual(progress["items"], {group_key: group_evidence})
