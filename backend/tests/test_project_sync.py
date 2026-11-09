from pathlib import Path
import json
from datetime import timedelta
import sys
import unittest
from unittest.mock import patch
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_workflow_project_service as fixtures
from fastapi import HTTPException
from app.contracts.project_sync import AcceptProjectFinding, ProjectSyncRebindInput, RequirementCorrection
from app.contracts.requirement_imports import RequirementReviewsInput
from app.contracts.quality_feedback import QualityFindingInput
from app.contracts.knowledge import KnowledgeMutation
from app.contracts.billing import BillingAccount, BillingAllocation, BillingUserProfile
from app.agents.adk_runtime import _model_reservation
from app.agents.substantive_review import bind_reviewed_inputs, review_input, validate_assessment
from app.services import workflow_project_service as projects, project_sync as sync, test_case_suite_review as suite_review
from app.services import project_draft_admission as admission, approval_continuation
from app.services import project_sync_rebind as rebind, requirement_import_service as imports
from app.services.audit_service import record_usage_event
from app.services.billing_repository import build_allocation_id
from app.services.billing_service import BillingAccessContext, record_billing_consumption
from app.services.quality_feedback import capture_finding
from app.services.guidance_runtime import prepare_run
from app.services.guidance_service import current_guidance, guidance_scope, guidance_text, submit_with_guidance
from app.services.behaviour_maintenance import maintenance_scope
from app.services.knowledge_service import mutate_knowledge
from app.services.versioning_service import persist_test_case_versions
from app.services.use_case_review_service import review_use_case_snapshot


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

    def fixture(self, *, context_only=False, versioned=False):
        project = projects.create_project(name="Sync", description=None, actor=self.actor, request_id="create")
        self.pid = project.project_id
        requirements = [{"id": "R1", "text": "Limit is 8", "review_status": "Approved"}, {"id": "R2", "text": "Show receipt", "review_status": "Approved"}]
        self.preserved = {
            "id": "TC2",
            "title": "Receipt",
            "linked_requirement_ids": ["R2"],
            "steps": [{"step": 1, "action": "Submit", "expected": "Receipt appears"}],
        }
        if context_only or versioned:
            self.preserved.update(
                scenario_refs=["S2"],
                artifact_set_id="old-set",
                artifact_item_id="receipt-item",
                artifact_version_id="receipt-v1",
                artifact_version_number=1,
            )
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
                category="missing_data" if context_only else "ambiguous_requirement",
                summary="Booking fixture missing" if context_only else "Limit changed",
                expected_behavior="Use the confirmed booking fixture." if context_only else "Limit is 6",
                requirement_ids=["R1"],
            ),
            "finding",
        )
        self.payload = AcceptProjectFinding(
            base_project_revision=finding["project_revision"],
            finding_id=finding["finding"]["id"],
            decision="Use the confirmed booking fixture." if context_only else "Maximum is six",
            corrections=[] if context_only else [RequirementCorrection(requirement_id="R1", previous_text="Limit is 8", text="Limit is 6")],
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

    def enable_demo(self):
        from test_use_case_review_service import _run_transaction

        host = "127.0.0.1:44129"
        self.transaction_client.project = admission.DEMO_PROJECT_ID
        self.transaction_client._emulator_host = host
        self.enterContext(
            patch.dict(
                "os.environ",
                {"TCG_APPROVAL_SCENARIOS_ENABLED": "true", "FIREBASE_PROJECT_ID": admission.DEMO_PROJECT_ID, "FIRESTORE_EMULATOR_HOST": host},
            )
        )
        self.enterContext(patch.object(admission, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(admission, "get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(
            patch(
                "app.services.versioning_service.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.transaction_client.collection(name)
            )
        )
        self.enterContext(patch.object(sync, "persist_test_case_versions", wraps=persist_test_case_versions))
        self.enterContext(patch.object(sync, "record_usage_event", wraps=record_usage_event))
        self.enterContext(patch("app.services.billing_service.calculate_units_for_quantity", return_value=7))
        self.consume = self.enterContext(patch.object(sync, "record_billing_consumption", wraps=record_billing_consumption))
        account = BillingAccount(account_id="enterprise", scope_key="org:fixture", scope_type="organization", plan_tier="enterprise", balance_units=80)
        allocation = BillingAllocation(
            allocation_id=build_allocation_id(account.account_id, self.actor.sub),
            account_id=account.account_id,
            user_id=self.actor.sub,
            allocated_units=100,
            consumed_units=9,
        )
        self.account_path = f"billing_accounts/{account.account_id}"
        self.allocation_path = f"billing_allocations/{allocation.allocation_id}"
        self.store[self.account_path] = account.model_dump()
        self.store[self.allocation_path] = allocation.model_dump()
        self.billing = self.enterContext(
            patch.object(
                sync,
                "enforce_billing_access",
                return_value=BillingAccessContext(
                    account=account, profile=BillingUserProfile(user_id=self.actor.sub), pricing_entry=None, allocation=allocation, shadow_mode=False
                ),
            )
        )

    def demo_provider(self, adapter):
        def dispatch(payload, **kwargs):
            reserve = _model_reservation.get()
            self.assertIsNotNone(reserve)
            self.assertGreater(reserve(), 0)
            self.assertEqual(payload.workflow_settings.retry_attempts, 0)
            return adapter(payload, **kwargs)

        return dispatch

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
        self.enable_demo()
        root = self.transaction_client.collection("qa_projects").document(self.pid)
        approval_continuation.project_lease_ref(root).set(
            {"kind": "draft", "run_id": "other", "fence": 1, "expires_at": projects._utcnow() + timedelta(minutes=1), "dispatched": True}
        )
        with (
            patch.object(sync, "_generate_stage") as dispatch,
        ):
            accepted = sync.accept_finding(self.pid, self.actor, self.payload, "accept")
            before_replay = deepcopy(self.store)
            self.assertEqual(sync.accept_finding(self.pid, self.actor, self.payload, "accept"), accepted)
            with self.assertRaises(HTTPException) as deferred:
                sync.resume_sync(self.pid, self.actor, accepted.project_sync["id"], "resume")
            self.billing.assert_not_called()
            dispatch.assert_not_called()

        self.assertEqual(deferred.exception.status_code, 409)
        self.assertEqual(deferred.exception.detail["code"], "project_draft_busy")
        self.assertEqual(self.store, before_replay)
        self.assertEqual(accepted.current_revision, before.current_revision + 1)
        self.assertEqual(accepted.project_sync["status"], "pending")
        self.assertEqual(accepted.project_sync["attempts"], 0)
        self.assertEqual(accepted.current_snapshots["requirements"].payload["requirements"][0]["text"], "Limit is 6")
        self.assertEqual(accepted.current_snapshots["test_cases"], before.current_snapshots["test_cases"])
        self.assertTrue(accepted.stage_state["test_cases"].stale)

    def test_demo_resumes_earlier_pending_correction_and_reuses_unstarted_stage(self):
        self.fixture()
        self.enable_demo()
        self.billing.side_effect = [HTTPException(402, "Synthetic entitlement unavailable"), self.billing.return_value, self.billing.return_value]
        with patch.object(sync, "approval_scenarios_enabled", return_value=False):
            accepted = sync.accept_finding(self.pid, self.actor, self.payload, "prior-accept")
        change_id = accepted.project_sync["id"]
        root = f"qa_projects/{self.pid}"
        self.store[root]["project_sync"].pop("draft_admission_version")
        self.store[f"{root}/project_changes/{change_id}"].pop("draft_admission_version")
        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(self.planner)) as planner,
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(self.generator)) as generator,
        ):
            with self.assertRaises(HTTPException) as blocked:
                sync.resume_sync(self.pid, self.actor, change_id, "unstarted-attempt")
            self.assertEqual(blocked.exception.status_code, 402)
            enrolled = projects.get_project(self.pid, actor=self.actor)
            self.assertEqual(enrolled.current_revision, accepted.current_revision)
            self.assertEqual(enrolled.project_sync["draft_admission_version"], 1)
            planner.assert_not_called()
            completed = sync.resume_sync(self.pid, self.actor, change_id, "resume-demo")
        self.assertEqual(planner.call_args.kwargs["request_id"], "unstarted-attempt")
        self.assertEqual(generator.call_args.kwargs["request_id"], "resume-demo")
        self.assertEqual(completed.project_sync["status"], "ready_for_review")
        self.assertEqual(completed.current_revision, accepted.current_revision + 2)

    def test_demo_stages_publish_and_recover_lost_suite_reply_with_one_charge(self):
        before = self.fixture()
        self.enable_demo()
        suite = before.current_snapshots["test_cases"]
        self.preserved.update(artifact_set_id="old-set", artifact_item_id="receipt-item", artifact_version_id="receipt-v1", artifact_version_number=1)
        self.store[f"qa_projects/{self.pid}/snapshots/{suite.snapshot_id}"]["payload"]["test_cases"][1] = self.preserved
        report = projects.append_stage_snapshot(
            project_id=self.pid,
            actor=self.actor,
            request_id="previous-pom",
            operation="automation.playwright.generate",
            stage="reports",
            payload={"artifact_kind": "generated_code", "files": ["old.py"]},
        )
        self.payload.base_project_revision = report.project_revision
        original_commit = projects._commit_project_revision

        def lose_suite_reply(*args, **kwargs):
            committed = original_commit(*args, **kwargs)
            if (kwargs.get("snapshot") or {}).get("stage") == "test_cases":
                raise RuntimeError("Synthetic commit acknowledgement lost")
            return committed

        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(self.planner)) as planner,
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(self.generator)) as generator,
            patch.object(sync, "generate_playwright_pom") as automation,
            patch.object(projects, "_commit_project_revision", side_effect=lose_suite_reply),
        ):
            result = sync.accept_finding(self.pid, self.actor, self.payload, "accept-demo")
            self.assertEqual(sync.accept_finding(self.pid, self.actor, self.payload, "accept-demo"), result)
            self.assertEqual(sync.resume_sync(self.pid, self.actor, result.project_sync["id"], "resume-demo"), result)
        planner.assert_called_once()
        generator.assert_called_once()
        automation.assert_not_called()
        self.consume.assert_called_once()
        self.assertIsNotNone(self.consume.call_args.kwargs["transaction"])
        self.assertEqual(result.current_revision, report.project_revision + 3)
        self.assertEqual(result.project_sync["status"], "ready_for_review")
        self.assertEqual(result.project_sync["stages"]["automation"], "deferred")
        self.assertEqual(result.current_snapshots["reports"], report)
        self.assertEqual(result.execution_runs, [])
        plan = result.current_snapshots["use_cases"]
        self.assertEqual(plan.payload["coverage_plan"][0], before.current_snapshots["use_cases"].payload["coverage_plan"][0])
        latest = result.current_snapshots["test_cases"]
        self.assertFalse(latest.approved)
        self.assertEqual(latest.source_snapshot_id, plan.snapshot_id)
        self.assertEqual(latest.metadata["source_snapshot_ids"]["test_cases"], suite.snapshot_id)
        self.assertEqual(latest.metadata["source_snapshot_ids"]["reports"], report.snapshot_id)
        self.assertEqual(result.project_sync["stage_receipts"]["test_cases"]["base_revision"], plan.project_revision)
        self.assertEqual(latest.payload["test_cases"][0], self.preserved)
        generated = latest.payload["test_cases"][1]
        version = self.store[f"test_case_sets/{generated['artifact_set_id']}/items/{generated['artifact_item_id']}/versions/{generated['artifact_version_id']}"]
        self.assertEqual(version["source_event_id"], latest.source_event_id)
        self.assertEqual(self.store[self.account_path]["balance_units"], 73)
        self.assertEqual(self.store[self.allocation_path]["consumed_units"], 16)
        for collection in ("usage_events", "billing_consumption", "billing_wallet_ledger"):
            self.assertEqual(sum(path.startswith(f"{collection}/") for path in self.store), 1)

    def prepare_context_only_review(self):
        from test_use_case_review_service import _run_transaction

        before = self.fixture(context_only=True)
        self.enable_demo()
        self.enterContext(patch("app.services.use_case_review_service.get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(patch("app.services.use_case_review_service.transactional", side_effect=_run_transaction))

        def plan(payload, **_kwargs):
            self.assertEqual(payload.requirements[0].text, "Limit is 8")
            self.assertIn(self.payload.decision, guidance_text())
            return {
                "requirement_analysis": [{"requirement_id": "R1", "requirement_text": "Limit is 8"}],
                "coverage_plan": [
                    {
                        "requirement_id": "R1",
                        "requirement_text": "Limit is 8",
                        "scenarios": [{"id": "S1", "requirement_id": "R1", "title": "Limit", "objective": "Check the maximum of eight"}],
                    }
                ],
            }

        def generate(payload, **_kwargs):
            self.assertEqual(payload.coverage_plan[0].requirement_id, "R1")
            self.assertIn(self.payload.decision, guidance_text())
            return {
                "test_cases": [
                    {
                        "id": "new",
                        "title": "Accept the maximum of eight",
                        "linked_requirement_ids": ["R1"],
                        "scenario_refs": ["S1"],
                        "steps": [{"step": 1, "action": "Submit the booking with eight attendees", "expected": "A booking for eight attendees appears."}],
                    }
                ],
                "workflow_diagnostics": {"status": "completed"},
            }

        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(plan)),
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(generate)),
        ):
            drafted = sync.accept_finding(self.pid, self.actor, self.payload, "context-only")
        self.assertEqual(drafted.project_sync["status"], "ready_for_review")
        self.assertEqual(drafted.current_snapshots["requirements"].snapshot_id, before.current_snapshots["requirements"].snapshot_id)
        self.assertFalse(drafted.stage_state["context"].approved)
        draft = drafted.current_snapshots["test_cases"]
        self.assertFalse(draft.metadata.get("review_retry_available"))
        self.assertIsNone(draft.payload["test_cases"][-1]["steps"][0]["test_data"])
        review_use_case_snapshot(
            project_id=self.pid,
            snapshot_id=drafted.current_snapshots["use_cases"].snapshot_id,
            base_project_revision=drafted.current_revision,
            decision="approve",
            comment=None,
            actor=self.actor,
            request_id="explicit-context-plan-approval",
        )
        approved = projects.get_project(self.pid, actor=self.actor)
        self.assertEqual(approved.project_sync, drafted.project_sync)
        self.assertTrue(approved.current_snapshots["test_cases"].metadata.get("review_retry_available"))
        return approved, draft

    def test_context_only_sync_review_binds_decision_and_preserves_raw_nulls_after_lost_ack(self):
        approved, draft = self.prepare_context_only_review()
        original = projects.get_project_stage_snapshot(self.pid, draft.snapshot_id, actor=self.actor)
        publish = suite_review.append_stage_snapshot

        def assess(cases, requirements, context, **_kwargs):
            self.assertEqual([row.id for row in requirements], ["R1", "R2"])
            self.assertIn(self.payload.decision, guidance_text())
            self.assertTrue(current_guidance().memory_bypassed)
            self.assertFalse(current_guidance().skills_enabled)
            self.assertGreater(_model_reservation.get()(), 0)
            data, input_hash, _ = review_input(cases, requirements, context)
            raw = {
                "input_hash": input_hash,
                "obligations": [
                    {
                        "requirement_id": row.id,
                        "source_field": "text",
                        "obligation": row.text,
                        "status": "covered",
                        "reason": "Verified exact saved step.",
                        "evidence": [{"test_case_id": next(case["id"] for case in cases if row.id in case["linked_requirement_ids"]), "step": 1}],
                    }
                    for row in requirements
                ],
                "case_grounding": [{"test_case_id": case["id"], "reason": "Grounded saved case", "prerequisites": []} for case in cases],
            }
            return bind_reviewed_inputs(validate_assessment(json.dumps(raw), data, input_hash), data)

        def lose_ack(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic saved-review acknowledgement lost")

        args = {
            "project_id": self.pid,
            "snapshot_id": draft.snapshot_id,
            "base_project_revision": approved.current_revision,
            "actor": self.actor,
            "request_id": "review-exact-sync-draft",
        }
        with (
            patch.object(suite_review, "_heuristic_test_case_review", return_value={"approved": True, "score": 100, "threshold": 90}),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", side_effect=assess) as model,
            patch.object(suite_review, "start_workflow_run", return_value="review-sync"),
            patch.object(suite_review, "complete_workflow_run") as audit,
            patch.object(suite_review, "append_stage_snapshot", side_effect=lose_ack),
        ):
            reviewed = suite_review.review_saved_test_cases(**args)
            self.assertEqual(suite_review.review_saved_test_cases(**args), reviewed)
        model.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["status"], "completed")
        successor = reviewed.current_snapshots["test_cases"]
        self.assertEqual(successor.source_snapshot_id, draft.snapshot_id)
        self.assertEqual(successor.payload["test_cases"], original.payload["test_cases"])
        self.assertEqual(projects.get_project_stage_snapshot(self.pid, draft.snapshot_id, actor=self.actor), original)
        self.assertFalse(successor.approved)
        self.assertFalse(successor.payload["review"]["approved"])
        self.assertEqual(reviewed.project_sync, approved.project_sync)
        self.assertEqual(reviewed.suite_quality.design_status, "complete")
        self.assertEqual(reviewed.suite_quality.human_acceptance, "unavailable")
        with patch.dict("os.environ", {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
            historical = projects.get_project(self.pid, actor=self.actor)
        self.assertEqual(historical.suite_quality, reviewed.suite_quality)
        self.consume.assert_called_once()

    def test_sync_review_rejects_missing_private_receipt_wrong_version_and_forged_hint_without_provider(self):
        approved, draft = self.prepare_context_only_review()
        project_path = f"qa_projects/{self.pid}"
        draft_path = f"{project_path}/snapshots/{draft.snapshot_id}"
        change_path = f"{project_path}/project_changes/{approved.project_sync['id']}"
        pointer_path = f"{change_path}/draft_stages/test_cases"
        review_id = approved.stage_state["use_cases"].metadata["latest_human_review"]["review_id"]
        approval_path = f"{project_path}/use_case_reviews/{review_id}"
        case = draft.payload["test_cases"][-1]
        version_path = f"test_case_sets/{case['artifact_set_id']}/items/{case['artifact_item_id']}/versions/{case['artifact_version_id']}"
        intact = deepcopy(self.store)
        failures = [
            (pointer_path, None),
            (approval_path, None),
            (version_path, {"version_number": 999}),
            (draft_path, {"actor_user_id": "unrelated"}),
            (project_path, {"project_decisions": []}),
        ]
        with patch.object(suite_review, "assess_delivered_suite") as provider:
            for path, edit in failures:
                with self.subTest(path=path):
                    self.store.clear()
                    self.store.update(deepcopy(intact))
                    if edit is None:
                        self.store.pop(path)
                    else:
                        self.store[path].update(edit)
                    self.store[draft_path]["metadata"]["review_retry_available"] = True
                    project = projects.get_project(self.pid, actor=self.actor)
                    self.assertNotIn("review_retry_available", project.current_snapshots["test_cases"].metadata)
                    with self.assertRaises(HTTPException):
                        suite_review.review_saved_test_cases(
                            project_id=self.pid,
                            snapshot_id=draft.snapshot_id,
                            base_project_revision=approved.current_revision,
                            actor=self.actor,
                            request_id="blocked-sync-review",
                        )
        provider.assert_not_called()

    def test_material_sync_draft_remains_outside_saved_review_after_scenario_approval(self):
        from test_use_case_review_service import _run_transaction

        self.fixture()
        self.enable_demo()
        self.enterContext(patch("app.services.use_case_review_service.get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(patch("app.services.use_case_review_service.transactional", side_effect=_run_transaction))
        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(self.planner)),
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(self.generator)),
        ):
            project = sync.accept_finding(self.pid, self.actor, self.payload, "material")
        self.assertEqual(project.project_sync["stages"]["requirements"], "draft")
        draft = project.current_snapshots["test_cases"]
        review_use_case_snapshot(
            project_id=self.pid,
            snapshot_id=project.current_snapshots["use_cases"].snapshot_id,
            base_project_revision=project.current_revision,
            decision="approve",
            comment=None,
            actor=self.actor,
            request_id="material-plan-approval",
        )
        latest = projects.get_project(self.pid, actor=self.actor)
        self.assertNotIn("review_retry_available", latest.current_snapshots["test_cases"].metadata)
        with patch.object(suite_review, "assess_delivered_suite") as provider, self.assertRaises(HTTPException):
            suite_review.review_saved_test_cases(
                project_id=self.pid,
                snapshot_id=draft.snapshot_id,
                base_project_revision=latest.current_revision,
                actor=self.actor,
                request_id="material-review",
            )
        provider.assert_not_called()

    def prepare_material_rebind(self, *, continuation=None):
        from test_use_case_review_service import _run_transaction

        self.fixture(versioned=True)
        self.enable_demo()
        self.enterContext(patch.object(rebind, "get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(patch.object(imports, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(imports, "get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(patch("app.services.use_case_review_service.get_required_firestore_client", return_value=self.transaction_client))
        self.enterContext(patch("app.services.use_case_review_service.transactional", side_effect=_run_transaction))

        def generate(payload, **kwargs):
            return {**self.generator(payload, **kwargs), "workflow_diagnostics": {"status": "completed"}}

        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(self.planner)),
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(generate)),
        ):
            drafted = sync.accept_finding(self.pid, self.actor, self.payload, "material-for-rebind")
        change_id = drafted.project_sync["id"]
        self.assertEqual(drafted.project_sync["status"], "ready_for_review")
        self.assertTrue(drafted.material_requirements_review_required)
        self.assertEqual(rebind.sync_rebind_status(self.transaction_client, self.pid, self.actor).state, "needs_requirement_approval")
        with self.assertRaises(HTTPException):
            rebind.rebind_sync_drafts(
                self.pid, self.actor, ProjectSyncRebindInput(change_id=change_id, base_project_revision=drafted.current_revision), "too-early"
            )
        requirements = drafted.current_snapshots["requirements"]
        row = next(row for row in requirements.payload["requirements"] if row["id"] == "R1")
        review = RequirementReviewsInput(
            base_project_revision=drafted.current_revision,
            idempotency_key="approve-corrected-material",
            reviews=[{"requirement_uid": row["requirement_uid"], "review_status": "Approved", "quality_flags": ["reviewed"]}],
        )
        approved = imports.RequirementImportRepository().review(self.pid, self.actor, review, "ordinary-corrected-material", continuation=continuation)
        self.assertEqual(approved.current_snapshots["requirements"].source_snapshot_id, requirements.snapshot_id)
        self.assertEqual(approved.current_snapshots["context"], drafted.current_snapshots["context"])
        self.assertTrue(approved.material_scope_pending_rebind)
        self.assertFalse(approved.material_requirements_review_required)
        self.assertTrue(approved.stage_state["use_cases"].stale)
        self.assertTrue(approved.stage_state["test_cases"].stale)
        self.assertEqual(rebind.sync_rebind_status(self.transaction_client, self.pid, self.actor).state, "available")
        return drafted, approved, ProjectSyncRebindInput(change_id=change_id, base_project_revision=approved.current_revision)

    def test_material_rebind_preserves_raw_drafts_replays_lost_ack_and_requires_fresh_scenario_approval(self):
        from test_use_case_review_service import FakeTransaction

        drafted, approved, payload = self.prepare_material_rebind()
        plan = drafted.current_snapshots["use_cases"]
        suite = projects.get_project_stage_snapshot(self.pid, drafted.current_snapshots["test_cases"].snapshot_id, actor=self.actor)
        before = deepcopy(self.store)
        original_commit = FakeTransaction.commit

        def lose_ack(transaction):
            rebind_writes = any("/sync_rebind_receipts/" in ref.path for _, ref, _ in transaction.pending)
            original_commit(transaction)
            if rebind_writes:
                raise RuntimeError("Synthetic rebind commit acknowledgement lost")

        with patch.object(FakeTransaction, "commit", new=lose_ack):
            receipt = rebind.rebind_sync_drafts(self.pid, self.actor, payload, "exact-material-rebind")
        self.assertEqual(receipt.project_revision, approved.current_revision + 1)
        saved = projects.get_project(self.pid, actor=self.actor)
        self.assertEqual(saved.project_sync["status"], "ready_for_review")
        self.assertEqual(saved.project_sync["review_rebind"], receipt.model_dump(mode="json"))
        self.assertEqual(saved.project_sync["stage_receipts"], drafted.project_sync["stage_receipts"])
        self.assertEqual(saved.current_snapshots["use_cases"].payload, plan.payload)
        rebound = projects.get_project_stage_snapshot(self.pid, receipt.test_cases_snapshot_id, actor=self.actor)
        self.assertEqual(rebound.payload, suite.payload)
        self.assertEqual(rebound.source_snapshot_id, receipt.use_cases_snapshot_id)
        self.assertFalse(rebound.approved)
        self.assertEqual(self.consume.call_count, 1)
        self.assertEqual(
            {path: value for path, value in self.store.items() if path.startswith("test_case_sets/")},
            {path: value for path, value in before.items() if path.startswith("test_case_sets/")},
        )
        self.assertFalse(saved.current_snapshots["test_cases"].metadata.get("review_retry_available"))
        with self.assertRaises(HTTPException) as named:
            review_use_case_snapshot(
                project_id=self.pid,
                snapshot_id=receipt.use_cases_snapshot_id,
                base_project_revision=saved.current_revision,
                decision="approve",
                comment=None,
                actor=self.actor,
                request_id="rebound-named-approval",
                continuation=approval_continuation.ContinuationOptions(settings_fingerprint="a" * 64),
                require_prepared_journey=True,
            )
        self.assertEqual(named.exception.detail["code"], "sync_rebind_ordinary_approval_required")
        review_use_case_snapshot(
            project_id=self.pid,
            snapshot_id=receipt.use_cases_snapshot_id,
            base_project_revision=saved.current_revision,
            decision="approve",
            comment=None,
            actor=self.actor,
            request_id="rebound-ordinary-approval",
        )
        latest = projects.get_project(self.pid, actor=self.actor)
        self.assertTrue(latest.current_snapshots["test_cases"].metadata["review_retry_available"])
        self.assertEqual(rebind.sync_rebind_status(self.transaction_client, self.pid, self.actor).state, "bound")
        self.assertEqual(rebind.rebind_sync_drafts(self.pid, self.actor, payload, "exact-material-rebind"), receipt)
        with self.assertRaises(HTTPException) as different:
            rebind.rebind_sync_drafts(
                self.pid, self.actor, payload.model_copy(update={"base_project_revision": payload.base_project_revision + 1}), "exact-material-rebind"
            )
        self.assertEqual(different.exception.detail["code"], "sync_rebind_request_mismatch")
        self.assertEqual(sum("/sync_rebind_receipts/" in path for path in self.store), 1)
        with patch.dict("os.environ", {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
            self.assertEqual(rebind.read_sync_rebind_receipt(self.pid, self.actor, "exact-material-rebind"), receipt)
        self.assertEqual(projects.get_project_stage_snapshot(self.pid, suite.snapshot_id, actor=self.actor), suite)

    def test_material_rebind_rejects_changed_facts_competing_lease_and_failed_transaction(self):
        _, approved, payload = self.prepare_material_rebind()
        header = f"qa_projects/{self.pid}"
        snapshot_path = f"{header}/snapshots/{approved.current_snapshots['requirements'].snapshot_id}"
        intact = deepcopy(self.store)
        edits = {
            "text": lambda rows: rows[0].update(text="Limit is seven."),
            "citation": lambda rows: rows[0]["sources"][0].update(excerpt="A different rule."),
            "order": lambda rows: rows.reverse(),
            "disposition": lambda rows: rows[0].update(clarification_status="deferred"),
            "version": lambda rows: rows[0].update(content_version=99),
            "identity": lambda rows: rows[0].update(requirement_uid="foreign"),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                self.store.clear()
                self.store.update(deepcopy(intact))
                edit(self.store[snapshot_path]["payload"]["requirements"])
                before = deepcopy(self.store)
                self.assertEqual(rebind.sync_rebind_status(self.transaction_client, self.pid, self.actor).state, "unavailable")
                with self.assertRaises(HTTPException):
                    rebind.rebind_sync_drafts(self.pid, self.actor, payload, f"changed-{name}")
                self.assertEqual(self.store, before)
        for path in (
            f"{header}/project_changes/{payload.change_id}/draft_stages/test_cases",
            f"{header}/project_changes/{payload.change_id}/draft_stages/use_cases",
        ):
            self.store.clear()
            self.store.update(deepcopy(intact))
            self.store.pop(path)
            self.assertEqual(rebind.sync_rebind_status(self.transaction_client, self.pid, self.actor).state, "unavailable")
            with self.assertRaises(HTTPException):
                rebind.rebind_sync_drafts(self.pid, self.actor, payload, "missing-draft")
        self.store.clear()
        self.store.update(deepcopy(intact))
        lease = approval_continuation.project_lease_ref(self.transaction_client.collection("qa_projects").document(self.pid))
        lease.set({"kind": "draft", "run_id": "competing", "fence": 3, "dispatched": True, "expires_at": projects._utcnow() + timedelta(seconds=60)})
        with self.assertRaises(HTTPException) as busy:
            rebind.rebind_sync_drafts(self.pid, self.actor, payload, "competing-provider")
        self.assertEqual(busy.exception.detail["code"], "project_draft_busy")
        self.store.clear()
        self.store.update(deepcopy(intact))
        self.transaction_client.fail_on_create_segment = "/sync_rebind_receipts/"
        with self.assertRaises(RuntimeError):
            rebind.rebind_sync_drafts(self.pid, self.actor, payload, "rollback-material")
        self.assertEqual(self.store, intact)

    def test_named_material_requirement_approval_saves_the_decision_without_outbox(self):
        _, approved, _ = self.prepare_material_rebind(continuation=approval_continuation.ContinuationOptions(settings_fingerprint="a" * 64))
        receipt = self.store[f"qa_projects/{self.pid}/requirement_import_receipts/{imports.digest('approve-corrected-material')}"]
        self.assertEqual(receipt["continuation_blocked_reason"], "sync_rebind_required")
        self.assertTrue(approved.stage_state["requirements"].approved)
        self.assertFalse(any(path.startswith(f"{approval_continuation.OUTBOX_COLLECTION}/") for path in self.store))

    def test_demo_billing_rollback_preserves_plan_and_retry_uses_new_identity(self):
        before = self.fixture()
        self.enable_demo()

        def fail_after_staging_debit(**kwargs):
            record_billing_consumption(**kwargs)
            raise RuntimeError("Synthetic debit rollback")

        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(self.planner)) as planner,
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(self.generator)) as generator,
        ):
            with patch.object(sync, "record_billing_consumption", side_effect=fail_after_staging_debit):
                partial = sync.accept_finding(self.pid, self.actor, self.payload, "accept-demo")
            self.assertEqual(partial.project_sync["status"], "partial")
            self.assertEqual(partial.current_snapshots["test_cases"], before.current_snapshots["test_cases"])
            self.assertEqual(self.store[self.account_path]["balance_units"], 80)
            self.assertEqual(self.store[self.allocation_path]["consumed_units"], 9)
            self.assertFalse(
                any(path.startswith(("usage_events/", "billing_consumption/", "billing_wallet_ledger/", "test_case_sets/")) for path in self.store)
            )
            self.assertEqual(sync.accept_finding(self.pid, self.actor, self.payload, "accept-demo"), partial)
            held = admission.read_project_sync_admission(self.transaction_client, self.pid, self.actor.sub)["attempt"]
            self.assertEqual((held["state"], held["stage"]), ("interrupted", "test_cases"))
            with patch.dict("os.environ", {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
                self.assertEqual(sync.accept_finding(self.pid, self.actor, self.payload, "accept-demo"), partial)
                with self.assertRaises(HTTPException) as rolled_back:
                    sync.resume_sync(self.pid, self.actor, partial.project_sync["id"], "flag-off")
                self.assertEqual(rolled_back.exception.detail["code"], "project_draft_refresh_deferred")
            generator.assert_called_once()
            with self.assertRaises(HTTPException) as blocked:
                sync.resume_sync(self.pid, self.actor, partial.project_sync["id"], "retry-demo")
            self.assertEqual(blocked.exception.detail["code"], "project_draft_recovery_required")
            admission.release_draft_attempt(self.transaction_client, self.pid, self.actor.sub, held["operation_id"], held["fence"], project_sync=True)
            with self.assertRaises(HTTPException) as old:
                sync.resume_sync(self.pid, self.actor, partial.project_sync["id"], "accept-demo")
            self.assertEqual(old.exception.detail["code"], "project_draft_recovery_required")
            completed = sync.resume_sync(self.pid, self.actor, partial.project_sync["id"], "retry-after-release")
        planner.assert_called_once()
        self.assertEqual(generator.call_count, 2)
        self.assertEqual(completed.current_snapshots["use_cases"], partial.current_snapshots["use_cases"])
        self.assertEqual(completed.current_snapshots["test_cases"].request_id, "retry-after-release")
        self.assertEqual(self.store[self.account_path]["balance_units"], 73)
        self.assertEqual(sum(path.startswith("billing_consumption/") for path in self.store), 1)

    def test_demo_source_change_fences_late_suite_reply_without_debit(self):
        before = self.fixture()
        self.enable_demo()

        def replace_context(payload, **kwargs):
            projects.append_stage_snapshot(
                project_id=self.pid,
                actor=self.actor,
                request_id="new-context",
                operation="context.enrich",
                stage="context",
                payload={"notes": "Context changed after provider dispatch"},
            )
            return self.generator(payload, **kwargs)

        with (
            patch.object(sync, "generate_use_cases", side_effect=self.demo_provider(self.planner)) as planner,
            patch.object(sync, "generate_test_cases", side_effect=self.demo_provider(replace_context)) as generator,
        ):
            superseded = sync.accept_finding(self.pid, self.actor, self.payload, "accept-demo")
            with self.assertRaises(HTTPException) as stale:
                sync.resume_sync(self.pid, self.actor, superseded.project_sync["id"], "retry-demo")
            self.assertEqual(stale.exception.status_code, 409)
        planner.assert_called_once()
        generator.assert_called_once()
        self.consume.assert_not_called()
        self.assertEqual(superseded.project_sync["status"], "superseded")
        self.assertEqual(superseded.current_snapshots["test_cases"], before.current_snapshots["test_cases"])
        self.assertEqual(self.store[self.account_path]["balance_units"], 80)
        self.assertFalse(any(path.startswith(("usage_events/", "billing_consumption/", "billing_wallet_ledger/", "test_case_sets/")) for path in self.store))

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
