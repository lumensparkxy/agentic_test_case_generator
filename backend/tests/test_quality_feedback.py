from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from pydantic import ValidationError
import test_workflow_project_service as project_tests
from app.contracts.quality_feedback import QualityFindingInput, QualityFindingAction, ExecutionPrerequisite
from app.contracts.test_cases import TestCase
from app.services import workflow_project_service as projects
from app.services.quality_feedback import capture_finding, update_finding, list_findings
from app.services.execution_prerequisites import prerequisite_blockers, execution_reference_scope


class QualityFeedbackTests(unittest.TestCase):
    setUp = project_tests.WorkflowProjectServiceTests.setUp
    tearDown = project_tests.WorkflowProjectServiceTests.tearDown

    def fixture(self, case_payload=None):
        project = projects.create_project(name="Quality", description=None, actor=self.actor, request_id="create")
        self.project_id = project.project_id
        self.case = {
            "id": "TC-1",
            "title": "Supervisor approval",
            "steps": [{"step": 1, "action": "Approve expense", "expected": "Expense is approved"}],
            "expected_result": "Expense is approved",
            "linked_requirement_ids": ["REQ-1"],
        }
        projects.append_stage_snapshot(
            project_id=self.project_id,
            stage="requirements",
            payload={"requirements": [{"id": "REQ-1", "text": "Supervisors approve expenses"}]},
            operation="generate",
            actor=self.actor,
            request_id="requirements",
            approved=True,
        )
        for stage, payload in (
            ("use_cases", {"coverage_plan": []}),
            ("test_cases", {"test_cases": [self.case], "review": {"approved": True}, **(case_payload or {})}),
        ):
            projects.append_stage_snapshot(
                project_id=self.project_id, stage=stage, payload=payload, operation="generate", actor=self.actor, request_id=stage, approved=True
            )
        return projects.get_project(self.project_id, actor=self.actor)

    def finding(self, project, category="access"):
        return QualityFindingInput(
            base_project_revision=project.current_revision,
            source_snapshot_id=project.current_snapshots["test_cases"].snapshot_id,
            category=category,
            summary="Supervisor account missing",
            expected_behavior="Authorized supervisor approves expense",
            test_case_ids=["TC-1"],
            environment="staging",
            prerequisites=[ExecutionPrerequisite(name="supervisor", kind="credential", description="Supervisor account")],
        )

    def test_capture_duplicate_and_apply_preserve_assertions_and_invalidate_downstream(self):
        setup_reason = "Resolve the Supervisor account before approval."
        project = self.fixture(
            {
                "review": {"approved": False, "blocking_issues": [setup_reason, "A structural finding"]},
                "coverage_metrics": {"substantive_coverage_status": "incomplete"},
                "substantive_assessment": {
                    "status": "incomplete",
                    "design_status": "assessed_complete",
                    "execution_status": "blocked",
                    "reason": setup_reason,
                    "input_hash": "previous-input-hash",
                    "execution_blockers": [{"test_case_id": "TC-1", "prerequisite_name": "supervisor"}],
                },
            }
        )
        payload = self.finding(project)
        created = capture_finding(self.project_id, self.actor, payload, "record")
        duplicate = capture_finding(self.project_id, self.actor, payload, "record-again")
        self.assertEqual(created["project_revision"], duplicate["project_revision"])
        self.assertEqual(len(list_findings(self.project_id, self.actor)["findings"]), 1)
        original = deepcopy(self.store)
        action = QualityFindingAction(
            action="apply",
            base_project_revision=created["project_revision"],
            prerequisites=[
                ExecutionPrerequisite(
                    name="supervisor", kind="credential", description="Supervisor account", reference="env:PETF_TEST_SUPERVISOR", status="configured"
                )
            ],
        )
        applied = update_finding(self.project_id, created["finding"]["id"], self.actor, action, "apply")
        current = projects.get_project(self.project_id, actor=self.actor)
        self.assertEqual(applied["finding"]["status"], "applied_pending_verification")
        corrected_suite = current.current_snapshots["test_cases"].payload
        corrected = corrected_suite["test_cases"][0]
        self.assertEqual(corrected["steps"], self.case["steps"])
        self.assertEqual(corrected["expected_result"], self.case["expected_result"])
        self.assertEqual(corrected["execution_prerequisites"][0]["reference"], "env:PETF_TEST_SUPERVISOR")
        self.assertEqual(corrected_suite["review"]["blocking_issues"], ["A structural finding"])
        self.assertEqual(corrected_suite["substantive_assessment"]["status"], "unknown")
        self.assertNotIn("input_hash", corrected_suite["substantive_assessment"])
        self.assertNotIn("execution_blockers", corrected_suite["substantive_assessment"])
        self.assertEqual(corrected_suite["coverage_metrics"]["substantive_coverage_status"], "unknown")
        self.assertFalse(current.stage_state["test_cases"].approved)
        self.assertTrue(current.stage_state["test_cases"].stale)
        self.assertTrue(current.stage_state["use_cases"].stale)
        old_path = f"qa_projects/{self.project_id}/snapshots/{project.current_snapshots['test_cases'].snapshot_id}"
        self.assertEqual(self.store[old_path], original[old_path])
        self.assertEqual(update_finding(self.project_id, created["finding"]["id"], self.actor, action, "apply"), applied)

    def test_secrets_and_missing_references_rejected_and_not_ready(self):
        with self.assertRaises(ValidationError):
            ExecutionPrerequisite(name="login", kind="credential", description="password=real-value")
        case = TestCase(**self.case) if hasattr(self, "case") else TestCase(id="TC", title="Login", steps=[])
        case.execution_prerequisites = [
            ExecutionPrerequisite(name="login", kind="credential", description="Login", reference="secret:login", status="configured")
        ]
        self.assertTrue(prerequisite_blockers(case))
        case.execution_prerequisites[0].reference = "env:PETF_TEST_LOGIN"
        with patch.dict("os.environ", {"PETF_TEST_LOGIN": ""}):
            self.assertTrue(prerequisite_blockers(case))
        with patch.dict(
            "os.environ",
            {
                "PETF_TEST_LOGIN": "synthetic",
                "PETF_TEST_REFERENCE_ACCESS": '[{"owner_user_id":"owner","project_id":"project","variables":["PETF_TEST_LOGIN"]}]',
            },
        ):
            self.assertTrue(prerequisite_blockers(case))
            with execution_reference_scope("other-owner", "project"):
                self.assertTrue(prerequisite_blockers(case))
            with execution_reference_scope("owner", "other-project"):
                self.assertTrue(prerequisite_blockers(case))
            with execution_reference_scope("owner", "project"):
                self.assertEqual(prerequisite_blockers(case), [])
            self.assertTrue(prerequisite_blockers(case))

    def test_stale_source_and_application_defect_cannot_backfill_assertions(self):
        project = self.fixture()
        created = capture_finding(self.project_id, self.actor, self.finding(project, "application_defect"), "capture")
        with self.assertRaises(HTTPException) as error:
            update_finding(
                self.project_id,
                created["finding"]["id"],
                self.actor,
                QualityFindingAction(action="apply", base_project_revision=created["project_revision"]),
                "apply",
            )
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            update_finding(
                self.project_id,
                created["finding"]["id"],
                self.actor,
                QualityFindingAction(
                    action="verify",
                    base_project_revision=created["project_revision"],
                    verification_snapshot_id=project.current_snapshots["test_cases"].snapshot_id,
                ),
                "verify",
            )

    def test_execution_failures_create_findings_atomically_and_passed_rerun_verifies(self):
        project = self.fixture()
        source = project.current_snapshots["test_cases"].snapshot_id
        failed = projects.append_stage_snapshot(
            project_id=self.project_id,
            stage="execution",
            payload={"target_environment": "staging", "results": [{"source_test_case_id": "TC-1", "status": "failed"}]},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="run",
            source_snapshot_id=source,
            metadata={"target_environment": "staging"},
        )
        findings = list_findings(self.project_id, self.actor)
        self.assertEqual(len(findings["findings"]), 1)
        finding = findings["findings"][0]
        self.assertEqual(finding["category"], "unknown")
        self.assertEqual(finding["source_snapshot_id"], failed.snapshot_id)
        classified = update_finding(
            self.project_id,
            finding["id"],
            self.actor,
            QualityFindingAction(action="classify", category="environment", base_project_revision=findings["project_revision"]),
            "classify",
        )
        self.assertEqual(classified["finding"]["category"], "environment")
        with patch("app.services.execution_result_storage.INLINE_RESULT_BYTES", 1):
            passed = projects.append_stage_snapshot(
                project_id=self.project_id,
                stage="execution",
                payload={"results": [{"source_test_case_id": "TC-1", "status": "passed"}]},
                operation="automation.execution.run",
                actor=self.actor,
                request_id="rerun",
                source_snapshot_id=source,
                metadata={"target_environment": "staging"},
            )
        verification = QualityFindingAction(action="verify", base_project_revision=passed.project_revision, verification_snapshot_id=passed.snapshot_id)
        part_path = f"qa_projects/{self.project_id}/snapshots/{passed.snapshot_id}/execution_result_parts/0000"
        part = self.store.pop(part_path)
        with self.assertLogs(level="WARNING"), self.assertRaises(HTTPException) as incomplete:
            update_finding(self.project_id, finding["id"], self.actor, verification, "verify")
        self.assertEqual(incomplete.exception.status_code, 409)
        self.store[part_path] = part
        result = update_finding(self.project_id, finding["id"], self.actor, verification, "verify")
        self.assertEqual(result["finding"]["status"], "verified")

    def test_ownership_and_stale_revisions_prevent_writes(self):
        project = self.fixture()
        payload = self.finding(project)
        before = deepcopy(self.store)
        with self.assertRaises(HTTPException) as error:
            capture_finding(self.project_id, self.actor.model_copy(update={"sub": "other-owner"}), payload, "foreign")
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as error:
            capture_finding(self.project_id, self.actor, payload.model_copy(update={"base_project_revision": 0}), "stale")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(before, self.store)

    def test_changed_assertion_cannot_verify_application_defect(self):
        project = self.fixture()
        created = capture_finding(self.project_id, self.actor, self.finding(project, "application_defect"), "capture")
        changed = deepcopy(self.case)
        changed["steps"][0]["expected"] = "Ignore whether the expense was approved"
        suite = projects.append_stage_snapshot(
            project_id=self.project_id,
            stage="test_cases",
            payload={"test_cases": [changed]},
            operation="review",
            actor=self.actor,
            request_id="changed",
            approved=True,
        )
        run = projects.append_stage_snapshot(
            project_id=self.project_id,
            stage="execution",
            payload={"results": [{"source_test_case_id": "TC-1", "status": "passed"}]},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="rerun",
            source_snapshot_id=suite.snapshot_id,
            metadata={"target_environment": "staging"},
        )
        with self.assertRaises(HTTPException) as error:
            update_finding(
                self.project_id,
                created["finding"]["id"],
                self.actor,
                QualityFindingAction(action="verify", base_project_revision=run.project_revision, verification_snapshot_id=run.snapshot_id),
                "verify",
            )
        self.assertIn("Changed assertions", error.exception.detail)

    def test_literal_runtime_marker_cannot_bypass_project_authorization(self):
        case = TestCase(id="TC", title="Input", steps=[{"step": 1, "action": 'Enter "[[env:PETF_TEST_LOGIN]]" into "Login"', "expected": "Signed in"}])
        with patch.dict("os.environ", {"PETF_TEST_LOGIN": "synthetic", "PETF_TEST_REFERENCE_ACCESS": "not-json"}):
            with execution_reference_scope("owner", "project"):
                self.assertTrue(prerequisite_blockers(case))

    def test_reference_scope_reaches_worker_and_resets_after_execution(self):
        import asyncio
        from types import SimpleNamespace
        from app.routers.automation import _execution_with_references

        case = TestCase(
            id="TC",
            title="Input",
            steps=[],
            execution_prerequisites=[
                {"name": "login", "kind": "credential", "description": "Login", "reference": "env:PETF_TEST_LOGIN", "status": "configured"}
            ],
        )
        with patch.dict(
            "os.environ",
            {
                "PETF_TEST_LOGIN": "synthetic",
                "PETF_TEST_REFERENCE_ACCESS": '[{"owner_user_id":"user-1","project_id":"project","variables":["PETF_TEST_LOGIN"]}]',
            },
        ):
            result = asyncio.run(
                _execution_with_references(lambda cases: prerequisite_blockers(cases[0]), SimpleNamespace(project_id="project", test_cases=[case]), self.actor)
            )
            self.assertEqual(result, [])
            self.assertTrue(prerequisite_blockers(case))
