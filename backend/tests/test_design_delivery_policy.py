from copy import deepcopy
from os import environ
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app.contracts.exports import ExportTestCasesInput
from app.contracts.projects import QaProjectExecutionRun
from app.services.export_evidence import build_export_evidence, design_delivery_enabled
from app.services.suite_quality import build_suite_design_proof, resolve_suite_quality
from test_suite_quality import NOW, attach_suite, fixture


class DesignDeliveryPolicyTests(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}))

    def suite(self, *, deprecated=False):
        project, payload, metadata = fixture(missing_setup=True)
        if deprecated:
            old_case = {
                **deepcopy(payload["test_cases"][0]),
                "id": "TC-OLD",
                "status": "Deprecated",
                "linked_requirement_ids": ["REQ-2", "REQ-HISTORICAL"],
                "scenario_refs": ["SCN-OLD"],
                "artifact_item_id": "old-item",
                "artifact_version_id": "old-version",
            }
            payload["test_cases"].append(old_case)
        payload["review"] = {"approved": True, "score": 95, "threshold": 90}
        proof = build_suite_design_proof(project, payload, metadata, plan_snapshot_id="plan-v1")
        suite = attach_suite(project, payload, metadata, proof=proof)
        # Deliberately permissive legacy booleans cannot shrink the legacy
        # denominator or confer human suite acceptance.
        project.stage_state["test_cases"].approved = True
        suite.approved = True
        project.suite_quality = resolve_suite_quality(project, raw_suite=suite)
        return project, suite

    def export_input(self, project, suite, **values):
        return ExportTestCasesInput.model_validate(
            {
                "project_id": project.project_id,
                "base_project_revision": project.current_revision,
                "source_snapshot_id": suite.snapshot_id,
                "delivery_intent": "reviewed_design",
                "test_cases": suite.payload["test_cases"],
                **values,
            }
        )

    def rejection(self, request, project, status, code=None):
        with self.assertRaises(HTTPException) as failure:
            build_export_evidence(request, project)
        self.assertEqual(failure.exception.status_code, status)
        if code:
            self.assertEqual(failure.exception.detail["code"], code)

    def test_reviewed_design_preserves_scope_and_independent_evidence(self):
        project, suite = self.suite(deprecated=True)
        project.execution_runs = [
            QaProjectExecutionRun(
                run_record_id="run-record-1",
                project_id=project.project_id,
                run_id="actual-exact-run",
                target_environment="synthetic",
                project_revision=project.current_revision,
                test_case_count=1,
                status="failed",
                source_snapshot_id=suite.snapshot_id,
                snapshot_id="execution-v1",
                selected_test_case_ids=["TC-1"],
                created_at=NOW,
            ),
            QaProjectExecutionRun(
                run_record_id="run-record-2",
                project_id=project.project_id,
                run_id="unrelated-run",
                target_environment="synthetic",
                project_revision=project.current_revision,
                test_case_count=1,
                status="passed",
                source_snapshot_id="another-suite",
                selected_test_case_ids=["TC-1"],
                created_at=NOW,
            ),
        ]
        request = self.export_input(project, suite)
        audit = build_export_evidence(request, project)
        self.assertFalse(audit.is_draft)
        self.assertFalse(audit.draft_override_used)
        self.assertIsNone(audit.draft_override_reason)
        self.assertTrue(audit.suite_complete)
        self.assertEqual(audit.delivered_case_count, 2)
        self.assertEqual(audit.design_delivery.design_status, "complete")
        self.assertEqual(audit.design_delivery.declared_setup, "blocked")
        self.assertEqual(audit.design_delivery.target_readiness, "unknown")
        self.assertFalse(audit.design_delivery.legacy_approved)
        self.assertEqual(audit.design_delivery.human_acceptance, "unavailable")
        self.assertEqual(audit.design_delivery.scenario_review_id, "human-review")
        self.assertEqual(audit.design_delivery.required_requirement_ids, ["REQ-1"])
        self.assertEqual(audit.design_delivery.deferred_requirement_ids, ["REQ-2"])
        self.assertEqual(audit.design_delivery.excluded_requirement_ids, ["REQ-3"])
        self.assertEqual(audit.design_delivery.active_case_count, 1)
        self.assertEqual(audit.design_delivery.deprecated_case_count, 1)
        self.assertEqual(audit.coverage["requirements"]["referenced_ids"], ["REQ-1"])
        self.assertEqual(audit.coverage["requirements"]["total"], 1)
        self.assertEqual(audit.coverage["scenarios"]["referenced_ids"], ["SCN-1"])
        self.assertEqual(audit.execution_run_ids, ["actual-exact-run"])
        self.assertEqual(audit.execution_status, "failed")
        self.assertEqual(
            audit.source_snapshot_ids, {"requirements": "requirements-v1", "use_cases": "plan-v1", "test_cases": suite.snapshot_id, "execution": "execution-v1"}
        )
        self.assertEqual([version["artifact_version_id"] for version in audit.case_versions], ["version-1", "old-version"])
        self.assertEqual(request.test_cases[0].steps[0].test_data, "  β-01  ")

    def test_disabled_intent_allows_only_a_verified_saved_replay_and_legacy_draft(self):
        project, suite = self.suite()
        request = self.export_input(project, suite)
        self.assertTrue(design_delivery_enabled())
        with patch.dict(environ, {"TCG_DESIGN_DELIVERY_ENABLED": "false"}):
            self.assertFalse(design_delivery_enabled())
            self.rejection(request, project, 409, "design_delivery_disabled")
            replay = build_export_evidence(request, project, allow_saved_design=True)
            self.assertFalse(replay.is_draft)
            self.assertEqual(replay.design_delivery.delivery_kind, "reviewed_design")
            legacy = self.export_input(project, suite, delivery_intent=None, draft_override_requested=True, draft_override_reason="Legacy review pending")
            draft = build_export_evidence(legacy, project)
            self.assertIsNone(draft.design_delivery)
            self.assertTrue(draft.is_draft)
            project.stage_state["test_cases"].stale = True
            with self.assertRaises(HTTPException) as stale:
                build_export_evidence(request, project, allow_saved_design=True)
            self.assertEqual(stale.exception.status_code, 409)

    def test_requires_exact_current_project_revision_and_case_content(self):
        project, suite = self.suite()
        for changes in ({"project_id": None}, {"source_snapshot_id": None}, {"base_project_revision": None}):
            with self.subTest(changes=changes):
                self.rejection(self.export_input(project, suite, **changes), project, 422, "design_delivery_requires_saved_suite")
        for changes in ({"source_snapshot_id": "wrong-suite"}, {"base_project_revision": project.current_revision - 1}):
            with self.subTest(changes=changes):
                self.rejection(self.export_input(project, suite, **changes), project, 409)
        altered = deepcopy(suite.payload["test_cases"])
        altered[0]["steps"][0]["test_data"] = altered[0]["steps"][0]["test_data"].strip()
        self.rejection(self.export_input(project, suite, test_cases=altered), project, 409)

    def test_explicit_draft_override_conflicts_with_reviewed_design(self):
        project, suite = self.suite()
        for changes in ({"draft_override_requested": True}, {"draft_override_reason": "Draft only"}):
            with self.subTest(changes=changes):
                self.rejection(self.export_input(project, suite, **changes), project, 422, "design_delivery_conflicting_intent")

    def test_forged_or_stale_design_cannot_admit_despite_client_or_legacy_approval(self):
        project, suite = self.suite()
        original_quality = project.suite_quality
        request = self.export_input(project, suite, approved=True, review={"approved": True})
        for updates in (
            {"design_status": "unknown"},
            {"source_freshness": "stale"},
            {"scenario_decision": "unavailable"},
            {"suite_snapshot_id": "different-suite"},
            {"scenario_snapshot_id": "different-scenario"},
            {"scenario_review_id": None},
            {"human_acceptance": "changes_requested"},
        ):
            with self.subTest(updates=updates):
                project.suite_quality = original_quality.model_copy(update=updates)
                self.rejection(request, project, 409, "design_delivery_unavailable")
        project.suite_quality = None
        self.rejection(request, project, 409, "design_delivery_unavailable")
        project.suite_quality = original_quality
        project.stage_state["test_cases"].metadata["latest_human_review"] = {"decision": "request_changes", "snapshot_id": suite.snapshot_id}
        self.rejection(request, project, 409, "design_delivery_unavailable")

    def test_outstanding_tasks_and_raw_placeholders_block_reviewed_design(self):
        project, suite = self.suite(deprecated=True)
        suite.payload["generation_tasks"] = [{"reason": "Generate missing case"}]
        self.rejection(self.export_input(project, suite), project, 409, "design_delivery_unavailable")
        suite.payload["generation_tasks"] = []
        suite.payload["test_cases"][1]["steps"][0]["action"] = "Observe system behavior and capture the outcome"
        self.rejection(self.export_input(project, suite), project, 422)
        legacy = self.export_input(project, suite, delivery_intent=None, draft_override_requested=True, draft_override_reason="Preserve old artifact")
        self.assertTrue(build_export_evidence(legacy, project).is_draft)

    def test_deprecated_rows_cannot_satisfy_active_scope(self):
        project, suite = self.suite(deprecated=True)
        active, old = suite.payload["test_cases"]
        old["linked_requirement_ids"] = active["linked_requirement_ids"]
        old["scenario_refs"] = active["scenario_refs"]
        active["scenario_refs"] = ["SCN-OTHER"]
        self.rejection(self.export_input(project, suite), project, 409)
