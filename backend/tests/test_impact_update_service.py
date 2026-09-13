from pathlib import Path
import sys
import unittest
from typing import Any
from copy import deepcopy
from app.services.workflow_project_service import ProjectConflictError, ProjectPermissionError, update_project
from app.services.test_case_quality import placeholder_reason
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from test_use_case_review_service import FakeFirestoreClient, _run_transaction

from app.models import AuthUser, TestCase, TestStep
from copy import deepcopy
from fastapi import HTTPException
from app.services import impact_update_service as service
from app.services.workflow_project_service import ProjectConflictError, ProjectPermissionError, update_project
from app.services.impact_update_service import analyze_project_impact, apply_project_impact_update
from app.services.orchestrator_service import get_project_orchestrator_status
from app.services.workflow_project_service import append_stage_snapshot, create_project, get_project


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


def _requirements(*, omit: set[str] | None = None, modified: set[str] | None = None) -> list[dict[str, Any]]:
    omit = omit or set()
    modified = modified or set()
    rows = []
    for index in range(1, 11):
        req_id = f"REQ-{index:03d}"
        if req_id in omit:
            continue
        text = f"Requirement {index} baseline behavior"
        if req_id in modified:
            text = f"Requirement {index} changed payment retry approval behavior"
        rows.append({"id": req_id, "text": text, "review_status": "Approved"})
    return rows


def _coverage_plan(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": requirement["id"],
            "requirement_text": requirement["text"],
            "scenarios": [
                {
                    "id": f"{requirement['id']}-SCN-01",
                    "requirement_id": requirement["id"],
                    "scenario_type": "Happy Path",
                    "title": f"{requirement['id']} primary behavior",
                    "objective": f"Validate {requirement['text']}",
                    "priority": "High",
                    "must_have": True,
                }
            ],
        }
        for requirement in requirements
    ]


def _test_case(req_id: str, *, title: str | None = None) -> TestCase:
    index = int(req_id.rsplit("-", 1)[-1])
    return TestCase(
        id=f"TC-{index:03d}",
        title=title or f"{req_id} regression test",
        description=f"Baseline coverage for {req_id}",
        status="Ready",
        steps=[TestStep(step=1, action=f"Exercise {req_id}", expected="Behavior is correct")],
        linked_requirement_ids=[req_id],
        scenario_refs=[f"{req_id}-SCN-01"],
        artifact_set_id="tc-set-1",
        artifact_item_id=f"tc-item-{index:03d}",
        artifact_version_id=f"tc-version-{index:03d}",
        artifact_version_number=1,
    )


class ImpactUpdateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        for target in ("enforce_billing_access", "start_workflow_run", "complete_workflow_run", "record_usage_event", "record_billing_consumption"):
            patcher = patch.object(service, target, return_value="run-test")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(service, "generate_test_cases", side_effect=self._generate)
        self.generator = patcher.start()
        self.addCleanup(patcher.stop)
        self.transaction_client = FakeFirestoreClient(self.store)
        for target, kwargs in [
            ("app.services.workflow_project_service.get_required_firestore_client", {"return_value": self.transaction_client}),
            ("app.services.workflow_project_service.transactional", {"side_effect": _run_transaction}),
        ]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.project_collection = FakeCollection("qa_projects", self.store)
        self.test_case_collection = FakeCollection("test_case_sets", self.store)
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        self.required_collection_patch = patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=self.project_collection,
        )
        self.optional_collection_patch = patch(
            "app.services.versioning_service.get_optional_firestore_collection",
            return_value=self.test_case_collection,
        )
        self.required_collection_patch.start()
        self.optional_collection_patch.start()

    def _generate(self, payload, **kwargs):
        return {
            "approved": True,
            "test_cases": [
                {
                    "id": f"MODEL-{scenario.id}",
                    "title": "Verify changed approval",
                    "description": "Exercise revised approval rules",
                    "steps": [{"step": 1, "action": "Submit the payment with retry enabled", "expected": "Payment status is Pending approval"}],
                    "linked_requirement_ids": [plan.requirement_id],
                    "scenario_refs": [scenario.id],
                    "generation_source": "model",
                }
                for plan in payload.coverage_plan
                for scenario in plan.scenarios
            ],
        }

    def tearDown(self) -> None:
        self.optional_collection_patch.stop()
        self.required_collection_patch.stop()

    def _seed_project(self, *, modified: set[str] | None = None, omit: set[str] | None = None, include_semantic_neighbor: bool = False):
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        baseline_requirements = _requirements()
        baseline_requirement_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="requirements",
            payload={"requirements": baseline_requirements},
            operation="requirements.parse",
            actor=self.actor,
            request_id="req-2",
            approved=True,
            title="Requirements v1",
        )
        baseline_use_case_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="use_cases",
            payload={"coverage_plan": _coverage_plan(baseline_requirements), "requirement_analysis": []},
            operation="testcases.generate.use_cases",
            actor=self.actor,
            request_id="req-3",
            approved=True,
            source_snapshot_id=baseline_requirement_snapshot.snapshot_id,
            title="Use cases v1",
        )
        test_cases = [_test_case(requirement["id"]) for requirement in baseline_requirements]
        if include_semantic_neighbor:
            test_cases[0] = _test_case("REQ-001", title="Payment retry ledger audit")
        append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={
                "test_cases": [test_case.model_dump(mode="json") for test_case in test_cases],
                "coverage_plan": _coverage_plan(baseline_requirements),
                "requirement_analysis": [],
            },
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-4",
            approved=True,
            source_snapshot_id=baseline_use_case_snapshot.snapshot_id,
            title="Test cases v1",
            metadata={
                "source_snapshot_ids": {
                    "requirements": baseline_requirement_snapshot.snapshot_id,
                    "context": None,
                    "use_cases": baseline_use_case_snapshot.snapshot_id,
                },
                "source_requirements_snapshot_id": baseline_requirement_snapshot.snapshot_id,
                "source_use_case_snapshot_id": baseline_use_case_snapshot.snapshot_id,
            },
        )
        current_requirements = _requirements(modified=modified or set(), omit=omit or set())
        append_stage_snapshot(
            project_id=project.project_id,
            stage="requirements",
            payload={"requirements": current_requirements},
            operation="requirements.refine",
            actor=self.actor,
            request_id="req-5",
            approved=True,
            title="Requirements v2",
        )
        append_stage_snapshot(
            project_id=project.project_id,
            stage="use_cases",
            payload={"coverage_plan": _coverage_plan(current_requirements)},
            operation="use_cases.generate",
            actor=self.actor,
            request_id="req-6",
            approved=True,
        )
        state = self.store[f"qa_projects/{project.project_id}"]["stage_state"]["use_cases"]
        for item in state["metadata"]["scenario_reviews"]["items"].values():
            item["status"] = "approved"
        return get_project(project.project_id, actor=self.actor)

    def test_detects_two_changed_requirements_and_maps_direct_impacts_only(self) -> None:
        project = self._seed_project(modified={"REQ-003", "REQ-010"}, include_semantic_neighbor=True)

        result_project = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="req-impact")
        analysis = result_project.current_snapshots["impact_analysis"].payload

        self.assertEqual(len([r for r in analysis["changed_items"] if r["kind"] == "requirement"]), 2)
        self.assertEqual(analysis["summary"]["modified_count"], 4)
        self.assertEqual(analysis["summary"]["unchanged_requirement_count"], 8)
        self.assertEqual(analysis["summary"]["directly_impacted_test_case_count"], 2)
        direct_ids = {item["test_case_id"] for item in analysis["impacted_test_cases"] if item["impact_source"] == "direct"}
        self.assertEqual(direct_ids, {"TC-003", "TC-010"})
        semantic_recommendations = [item for item in analysis["recommendations"] if item["impact_source"] == "semantic_neighbor"]
        self.assertTrue(semantic_recommendations)
        self.assertTrue(all(not item["accepted"] for item in semantic_recommendations))

    def test_apply_preserves_unchanged_versions_and_versions_impacted_cases(self) -> None:
        project = self._seed_project(modified={"REQ-003", "REQ-010"})
        analyzed_project = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="req-impact")

        updated_project = apply_project_impact_update(
            project_id=analyzed_project.project_id,
            actor=self.actor,
            request_id="req-apply",
            base_project_revision=analyzed_project.current_revision,
        )
        test_cases = {item["id"]: item for item in updated_project.current_snapshots["test_cases"].payload["test_cases"]}

        self.assertEqual(test_cases["TC-001"]["artifact_version_number"], 1)
        self.assertEqual(test_cases["TC-003"]["artifact_version_number"], 2)
        self.assertEqual(test_cases["TC-010"]["artifact_version_number"], 2)
        self.assertIn("impact:update", test_cases["TC-003"]["tags"])
        result = updated_project.current_snapshots["test_cases"].payload["impact_update_result"]
        self.assertEqual(result["preserved_count"], 8)
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual(result["added_count"], 0)

    def test_apply_clears_incremental_update_recommendation_after_versioned_snapshot(self) -> None:
        project = self._seed_project(modified={"REQ-003", "REQ-010"})
        analyzed_project = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="req-impact")
        before_status = get_project_orchestrator_status(analyzed_project.project_id, actor=self.actor)

        updated_project = apply_project_impact_update(
            project_id=analyzed_project.project_id, actor=self.actor, request_id="req-apply", base_project_revision=analyzed_project.current_revision
        )
        after_status = get_project_orchestrator_status(updated_project.project_id, actor=self.actor)

        before_actions = {action.action for action in before_status.next_actions}
        after_actions = {action.action for action in after_status.next_actions}
        self.assertIn("apply_update", before_actions)
        self.assertFalse(after_status.upstream_changed)
        self.assertNotIn("apply_update", after_actions)
        self.assertEqual(after_status.stages["test_cases"].summary["preserved_count"], 8)
        self.assertEqual(after_status.stages["test_cases"].summary["updated_count"], 2)

    def test_removed_requirement_deprecates_linked_test_case(self) -> None:
        project = self._seed_project(omit={"REQ-005"})
        analyzed_project = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="req-impact")

        updated_project = apply_project_impact_update(
            project_id=analyzed_project.project_id, actor=self.actor, request_id="req-apply", base_project_revision=analyzed_project.current_revision
        )
        test_cases = {item["id"]: item for item in updated_project.current_snapshots["test_cases"].payload["test_cases"]}

        self.assertEqual(test_cases["TC-005"]["status"], "Deprecated")
        self.assertFalse(updated_project.stage_state["test_cases"].approved)
        self.assertEqual(updated_project.current_snapshots["test_cases"].payload["impact_update_result"]["deprecated_count"], 1)

    def test_legacy_placeholder_is_repair_work_and_replaced_under_same_identity(self):
        project = self._seed_project()
        snapshot = project.current_snapshots["test_cases"]
        path = f"qa_projects/{project.project_id}/snapshots/{snapshot.snapshot_id}"
        self.store[path]["payload"]["test_cases"][0]["steps"] = [
            {"step": 1, "action": "Review the implemented behavior for REQ-001.", "expected": "The behavior reflects the approved changed requirement."}
        ]
        original = deepcopy(self.store[path])
        viewed = get_project(project.project_id, actor=self.actor)
        self.assertEqual(len(viewed.current_snapshots["test_cases"].payload["test_cases"]), 9)
        self.assertFalse(viewed.stage_state["test_cases"].approved)
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        repairs = [r for r in analyzed.current_snapshots["impact_analysis"].payload["recommendations"] if r["recommendation_id"].startswith("repair-")]
        self.assertEqual(len(repairs), 1)
        self.assertFalse(repairs[0]["accepted"])
        updated = apply_project_impact_update(
            project_id=project.project_id,
            actor=self.actor,
            request_id="repair",
            accepted_recommendation_ids=[repairs[0]["recommendation_id"]],
            base_project_revision=analyzed.current_revision,
        )
        rows = updated.current_snapshots["test_cases"].payload["test_cases"]
        repaired = next(r for r in rows if r["id"] == "TC-001")
        self.assertEqual(repaired["artifact_item_id"], "tc-item-001")
        self.assertEqual(repaired["artifact_version_number"], 2)
        self.assertIsNone(placeholder_reason(repaired))
        self.assertEqual(self.store[path], original)
        self.assertFalse(updated.stage_state["test_cases"].approved)
        self.assertEqual(updated.current_snapshots["test_cases"].payload["generation_tasks"], [])

    def test_generation_failure_preserves_entire_store(self):
        project = self._seed_project(modified={"REQ-003"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        before = deepcopy(self.store)
        self.generator.side_effect = RuntimeError("Model unavailable")
        with self.assertRaises(RuntimeError):
            apply_project_impact_update(project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision)
        self.assertEqual(self.store, before)

    def test_placeholder_and_incomplete_results_cannot_publish(self):
        project = self._seed_project(modified={"REQ-003"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        before = deepcopy(self.store)
        for kind in ("generic", "missing", "rejected", "foreign"):

            def bad_output(payload, **kwargs):
                output = self._generate(payload)
                if kind == "generic":
                    output["test_cases"][0]["steps"][0]["action"] = "Execute the negative scenario for REQ-003"
                elif kind == "missing":
                    output["test_cases"] = []
                elif kind == "rejected":
                    output["approved"] = False
                else:
                    output["test_cases"][0]["linked_requirement_ids"] = ["REQ-FOREIGN"]
                return output

            self.generator.side_effect = bad_output
            with self.subTest(kind=kind), self.assertRaises(service.ImpactWorkflowError):
                apply_project_impact_update(
                    project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision
                )
            self.assertEqual(self.store, before)

    def test_concurrent_edit_and_missing_revision_do_not_replace_suite(self):
        project = self._seed_project(modified={"REQ-003"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        with self.assertRaises(ProjectConflictError):
            apply_project_impact_update(project_id=project.project_id, actor=self.actor, request_id="missing")
        self.generator.assert_not_called()

        def concurrent(payload, **kwargs):
            update_project(
                project_id=project.project_id, actor=self.actor, request_id="other", name="Other edit", base_project_revision=analyzed.current_revision
            )
            return self._generate(payload)

        self.generator.side_effect = concurrent
        with self.assertRaises(ProjectConflictError):
            apply_project_impact_update(project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision)
        latest = get_project(project.project_id, actor=self.actor)
        self.assertEqual(latest.current_snapshots["test_cases"], analyzed.current_snapshots["test_cases"])
        self.assertEqual(latest.name, "Other edit")

    def test_commit_failure_does_not_publish_artifact_versions(self):
        project = self._seed_project(modified={"REQ-003"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        before = deepcopy(self.store)
        from test_use_case_review_service import FakeTransaction

        with patch.object(self.transaction_client, "transaction", side_effect=lambda: FakeTransaction(self.store, fail_on_create_segment="snapshots")):
            with self.assertRaises(RuntimeError):
                apply_project_impact_update(
                    project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision
                )
        self.assertEqual(self.store, before)

    def test_unsaved_withheld_row_is_added_instead_of_updating_missing_identity(self):
        project = self._seed_project()
        snapshot = project.current_snapshots["test_cases"]
        path = f"qa_projects/{project.project_id}/snapshots/{snapshot.snapshot_id}"
        self.store[path]["payload"]["generation_tasks"] = [
            {"source_test_case_id": "TC-FB-UNSAVED", "requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-01"], "reason": "Unfinished generation"}
        ]
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        rec = next(r for r in analyzed.current_snapshots["impact_analysis"].payload["recommendations"] if r["recommendation_id"].startswith("repair-"))
        self.assertEqual(rec["action"], "add")
        self.assertIsNone(rec.get("test_case_id"))

    def test_owner_archived_unapproved_and_stale_inputs_stop_before_generation(self):
        project = self._seed_project(modified={"REQ-003"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        path = f"qa_projects/{project.project_id}"
        before = deepcopy(self.store)
        for kind in ("owner", "archived", "unapproved", "human_review", "stale"):
            self.store.clear()
            self.store.update(deepcopy(before))
            if kind == "owner":
                self.store[path]["owner_user_id"] = "other"
            elif kind == "archived":
                self.store[path]["status"] = "archived"
            elif kind == "unapproved":
                self.store[path]["stage_state"]["use_cases"]["approved"] = False
            elif kind == "human_review":
                for item in self.store[path]["stage_state"]["use_cases"]["metadata"]["scenario_reviews"]["items"].values():
                    item["status"] = "needs_review"
            else:
                self.store[path]["stage_state"]["impact_analysis"]["stale"] = True
            with self.subTest(kind=kind), self.assertRaises((HTTPException, ProjectPermissionError, service.ImpactWorkflowError)):
                apply_project_impact_update(
                    project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision
                )
        self.generator.assert_not_called()

    def test_added_requirement_gets_model_steps_not_impact_template(self):
        project = self._seed_project()
        requirements = _requirements() + [{"id": "REQ-011", "text": "Require payment approval", "review_status": "Approved"}]
        for stage, payload in [("requirements", {"requirements": requirements}), ("use_cases", {"coverage_plan": _coverage_plan(requirements)})]:
            append_stage_snapshot(
                project_id=project.project_id, stage=stage, payload=payload, operation="seed", actor=self.actor, request_id=stage, approved=True
            )
        state = self.store[f"qa_projects/{project.project_id}"]["stage_state"]["use_cases"]
        for item in state["metadata"]["scenario_reviews"]["items"].values():
            item["status"] = "approved"
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        updated = apply_project_impact_update(
            project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision
        )
        added = [r for r in updated.current_snapshots["test_cases"].payload["test_cases"] if "REQ-011" in r["linked_requirement_ids"]]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["steps"][0]["expected"], "Payment status is Pending approval")
        self.assertFalse(added[0]["id"].startswith("TC-IMPACT"))
        self.assertEqual(self.generator.call_count, 1)
        self.assertEqual([r.id for r in self.generator.call_args.args[0].requirements], ["REQ-011"])
        self.assertFalse(updated.current_snapshots["test_cases"].payload["review"]["approved"])


if __name__ == "__main__":
    unittest.main()
