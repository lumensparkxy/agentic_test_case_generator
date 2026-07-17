from pathlib import Path
import sys
import unittest
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, OrchestratorStatusResponse
from app.services.orchestrator_service import build_orchestrator_status, get_project_orchestrator_status
from app.services.workflow_project_service import append_stage_snapshot, create_project


class FakeSnapshot:
    def __init__(self, payload: dict[str, Any] | None):
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._payload) if self._payload is not None else None


class FakeDocument:
    def __init__(self, path: str, store: dict[str, dict[str, Any]]):
        self.path = path
        self.store = store

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        if merge and self.path in self.store:
            self.store[self.path].update(payload)
        else:
            self.store[self.path] = dict(payload)

    def update(self, payload: dict[str, Any]) -> None:
        if self.path not in self.store:
            raise RuntimeError("document missing")
        self.store[self.path].update(payload)

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self.store.get(self.path))

    def collection(self, name: str):
        return FakeCollection(f"{self.path}/{name}", self.store)


class FakeCollection:
    def __init__(self, path: str, store: dict[str, dict[str, Any]]):
        self.path = path
        self.store = store

    def document(self, document_id: str):
        return FakeDocument(f"{self.path}/{document_id}", self.store)

    def stream(self):
        prefix = f"{self.path}/"
        for path, payload in list(self.store.items()):
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix) :]
            if "/" not in remainder:
                yield FakeSnapshot(payload)


class OrchestratorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.collection = FakeCollection("qa_projects", self.store)
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        self.collection_patch = patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=self.collection,
        )
        self.collection_patch.start()
        app.dependency_overrides[get_current_user] = lambda: self.actor

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.collection_patch.stop()

    def _create_project(self):
        return create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-create")

    def _append_requirements(self, project_id: str, *, approved: bool = True, title: str = "Requirements"):
        return append_stage_snapshot(
            project_id=project_id,
            stage="requirements",
            payload={"requirements": [{"id": "REQ-001", "text": title, "review_status": "Approved" if approved else "Needs Review"}]},
            operation="requirements.refine",
            actor=self.actor,
            request_id=f"req-{title}",
            approved=approved,
            title=title,
        )

    def _append_use_cases(
        self,
        project_id: str,
        *,
        approved: bool = True,
        source_snapshot_id: str | None = None,
        human_reviewed: bool = True,
    ):
        snapshot = append_stage_snapshot(
            project_id=project_id,
            stage="use_cases",
            payload={
                "coverage_plan": [
                    {
                        "requirement_id": "REQ-001",
                        "scenarios": [{"id": "REQ-001-SCN-01", "requirement_id": "REQ-001", "title": "Primary checkout"}],
                    }
                ],
                "requirement_analysis": [],
                "review": {
                    "approved": approved,
                    "score": 100 if approved else 70,
                    "threshold": 90,
                    "blocking_issues": [] if approved else ["Use Case quality review requires attention"],
                    "suggestions": [],
                    "unmet_criteria": [],
                },
            },
            operation="testcases.generate.use_cases",
            actor=self.actor,
            request_id="req-use-cases",
            approved=approved,
            source_snapshot_id=source_snapshot_id,
            title="Use cases",
        )
        if approved and human_reviewed:
            use_cases_state = self.store[f"qa_projects/{project_id}"]["stage_state"]["use_cases"]
            use_cases_state.setdefault("metadata", {})["latest_human_review"] = {
                "review_id": "review-use-cases",
                "snapshot_id": snapshot.snapshot_id,
                "decision": "approve",
                "comment": None,
                "reviewer_user_id": self.actor.sub,
                "reviewer_name": self.actor.name,
                "reviewed_at": "2026-07-17T12:00:00+00:00",
            }
        return snapshot

    def _append_test_cases(self, project_id: str, *, approved: bool = True, source_snapshot_id: str | None = None):
        return append_stage_snapshot(
            project_id=project_id,
            stage="test_cases",
            payload={
                "test_cases": [
                    {
                        "id": "TC-001",
                        "title": "Checkout happy path",
                        "description": "Baseline checkout coverage",
                        "status": "Ready",
                        "linked_requirement_ids": ["REQ-001"],
                        "scenario_refs": ["REQ-001-SCN-01"],
                    }
                ],
                "review": {
                    "approved": approved,
                    "score": 100 if approved else 70,
                    "threshold": 90,
                    "blocking_issues": [] if approved else ["Approve suite before export"],
                    "suggestions": [],
                    "unmet_criteria": [],
                },
            },
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-test-cases",
            approved=approved,
            source_snapshot_id=source_snapshot_id,
            title="Test cases",
            metadata={
                "source_snapshot_ids": {
                    "requirements": None,
                    "context": None,
                    "use_cases": source_snapshot_id,
                },
                "test_case_count": 1,
            },
        )

    def _append_impact_analysis(self, project_id: str, *, changed_item_approved: bool = True):
        return append_stage_snapshot(
            project_id=project_id,
            stage="impact_analysis",
            payload={
                "changed_items": [
                    {
                        "item_id": "REQ-001",
                        "change_type": "modified",
                        "approved": changed_item_approved,
                        "requirement_id": "REQ-001",
                    }
                ],
                "recommendations": [
                    {
                        "recommendation_id": "REC-001",
                        "action": "update",
                        "accepted": True,
                        "test_case_id": "TC-001",
                    }
                ],
                "impacted_test_cases": [
                    {
                        "test_case_id": "TC-001",
                        "title": "Checkout happy path",
                        "impact_source": "direct",
                        "linked_requirement_ids": ["REQ-001"],
                        "scenario_refs": ["REQ-001-SCN-01"],
                        "reason": "Direct traceability match.",
                    }
                ],
                "summary": {
                    "changed_item_count": 1,
                    "added_count": 0,
                    "modified_count": 1,
                    "removed_count": 0,
                    "unchanged_requirement_count": 0,
                    "directly_impacted_test_case_count": 1,
                    "semantic_neighbor_count": 0,
                    "recommendation_counts": {"update": 1},
                },
            },
            operation="impact.analysis",
            actor=self.actor,
            request_id="req-impact",
            approved=False,
            title="Impact analysis",
        )

    def _seed_first_generation_ready(self):
        project = self._create_project()
        requirement_snapshot = self._append_requirements(project.project_id)
        self._append_use_cases(project.project_id, source_snapshot_id=requirement_snapshot.snapshot_id)
        return project

    def _seed_baseline_suite(self, *, test_cases_approved: bool = True):
        project = self._create_project()
        requirement_snapshot = self._append_requirements(project.project_id)
        use_case_snapshot = self._append_use_cases(project.project_id, source_snapshot_id=requirement_snapshot.snapshot_id)
        self._append_test_cases(project.project_id, approved=test_cases_approved, source_snapshot_id=use_case_snapshot.snapshot_id)
        return project

    def _action(self, status: OrchestratorStatusResponse, action_id: str):
        return next(action for action in status.next_actions if action.action == action_id)

    def test_no_project_status_reports_missing_project_blocker(self) -> None:
        status = build_orchestrator_status(None)

        self.assertEqual(status.current_stage, "requirements")
        self.assertEqual(status.next_actions[0].action, "refine")
        self.assertFalse(status.next_actions[0].enabled)
        self.assertEqual(status.blockers[0].code, "missing_project")

    def test_machine_approved_use_cases_still_require_matching_human_review(self) -> None:
        project = self._create_project()
        requirement_snapshot = self._append_requirements(project.project_id)
        self._append_use_cases(
            project.project_id,
            approved=True,
            human_reviewed=False,
            source_snapshot_id=requirement_snapshot.snapshot_id,
        )

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        self.assertEqual(status.stages["use_cases"].status, "attention_required")
        self.assertFalse(status.stages["use_cases"].approved)
        approval = self._action(status, "approve")
        self.assertEqual(approval.stage, "use_cases")
        self.assertTrue(approval.primary)

    def test_no_baseline_recommends_first_time_generation(self) -> None:
        project = self._seed_first_generation_ready()

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        generate = self._action(status, "generate")
        self.assertTrue(generate.primary)
        self.assertTrue(generate.enabled)
        self.assertEqual(generate.stage, "test_cases")
        self.assertFalse(status.has_baseline_test_suite)
        self.assertEqual(status.current_stage, "test_cases")

    def test_stale_upstream_with_baseline_recommends_impact_analysis(self) -> None:
        project = self._seed_baseline_suite()
        self._append_requirements(project.project_id, title="Requirements v2")

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        analyze = self._action(status, "analyze_impact")
        full_regenerate = self._action(status, "full_regenerate")
        self.assertTrue(status.has_baseline_test_suite)
        self.assertTrue(status.upstream_changed)
        self.assertEqual(status.changed_upstream_stages, ["requirements"])
        self.assertTrue(analyze.primary)
        self.assertTrue(analyze.enabled)
        self.assertTrue(full_regenerate.secondary)
        self.assertTrue(full_regenerate.enabled)

    def test_unapproved_changed_upstream_can_still_run_impact_analysis(self) -> None:
        project = self._seed_baseline_suite()
        self._append_requirements(project.project_id, approved=False, title="Requirements v2")

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        analyze = self._action(status, "analyze_impact")
        full_regenerate = self._action(status, "full_regenerate")
        self.assertTrue(status.has_baseline_test_suite)
        self.assertTrue(status.upstream_changed)
        self.assertEqual(status.current_stage, "impact_analysis")
        self.assertTrue(analyze.primary)
        self.assertTrue(analyze.enabled)
        self.assertTrue(full_regenerate.secondary)

    def test_current_impact_analysis_recommends_apply_update(self) -> None:
        project = self._seed_baseline_suite()
        self._append_requirements(project.project_id, title="Requirements v2")
        self._append_impact_analysis(project.project_id, changed_item_approved=True)

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        apply_update = self._action(status, "apply_update")
        self.assertTrue(apply_update.primary)
        self.assertTrue(apply_update.enabled)
        self.assertEqual(apply_update.label, "Apply Accepted Updates")
        self.assertEqual(status.stages["impact_analysis"].summary["modified_count"], 1)
        self.assertEqual(status.stages["impact_analysis"].summary["directly_impacted_test_case_count"], 1)

    def test_apply_update_is_blocked_until_changed_items_are_approved(self) -> None:
        project = self._seed_baseline_suite()
        self._append_requirements(project.project_id, title="Requirements v2")
        self._append_impact_analysis(project.project_id, changed_item_approved=False)

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        apply_update = self._action(status, "apply_update")
        self.assertFalse(apply_update.enabled)
        self.assertEqual(apply_update.blockers[0].code, "missing_approval")
        self.assertEqual(apply_update.blockers[0].action, "apply_update")

    def test_apply_update_is_blocked_until_changed_upstream_stage_is_approved(self) -> None:
        project = self._seed_baseline_suite()
        self._append_requirements(project.project_id, approved=False, title="Requirements v2")
        self._append_impact_analysis(project.project_id, changed_item_approved=True)

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        apply_update = self._action(status, "apply_update")
        self.assertTrue(apply_update.primary)
        self.assertFalse(apply_update.enabled)
        self.assertEqual(apply_update.blockers[0].code, "missing_approval")
        self.assertEqual(apply_update.blockers[0].source_stage, "requirements")

    def test_unapproved_test_cases_block_report_action(self) -> None:
        project = self._seed_baseline_suite(test_cases_approved=False)

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        approve = self._action(status, "approve")
        report = self._action(status, "report")
        self.assertTrue(approve.primary)
        self.assertFalse(report.enabled)
        self.assertEqual(report.blockers[0].code, "missing_approval")

    def test_approved_test_cases_recommend_automation_from_current_snapshot(self) -> None:
        project = self._seed_baseline_suite()

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        automate = self._action(status, "automate")
        report = self._action(status, "report")
        review = self._action(status, "review")
        self.assertTrue(automate.primary)
        self.assertTrue(automate.enabled)
        self.assertEqual(automate.stage, "automation")
        self.assertTrue(report.secondary)
        self.assertTrue(report.enabled)
        self.assertTrue(review.secondary)
        self.assertTrue(review.enabled)
        self.assertEqual(status.stages["automation"].status, "ready")
        self.assertEqual(status.stages["automation"].summary["source"], "test_cases")
        self.assertEqual(status.stages["automation"].summary["test_case_count"], 1)
        self.assertEqual(
            status.stages["automation"].summary["source_snapshot_id"],
            status.stages["test_cases"].current_snapshot_id,
        )

    def test_execution_preview_recommends_execute(self) -> None:
        project = self._seed_baseline_suite()
        append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"summary": {"executable": 1}, "target_environment": "staging"},
            operation="automation.execution.preview",
            actor=self.actor,
            request_id="req-preview",
            approved=True,
            title="Execution preview",
        )

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        execute = self._action(status, "execute")
        self.assertTrue(execute.primary)
        self.assertTrue(execute.enabled)
        self.assertEqual(status.stages["execution"].status, "ready")

    def test_passed_execution_without_report_recommends_report(self) -> None:
        project = self._seed_baseline_suite()
        append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "passed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-run",
            approved=True,
            title="Execution run",
            metadata={"status": "passed", "run_id": "run-1"},
        )

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        report = self._action(status, "report")
        review = self._action(status, "review")
        self.assertTrue(report.primary)
        self.assertTrue(report.enabled)
        self.assertTrue(review.secondary)
        self.assertTrue(review.enabled)
        self.assertEqual(status.current_stage, "reports")

    def test_stale_report_recommends_report_regeneration(self) -> None:
        project = self._seed_baseline_suite()
        append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "passed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-run",
            approved=True,
            title="Execution run",
            metadata={"status": "passed", "run_id": "run-1"},
        )
        append_stage_snapshot(
            project_id=project.project_id,
            stage="reports",
            payload={"format": "json", "evidence": {"source_snapshot_ids": {"test_cases": "snap-old"}}},
            operation="export.json",
            actor=self.actor,
            request_id="req-report",
            approved=True,
            title="Evidence report",
        )
        append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={"test_cases": [{"id": "TC-002", "title": "Checkout v2"}]},
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-test-cases-v2",
            approved=True,
            title="Updated test cases",
        )

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        report = self._action(status, "report")
        self.assertTrue(report.primary)
        self.assertTrue(report.enabled)
        self.assertEqual(report.label, "Regenerate Evidence Report")
        self.assertEqual(status.stages["reports"].status, "stale")
        self.assertEqual(status.current_stage, "reports")

    def test_failed_execution_recommends_review(self) -> None:
        project = self._seed_baseline_suite()
        append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "failed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-run",
            approved=False,
            title="Execution run",
            metadata={"status": "failed", "run_id": "run-1"},
        )

        status = get_project_orchestrator_status(project.project_id, actor=self.actor)

        review = self._action(status, "review")
        self.assertTrue(review.primary)
        self.assertEqual(status.stages["review"].blockers[0].code, "failed_execution")

    def test_status_endpoint_returns_orchestrator_response(self) -> None:
        project = self._seed_first_generation_ready()

        with TestClient(app) as client:
            response = client.get(f"/projects/{project.project_id}/orchestrator/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["project_id"], project.project_id)
        self.assertEqual(payload["next_actions"][0]["action"], "generate")


if __name__ == "__main__":
    unittest.main()
