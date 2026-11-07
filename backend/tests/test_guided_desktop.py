"""Business-rule clarification and selected-case repair boundaries."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from app.contracts.quality_feedback import QualityFindingInput, QualityFindingAction
from app.contracts.requirements import Requirement
from app.contracts.test_cases import GenerateTestCasesResponse, RefineTestCasesInput
from app.services import scoped_refinement as repair, workflow_project_service as projects
from app.services.quality_feedback import capture_finding, update_finding
from app.services.requirement_import_service import validate_generation_baseline
from app.services.requirement_scope import approved_generation_requirements
import test_quality_feedback as quality_tests
from app.contracts.requirement_imports import RequirementReviewsInput
from app.services import requirement_import_service as imports
from test_use_case_review_service import _run_transaction


class ClarificationTests(unittest.TestCase):
    def setUp(self):
        quality_tests.QualityFeedbackTests.setUp(self)
        patcher = patch("app.services.requirement_import_service.get_required_firestore_client", return_value=self.transaction_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    tearDown = quality_tests.QualityFeedbackTests.tearDown
    fixture = quality_tests.QualityFeedbackTests.fixture

    def test_defer_exclude_preserve_unaffected_approval_and_block_unknown_generation(self):
        project = self.fixture()
        rows = [
            {"id": "REQ-1", "text": "Supervisor approval policy", "review_status": "Approved"},
            {"id": "REQ-2", "text": "Customer sees receipt", "review_status": "Approved"},
        ]
        projects.append_stage_snapshot(
            project_id=self.project_id,
            stage="requirements",
            payload={"requirements": rows},
            operation="fixture",
            actor=self.actor,
            request_id="rules",
            approved=True,
        )
        project = projects.get_project(self.project_id, actor=self.actor)
        source = project.current_snapshots["requirements"]
        rows = deepcopy(source.payload["requirements"])
        original_source_rows = projects.get_project_stage_snapshot(self.project_id, source.snapshot_id, actor=self.actor).payload["requirements"]
        captured = capture_finding(
            self.project_id,
            self.actor,
            QualityFindingInput(
                base_project_revision=project.current_revision,
                source_snapshot_id=source.snapshot_id,
                category="ambiguous_requirement",
                summary="Who may approve?",
                expected_behavior="Unresolved",
                requirement_ids=["REQ-1"],
            ),
            "question",
        )
        identity = captured["finding"]["id"]
        for action in ("defer", "exclude"):
            project = projects.get_project(self.project_id, actor=self.actor)
            updated = update_finding(
                self.project_id,
                identity,
                self.actor,
                QualityFindingAction(action=action, base_project_revision=project.current_revision, reason="Await the policy owner"),
                action,
            )
            current = projects.get_project(self.project_id, actor=self.actor)
            values = current.current_snapshots["requirements"].payload["requirements"]
            self.assertEqual(values[1], rows[1])
            self.assertEqual(values[0]["text"], rows[0]["text"])
            self.assertEqual(values[0]["review_status"], "Rejected" if action == "exclude" else "Needs Review")
            self.assertEqual(updated["finding"]["original_source_snapshot_id"], source.snapshot_id)
            self.assertEqual(
                projects.get_project_stage_snapshot(self.project_id, source.snapshot_id, actor=self.actor).payload["requirements"], original_source_rows
            )
            self.assertTrue(current.stage_state["test_cases"].stale)
            self.assertEqual([r.id for r in approved_generation_requirements(current)], ["REQ-2"])
            self.assertFalse(current.stage_state["requirements"].approved)
            with self.assertRaises(HTTPException) as failure:
                validate_generation_baseline(
                    self.project_id, self.actor, current.current_revision, [Requirement(id="REQ-1", text=rows[0]["text"], review_status="Approved")]
                )
            self.assertEqual(failure.exception.status_code, 409)
            validate_generation_baseline(
                self.project_id, self.actor, current.current_revision, [Requirement(id="REQ-2", text=rows[1]["text"], review_status="Approved")]
            )

    def test_scope_reason_and_current_revision_are_required(self):
        project = self.fixture()
        captured = capture_finding(
            self.project_id,
            self.actor,
            QualityFindingInput(
                base_project_revision=project.current_revision,
                source_snapshot_id=project.current_snapshots["requirements"].snapshot_id,
                category="ambiguous_requirement",
                summary="Which supervisor?",
                expected_behavior="Unresolved",
                requirement_ids=["REQ-1"],
            ),
            "question",
        )
        current = projects.get_project(self.project_id, actor=self.actor)
        requirement = current.current_snapshots["requirements"].payload["requirements"][0]
        review = RequirementReviewsInput(
            base_project_revision=current.current_revision,
            idempotency_key="no-bypass",
            reviews=[{"requirement_uid": requirement["requirement_uid"], "review_status": "Approved"}],
        )
        with patch.object(imports, "transactional", side_effect=_run_transaction), self.assertRaises(HTTPException) as bypass:
            imports.repository().review(self.project_id, self.actor, review, "approve-unknown")
        self.assertEqual(bypass.exception.status_code, 409)
        before = deepcopy(self.store)
        for revision, reason in ((project.current_revision, "Waiting"), (captured["project_revision"], "")):
            with self.assertRaises(HTTPException):
                update_finding(
                    self.project_id,
                    captured["finding"]["id"],
                    self.actor,
                    QualityFindingAction(action="defer", base_project_revision=revision, reason=reason),
                    "defer",
                )
            self.assertEqual(self.store, before)


class ScopedRepairTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            {
                "id": f"TC-{n}",
                "title": f"Booking {n}",
                "steps": [{"step": 1, "action": "Submit booking", "expected": "Booking receipt appears"}],
                "linked_requirement_ids": [f"REQ-{n}"],
                "scenario_refs": [f"SCN-{n}"],
            }
            for n in (1, 2)
        ]
        self.plan = [
            {
                "requirement_id": f"REQ-{n}",
                "requirement_text": "Create booking",
                "scenarios": [{"id": f"SCN-{n}", "requirement_id": f"REQ-{n}", "title": "Book", "objective": "One durable booking"}],
            }
            for n in (1, 2)
        ]
        self.baseline = GenerateTestCasesResponse(test_cases=self.cases, coverage_plan=self.plan)
        self.sources = {"source_coverage_plan": self.plan, "source_requirement_analysis": []}
        self.payload = RefineTestCasesInput(
            project_id="project",
            base_project_revision=7,
            selected_test_case_ids=["TC-2"],
            requirements=[{"id": f"REQ-{n}", "text": "Create booking", "review_status": "Approved"} for n in (1, 2)],
            test_cases=[{**c, "title": "Client alteration"} for c in self.cases],
            template={"name": "Table", "format": "table", "fields": []},
            feedback="Check receipt ID",
        )
        self.project = SimpleNamespace(
            current_revision=7,
            current_snapshots={
                "test_cases": SimpleNamespace(payload=self.baseline.model_dump()),
                "requirements": SimpleNamespace(
                    snapshot_id="requirements",
                    payload={"requirements": [{"id": f"REQ-{n}", "text": "Canonical booking rule", "review_status": "Approved"} for n in (1, 2)]},
                ),
            },
            stage_state={"test_cases": SimpleNamespace(stale=False), "requirements": SimpleNamespace(stale=False, current_snapshot_id="requirements")},
        )

    def test_server_baseline_scopes_model_and_preserves_unrelated_test(self):
        with patch.object(repair, "get_project", return_value=self.project):
            model, sources, original = repair.prepare_scoped_refinement(self.payload, self.sources, None)
        self.assertEqual([case.id for case in model.test_cases], ["TC-2"])
        self.assertEqual(model.test_cases[0].title, "Booking 2")
        self.assertEqual([r.id for r in model.requirements], ["REQ-2"])
        self.assertEqual(model.requirements[0].text, "Canonical booking rule")
        self.assertEqual(model.context.requirements, model.requirements)
        self.assertEqual([g["requirement_id"] for g in sources["source_coverage_plan"]], ["REQ-2"])
        original.generation_tasks = [{"requirement_ids": ["REQ-1"], "reason": "Unrelated missing scenario"}]
        candidate = GenerateTestCasesResponse(test_cases=[{**self.cases[1], "title": "Verify receipt ID"}], approved=True)
        merged = repair.merge_scoped_refinement(candidate, original, ["TC-2"], self.sources)
        self.assertEqual(merged.test_cases[0], self.baseline.test_cases[0])
        self.assertEqual(merged.coverage_plan, self.baseline.coverage_plan)
        self.assertEqual(merged.scoped_refinement, {"changed": 1, "added": 0, "preserved": 1, "changed_ids": ["TC-2"]})
        self.assertEqual(merged.generation_tasks, original.generation_tasks)
        self.assertFalse(merged.approved)
        self.assertFalse(merged.review.approved)
        self.assertEqual(merged.substantive_assessment["status"], "unknown")

    def test_stale_unknown_and_duplicate_selection_rejected(self):
        for ids, revision in ((["TC-9"], 7), (["TC-2", "TC-2"], 7), (["TC-2"], 6)):
            with patch.object(repair, "get_project", return_value=self.project), self.assertRaises(HTTPException):
                repair.prepare_scoped_refinement(
                    self.payload.model_copy(update={"selected_test_case_ids": ids, "base_project_revision": revision}), self.sources, None
                )

    def test_out_of_scope_missing_placeholder_and_remapped_results_rejected(self):
        before = self.baseline.model_dump()
        candidates = [self.cases, [], [{**self.cases[1], "steps": []}], [{**self.cases[1], "linked_requirement_ids": ["REQ-1"]}]]
        for cases in candidates:
            with self.assertRaises(HTTPException):
                repair.merge_scoped_refinement(GenerateTestCasesResponse(test_cases=cases), self.baseline, ["TC-2"], self.sources)
            self.assertEqual(self.baseline.model_dump(), before)
