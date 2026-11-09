from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import ContextVar
from datetime import timedelta
import json
from threading import Event
import sys
import unittest
from typing import Any
from copy import deepcopy
from app.services.workflow_project_service import ProjectConflictError, ProjectPermissionError, update_project
from app.services.test_case_quality import case_source_hash, placeholder_reason
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from test_use_case_review_service import FakeFirestoreClient, _run_transaction

from app.models import AuthUser, EnrichInput, TestCase, TestStep
from app.config import GenerationSettings, Settings
from copy import deepcopy
from fastapi import HTTPException
from app.services import impact_update_service as service
from app.services import test_case_suite_review as suite_review
from app.services.workflow_project_service import ProjectConflictError, ProjectPermissionError, update_project
from app.services.impact_update_service import analyze_project_impact, apply_project_impact_update
from app.services.guidance_service import current_guidance
from app.services.impact_application_state import ApplicationLease, now
from app.services.orchestrator_service import get_project_orchestrator_status
from app.services.requirement_scope import approved_generation_requirements
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
        self.model_settings = Settings(gemini_api_key="synthetic", model_name="test-model")
        settings_patch = patch.object(service, "get_settings", return_value=self.model_settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        for target in ("enforce_billing_access", "start_workflow_run", "complete_workflow_run", "record_billing_consumption"):
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
            "substantive_assessment": {"status": "assessed_complete", "input_hash": "synthetic-review"},
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

    def _analyzed(self):
        project = self._seed_project(modified={"REQ-003"})
        return analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")

    def _apply(self, project, **overrides):
        return apply_project_impact_update(
            **{
                "project_id": project.project_id,
                "actor": self.actor,
                "request_id": "apply",
                "analysis_snapshot_id": project.current_snapshots["impact_analysis"].snapshot_id,
                "base_project_revision": project.current_revision,
                **overrides,
            }
        )

    def test_replay_returns_durable_receipt_without_versions_generation_or_usage(self):
        project = self._analyzed()
        first = self._apply(project)
        before = deepcopy(self.store)
        calls = self.generator.call_count
        second = self._apply(project, request_id="different-tab")
        self.assertEqual(first.impact_application, second.impact_application)
        self.assertEqual(first.impact_application.status, "applied")
        self.assertEqual(first.impact_application.changed_test_case_ids, ["TC-003"])
        self.assertEqual(first.impact_application.result_snapshot_id, first.current_snapshots["test_cases"].snapshot_id)
        self.assertEqual(self.generator.call_count, calls)
        self.assertNotIn("max_parallel_workers", self.generator.call_args.kwargs)
        self.assertEqual(self.store, before)
        self.assertEqual(len([k for k in self.store if k.startswith("usage_events/")]), 1)

    def test_concurrent_request_joins_active_lease(self):
        project = self._analyzed()
        joined = []

        def generate(payload, **kwargs):
            joined.append(self._apply(project, request_id="second-tab"))
            return self._generate(payload)

        self.generator.side_effect = generate
        result = self._apply(project)
        self.assertEqual(joined[0].impact_application.status, "applying")
        self.assertEqual(result.impact_application.status, "applied")
        self.assertEqual(self.generator.call_count, 1)

    def test_parallel_replacements_are_bounded_and_commit_in_selection_order(self):
        requirement_ids = [f"REQ-{index:03d}" for index in range(1, 5)]
        baseline = self._seed_project(modified=set(requirement_ids))
        project = analyze_project_impact(project_id=baseline.project_id, actor=self.actor, request_id="analysis")
        existing = project.current_snapshots["test_cases"]
        entered = {requirement_id: Event() for requirement_id in requirement_ids}
        release = {requirement_id: Event() for requirement_id in requirement_ids}
        observed = {}
        trace = ContextVar("test_parallel_trace", default="")

        def generate(payload, **kwargs):
            requirement_id = payload.requirements[0].id
            observed[requirement_id] = (current_guidance(), trace.get(), kwargs)
            entered[requirement_id].set()
            if not release[requirement_id].wait(5):
                raise AssertionError(f"Test did not release {requirement_id}")
            result = self._generate(payload)
            case = result["test_cases"][0]
            case["title"] = f"Replacement for {requirement_id}"
            result["test_cases"].append({**case, "id": f"EXTRA-{requirement_id}", "title": f"Additional check for {requirement_id}"})
            return result

        def apply():
            trace.set("caller-trace")
            return self._apply(project)

        self.generator.side_effect = generate
        settings = GenerationSettings(parallel_test_case_max_workers=20)
        with patch.object(service, "get_generation_settings", return_value=settings), ThreadPoolExecutor(max_workers=1) as runner:
            result = runner.submit(apply)
            try:
                self.assertTrue(all(entered[requirement_id].wait(5) for requirement_id in requirement_ids[:3]))
                self.assertFalse(entered["REQ-004"].is_set())
                release["REQ-002"].set()
                self.assertTrue(entered["REQ-004"].wait(5))
                self.assertFalse(release["REQ-001"].is_set())
                joined = self._apply(project, request_id="duplicate")
                self.assertEqual(joined.impact_application.status, "applying")
                self.assertEqual(joined.current_snapshots["test_cases"], existing)
            finally:
                for event in release.values():
                    event.set()
            updated = result.result(timeout=5)

        self.assertEqual(self.generator.call_count, 4)
        self.assertEqual(updated.current_revision, project.current_revision + 1)
        snapshot = updated.current_snapshots["test_cases"]
        self.assertFalse(snapshot.approved)
        self.assertEqual(snapshot.metadata["targeted_generation_worker_count"], 3)
        saved_rows = snapshot.payload["test_cases"]
        for index, requirement_id in enumerate(requirement_ids):
            manifest, trace_id, kwargs = observed[requirement_id]
            self.assertEqual(trace_id, "caller-trace")
            self.assertEqual(kwargs["max_parallel_workers"], 1)
            self.assertEqual(manifest.project_inputs.requirements_snapshot_id, project.current_snapshots["requirements"].snapshot_id)
            row, old = saved_rows[index], existing.payload["test_cases"][index]
            self.assertEqual((row["id"], row["artifact_item_id"]), (old["id"], old["artifact_item_id"]))
            self.assertEqual(row["artifact_version_number"], old["artifact_version_number"] + 1)
            self.assertEqual(row["title"], f"Replacement for {requirement_id}")
            evidence = snapshot.metadata["targeted_generation_runs"][index]
            self.assertEqual(evidence["recommendation_id"], f"impact-update-{old['id']}")
            self.assertEqual(evidence["source_test_case_id"], old["id"])
            self.assertEqual(evidence["source_project_revision"], project.current_revision)
            self.assertEqual(evidence["scenario_refs"], [f"{requirement_id}-SCN-01"])
            self.assertEqual(evidence["guidance"]["manifest_id"], manifest.manifest_id)
        self.assertEqual(saved_rows[4:10], existing.payload["test_cases"][4:])
        self.assertEqual([row["title"] for row in saved_rows[10:]], [f"Additional check for {requirement_id}" for requirement_id in requirement_ids])
        self.assertEqual(len([key for key in self.store if key.startswith("usage_events/")]), 1)

    def test_parallel_failure_drains_active_workers_and_does_not_start_remaining_work(self):
        requirement_ids = [f"REQ-{index:03d}" for index in range(1, 5)]
        baseline = self._seed_project(modified=set(requirement_ids))
        project = analyze_project_impact(project_id=baseline.project_id, actor=self.actor, request_id="analysis")
        before = deepcopy(self.store)
        entered = {requirement_id: Event() for requirement_id in requirement_ids}
        release_bad = Event()
        release_other = Event()
        failed_worker = Event()

        def generate(payload, **kwargs):
            requirement_id = payload.requirements[0].id
            entered[requirement_id].set()
            result = self._generate(payload)
            if requirement_id == "REQ-002":
                if not release_bad.wait(5):
                    raise AssertionError("Test did not release the failing worker")
                result["test_cases"][0]["scenario_refs"] = ["REQ-001-SCN-01"]
            elif not release_other.wait(5):
                raise AssertionError("Test did not release the other workers")
            return result

        def observe_wait(*args, **kwargs):
            done, pending = wait(*args, **kwargs)
            if any(future.exception() is not None for future in done):
                failed_worker.set()
            return done, pending

        self.generator.side_effect = generate
        with (
            patch.object(service, "get_generation_settings", return_value=GenerationSettings()),
            patch.object(service, "wait", side_effect=observe_wait),
            ThreadPoolExecutor(max_workers=1) as runner,
        ):
            result = runner.submit(self._apply, project)
            try:
                self.assertTrue(all(entered[requirement_id].wait(5) for requirement_id in requirement_ids[:3]))
                release_bad.set()
                self.assertTrue(failed_worker.wait(5))
                self.assertFalse(result.done())
                self.assertEqual(self._apply(project, request_id="duplicate").impact_application.status, "applying")
            finally:
                release_bad.set()
                release_other.set()
            with self.assertRaisesRegex(service.ImpactWorkflowError, "failed quality checks"):
                result.result(timeout=5)

        self.assertFalse(entered["REQ-004"].is_set())
        self.assertEqual(self.generator.call_count, 3)
        checkpoint_path = f"qa_projects/{project.project_id}/impact_recovery/current"
        self.assertEqual(len(self.store[checkpoint_path]["items"]), 2)
        self.assertEqual({key: value for key, value in self.store.items() if "/impact_applications/" not in key and key != checkpoint_path}, before)
        failed = get_project(project.project_id, actor=self.actor)
        self.assertEqual(failed.impact_application.status, "failed")
        self.assertEqual(failed.current_snapshots["test_cases"], project.current_snapshots["test_cases"])
        self.assertNotIn("checkpoints", failed.model_dump_json())
        self.generator.side_effect = self._generate
        with patch.object(service, "get_generation_settings", return_value=GenerationSettings()):
            repaired = self._apply(project, request_id="retry")
        self.assertEqual(self.generator.call_count, 5)
        self.assertEqual(
            {call.args[0].requirements[0].id for call in self.generator.call_args_list[3:]},
            {"REQ-002", "REQ-004"},
        )
        self.assertNotIn(checkpoint_path, self.store)
        self.assertEqual(repaired.impact_application.status, "applied")
        self.assertEqual(repaired.current_revision, project.current_revision + 1)
        self.assertEqual(repaired.current_snapshots["test_cases"].metadata["targeted_generation_reused_count"], 2)
        self.assertEqual(len([key for key in self.store if key.startswith("usage_events/")]), 1)

    def test_expired_worker_cannot_commit_after_retry_takes_lease(self):
        project = self._analyzed()
        aid = project.current_snapshots["impact_analysis"].snapshot_id
        selected = [r["recommendation_id"] for r in project.current_snapshots["impact_analysis"].payload["recommendations"] if r["accepted"]]
        old = ApplicationLease(project.project_id, aid, self.actor, project.current_revision, selected)
        self.assertTrue(old.reserve())
        path = f"qa_projects/{project.project_id}/impact_applications/{aid}"
        self.store[path]["lease_expires_at"] = now() - timedelta(seconds=1)
        self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "failed")
        retry = ApplicationLease(project.project_id, aid, self.actor, project.current_revision, selected)
        self.assertTrue(retry.reserve())
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            old.checkpoint("late", "should never be saved")
        retry.fail()
        applied = self._apply(project)
        before = deepcopy(self.store)
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            old.commit(self.transaction_client.transaction(), {})
        old.fail()
        self.assertEqual(self.store, before)
        self.assertEqual(applied.impact_application.status, "applied")

    def test_lost_commit_response_reconciles_as_applied_and_does_not_duplicate_usage(self):
        project = self._analyzed()
        real_append = service.append_stage_snapshot

        def lost_response(**kwargs):
            real_append(**kwargs)
            raise RuntimeError("Response lost after commit")

        with patch.object(service, "append_stage_snapshot", side_effect=lost_response):
            with self.assertRaisesRegex(RuntimeError, "Response lost"):
                self._apply(project)
        self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "applied")
        replay = self._apply(project)
        self.assertEqual(replay.impact_application.status, "applied")
        self.assertEqual(self.generator.call_count, 1)
        self.assertEqual(len([k for k in self.store if k.startswith("usage_events/")]), 1)

    def test_failed_attempt_retries_original_selection_and_new_analysis_resets_state(self):
        project = self._analyzed()
        self.generator.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            self._apply(project)
        self.generator.side_effect = self._generate
        updated = self._apply(project)
        self.assertEqual(updated.impact_application.status, "applied")
        new_analysis = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="new-analysis")
        self.assertIsNone(new_analysis.impact_application)
        with self.assertRaisesRegex(service.ImpactWorkflowError, "analysis changed"):
            self._apply(project)

    def test_historical_completion_requires_lineage_and_valid_applied_ids(self):
        project = self._analyzed()
        updated = self._apply(project)
        aid = project.current_snapshots["impact_analysis"].snapshot_id
        del self.store[f"qa_projects/{project.project_id}/impact_applications/{aid}"]
        self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "applied")
        result_path = f"qa_projects/{project.project_id}/snapshots/{updated.current_snapshots['test_cases'].snapshot_id}"
        self.store[result_path]["payload"]["impact_update_result"]["applied_recommendation_ids"] = ["unknown"]
        self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "verification_required")

    def test_project_read_does_not_pair_new_receipt_with_old_suite(self):
        from app.services import workflow_project_service as projects

        project = self._analyzed()
        original = deepcopy(self.store[f"qa_projects/{project.project_id}"])
        applied = self._apply(project)
        current = deepcopy(self.store[f"qa_projects/{project.project_id}"])
        with patch.object(projects, "_get_project_payload", side_effect=[original, current]):
            loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(loaded.current_revision, applied.current_revision)
        self.assertEqual(loaded.impact_application.result_snapshot_id, loaded.current_snapshots["test_cases"].snapshot_id)

    def test_partial_selection_consumes_analysis_and_keeps_other_cases_unchanged(self):
        project = self._analyzed()
        recs = project.current_snapshots["impact_analysis"].payload["recommendations"]
        selected = [r["recommendation_id"] for r in recs if r["action"] == "update"]
        first = self._apply(project, accepted_recommendation_ids=selected)
        second = self._apply(first, accepted_recommendation_ids=[r["recommendation_id"] for r in recs])
        self.assertEqual(second.impact_application.accepted_recommendation_ids, selected)
        self.assertEqual(first.current_revision, second.current_revision)
        self.assertEqual(self.generator.call_count, 1)

    def test_lease_renewal_cannot_resurrect_expired_worker(self):
        project = self._analyzed()
        aid = project.current_snapshots["impact_analysis"].snapshot_id
        lease = ApplicationLease(project.project_id, aid, self.actor, project.current_revision, ["selection"])
        self.assertTrue(lease.reserve())
        path = f"qa_projects/{project.project_id}/impact_applications/{aid}"
        short = now() + timedelta(seconds=2)
        self.store[path]["lease_expires_at"] = short
        lease.renew()
        self.assertGreater(self.store[path]["lease_expires_at"], short)
        self.store[path]["lease_expires_at"] = now() - timedelta(seconds=1)
        with self.assertRaises(RuntimeError):
            lease.renew()

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
        self.assertEqual({k: v for k, v in self.store.items() if "/impact_applications/" not in k}, before)
        self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "failed")

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
            self.assertEqual({k: v for k, v in self.store.items() if "/impact_applications/" not in k}, before)
            self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "failed")

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
        self.assertNotIn(f"qa_projects/{project.project_id}/impact_recovery/current", self.store)

    def test_commit_failure_keeps_only_private_results_and_expired_retry_reuses_them(self):
        project = self._seed_project(modified={"REQ-003"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        before = deepcopy(self.store)
        from test_use_case_review_service import FakeTransaction

        with patch.object(self.transaction_client, "transaction", side_effect=lambda: FakeTransaction(self.store, fail_on_create_segment="snapshots")):
            with self.assertRaises(RuntimeError):
                apply_project_impact_update(
                    project_id=project.project_id, actor=self.actor, request_id="apply", base_project_revision=analyzed.current_revision
                )
        checkpoint_path = f"qa_projects/{project.project_id}/impact_recovery/current"
        self.assertEqual({k: v for k, v in self.store.items() if "/impact_applications/" not in k and k != checkpoint_path}, before)
        self.assertEqual(len(self.store[checkpoint_path]["items"]), 1)
        self.assertEqual(get_project(project.project_id, actor=self.actor).impact_application.status, "failed")
        aid = analyzed.current_snapshots["impact_analysis"].snapshot_id
        receipt = self.store[f"qa_projects/{project.project_id}/impact_applications/{aid}"]
        receipt.update(status="applying", lease_expires_at=now() - timedelta(seconds=1))

        applied = self._apply(analyzed, request_id="expired-retry")
        self.assertEqual(applied.impact_application.status, "applied")
        self.assertEqual(self.generator.call_count, 1)
        self.assertEqual(applied.current_snapshots["test_cases"].metadata["targeted_generation_reused_count"], 1)
        self.assertEqual(applied.current_snapshots["test_cases"].metadata["targeted_generation_worker_count"], 0)
        self.assertNotIn(checkpoint_path, self.store)

    def test_retry_regenerates_expired_invalid_and_policy_mismatched_results(self):
        prepare = service.prepare_run
        for cause in ("expired", "malformed", "scope", "model", "guidance", "source_revision"):
            with self.subTest(cause=cause):
                self.generator.reset_mock()
                self.model_settings.model_name = "test-model"
                project = self._analyzed()
                path = f"qa_projects/{project.project_id}/impact_recovery/current"
                with patch.object(service, "append_stage_snapshot", side_effect=RuntimeError("Synthetic commit interruption")):
                    with self.assertRaisesRegex(RuntimeError, "commit interruption"):
                        self._apply(project)
                saved = self.store[path]
                key, payload = next(iter(saved["items"].items()))
                if cause == "expired":
                    saved["expires_at"] = now() - timedelta(seconds=1)
                elif cause == "malformed":
                    saved["items"][key] = "{"
                elif cause == "scope":
                    changed = json.loads(payload)
                    changed["test_cases"][0]["scenario_refs"] = ["unapproved-scenario"]
                    saved["items"][key] = json.dumps(changed)
                elif cause == "model":
                    self.model_settings.model_name = "changed-model"
                elif cause == "source_revision":
                    project = update_project(
                        project_id=project.project_id,
                        actor=self.actor,
                        request_id="revision-changed",
                        name="Revision changed",
                        base_project_revision=project.current_revision,
                    )

                def prepare_retry(*args, **kwargs):
                    manifest = prepare(*args, **kwargs)
                    return manifest.model_copy(update={"manifest_id": "changed-guidance"}) if cause == "guidance" else manifest

                with patch.object(service, "prepare_run", side_effect=prepare_retry):
                    applied = self._apply(project, request_id="retry")
                self.assertEqual(self.generator.call_count, 2)
                self.assertEqual(applied.current_snapshots["test_cases"].metadata["targeted_generation_reused_count"], 0)
                self.assertNotIn(path, self.store)

    def test_checkpoint_size_cap_does_not_prevent_an_approved_replacement(self):
        project = self._analyzed()
        with patch("app.services.impact_application_state.MAX_RECOVERY_BYTES", 100):
            updated = self._apply(project)
        self.assertEqual(updated.impact_application.status, "applied")
        self.assertNotIn(f"qa_projects/{project.project_id}/impact_recovery/current", self.store)

    def test_substantive_repair_groups_findings_and_preserves_unselected_gaps(self):
        project = self._seed_project()
        snapshot = project.current_snapshots["test_cases"]
        path = f"qa_projects/{project.project_id}/snapshots/{snapshot.snapshot_id}"
        tasks = [
            {
                "kind": "substantive_coverage",
                "source_test_case_id": "TC-001",
                "requirement_ids": ["REQ-001"],
                "scenario_refs": ["REQ-001-SCN-01"],
                "reason": "Observe booking creation at upper boundary",
            },
            {
                "kind": "substantive_coverage",
                "source_test_case_id": "TC-001",
                "requirement_ids": ["REQ-001"],
                "scenario_refs": ["REQ-001-SCN-01"],
                "reason": "Confirm supplied user fixture",
            },
            {
                "kind": "substantive_coverage",
                "source_test_case_id": "TC-002",
                "requirement_ids": ["REQ-002"],
                "scenario_refs": ["REQ-002-SCN-01"],
                "reason": "Check durable retry identity",
            },
        ]
        self.store[path]["payload"]["generation_tasks"] = tasks
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="analysis")
        recs = [r for r in analyzed.current_snapshots["impact_analysis"].payload["recommendations"] if r["recommendation_id"].startswith("repair-")]
        self.assertEqual(len(recs), 2)
        selected = next(r for r in recs if r["test_case_id"] == "TC-001")
        result = apply_project_impact_update(
            project_id=project.project_id,
            actor=self.actor,
            request_id="apply",
            base_project_revision=analyzed.current_revision,
            accepted_recommendation_ids=[selected["recommendation_id"]],
        )
        payload = result.current_snapshots["test_cases"].payload
        self.assertEqual(payload["generation_tasks"], [tasks[2]])
        self.assertEqual(payload["substantive_assessment"]["status"], "unknown")
        evidence = result.current_snapshots["test_cases"].metadata["targeted_generation_runs"]
        self.assertEqual(evidence[0]["substantive_assessment"]["input_hash"], "synthetic-review")
        self.assertFalse(payload["review"]["approved"])
        self.assertEqual(self.generator.call_count, 1)
        self.assertIn(tasks[0]["reason"], self.generator.call_args.args[0].feedback)
        self.assertIn(tasks[1]["reason"], self.generator.call_args.args[0].feedback)
        unchanged = next(c for c in payload["test_cases"] if c["id"] == "TC-002")
        self.assertEqual(unchanged["artifact_version_number"], 1)

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

    def _suite_pending_review(self, *, pending=()):
        project = self._seed_project(omit={"REQ-005"})
        analyzed = analyze_project_impact(project_id=project.project_id, actor=self.actor, request_id="impact")
        updated = self._apply(analyzed)
        state = self.store[f"qa_projects/{project.project_id}"]["stage_state"]["use_cases"]
        state["metadata"]["latest_human_review"] = {"snapshot_id": state["current_snapshot_id"], "decision": "approve"}
        current = updated.current_snapshots["test_cases"]
        path = f"qa_projects/{updated.project_id}/snapshots/{current.snapshot_id}"
        self.store[path]["payload"]["generation_tasks"] = list(pending)
        for case in self.store[path]["payload"]["test_cases"]:
            case["preconditions"] = "Open the provisioned fixture."
            case["expected_result"] = "The recorded result is displayed."
        return get_project(updated.project_id, actor=self.actor), path

    def _review_saved_suite(self, project, snapshot_id=None):
        return suite_review.review_saved_test_cases(
            project_id=project.project_id,
            snapshot_id=snapshot_id or project.current_snapshots["test_cases"].snapshot_id,
            base_project_revision=project.current_revision,
            actor=self.actor,
            request_id="review-test",
        )

    def _generated_suite_pending_review(self):
        project = self._seed_project()
        project_path = f"qa_projects/{project.project_id}"
        use_cases = project.current_snapshots["use_cases"]
        self.store[project_path]["stage_state"]["use_cases"]["metadata"]["latest_human_review"] = {
            "snapshot_id": use_cases.snapshot_id,
            "decision": "approve",
        }
        reason = "Independent assessment provider unavailable; review is required."
        rows = deepcopy(project.current_snapshots["test_cases"].payload["test_cases"])
        for row in rows:
            row.update(preconditions="Open the provisioned fixture.", expected_result="The recorded result is displayed.")
        snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={
                "test_cases": rows,
                "approved": False,
                "coverage_plan": use_cases.payload["coverage_plan"],
                "review": {"score": 100, "threshold": 90, "blocking_issues": [reason]},
                "substantive_assessment": {"status": "unknown", "reason": reason, "diagnostics": {"failure_reason": "provider_unavailable"}},
                "workflow_diagnostics": {"status": "partial"},
                "generation_evidence": {"evidence_id": "original-generation", "final_status": "partial"},
            },
            operation="testcases.generate",
            actor=self.actor,
            request_id="original-generation",
            source_snapshot_id=use_cases.snapshot_id,
            metadata={
                "source_snapshot_ids": {stage: project.current_snapshots[stage].snapshot_id for stage in ("requirements", "use_cases")},
                "source_requirement_hashes": {row.id: case_source_hash(row.model_dump(mode="json")) for row in approved_generation_requirements(project)},
                "source_context_hash": None,
            },
            base_project_revision=project.current_revision,
        )
        return get_project(project.project_id, actor=self.actor), f"{project_path}/snapshots/{snapshot.snapshot_id}"

    def test_generated_suite_outage_can_recover_without_regeneration(self):
        project, path = self._generated_suite_pending_review()
        original = deepcopy(self.store[path])
        self.assertTrue(project.current_snapshots["test_cases"].metadata["review_retry_available"])
        applied_inputs = []

        def critic(cases, requirements, context, **kwargs):
            applied_inputs.append(current_guidance().project_inputs)
            self.assertEqual(cases, [TestCase.model_validate(row).model_dump(mode="json") for row in original["payload"]["test_cases"]])
            self.assertIsNone(context)
            return {"status": "assessed_complete"}

        with (
            patch.object(suite_review, "start_workflow_run", return_value="review-run"),
            patch.object(suite_review, "complete_workflow_run"),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", return_value={"status": "unknown"}) as review,
        ):
            with self.assertRaises(HTTPException) as unavailable:
                self._review_saved_suite(project)
            self.assertEqual(unavailable.exception.status_code, 503)
            self.assertEqual(get_project(project.project_id, actor=self.actor).current_revision, project.current_revision)
            review.side_effect = critic
            approved = self._review_saved_suite(project)
            self.assertEqual(self._review_saved_suite(project).current_revision, approved.current_revision)
            self.assertEqual(review.call_count, 2)

        snapshot = approved.current_snapshots["test_cases"]
        self.assertTrue(snapshot.approved)
        self.assertEqual(snapshot.source_snapshot_id, original["snapshot_id"])
        self.assertEqual(snapshot.payload["test_cases"], original["payload"]["test_cases"])
        self.assertEqual(snapshot.payload["generation_evidence"], original["payload"]["generation_evidence"])
        self.assertEqual(applied_inputs[0].requirements_snapshot_id, original["metadata"]["source_snapshot_ids"]["requirements"])
        self.assertNotIn("review_retry_available", snapshot.metadata)
        self.assertEqual(self.store[path], original)
        self.assertFalse(any(key.startswith(("test_case_sets/", "usage_events/")) for key in self.store))
        self.generator.assert_not_called()

    def test_generated_suite_retry_rejects_generation_failures_and_changed_source_bindings(self):
        project, path = self._generated_suite_pending_review()
        original = deepcopy(self.store[path])
        for cause in ("generation_failed", "fallback", "tasks", "review_gap", "invalid_review", "lineage", "requirements", "context", "project_inputs"):
            self.store[path] = deepcopy(original)
            saved = self.store[path]
            payload = saved["payload"]
            if cause == "generation_failed":
                payload["workflow_diagnostics"]["failed_shard_count"] = 1
            elif cause == "fallback":
                payload["workflow_diagnostics"]["used_fallback"] = True
            elif cause == "tasks":
                payload["generation_tasks"] = [{"requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-01"], "reason": "Unfinished task"}]
            elif cause == "review_gap":
                payload["review"]["blocking_issues"].append("Structural review found another blocker.")
            elif cause == "invalid_review":
                payload["substantive_assessment"]["diagnostics"]["failure_reason"] = "invalid_evidence"
            elif cause == "lineage":
                saved["metadata"]["source_snapshot_ids"]["use_cases"] = "different-plan"
            elif cause == "requirements":
                saved["metadata"]["source_requirement_hashes"]["REQ-001"] = "different-content"
            elif cause == "context":
                saved["metadata"]["source_context_hash"] = "different-context"
            else:
                saved["metadata"]["guidance"] = {"project_inputs": {"revision": -1}}
            with self.subTest(cause=cause), patch.object(suite_review, "assess_delivered_suite") as critic:
                with self.assertRaises(HTTPException) as rejected:
                    self._review_saved_suite(project)
                self.assertEqual(rejected.exception.status_code, 409)
                critic.assert_not_called()
                current = get_project(project.project_id, actor=self.actor)
                self.assertEqual(current.current_revision, project.current_revision)
                if cause in {"generation_failed", "fallback", "tasks", "review_gap", "invalid_review"}:
                    self.assertNotIn("review_retry_available", current.current_snapshots["test_cases"].metadata)

    def test_generated_suite_retry_saves_real_review_findings_without_approval(self):
        project, _ = self._generated_suite_pending_review()
        original = project.current_snapshots["test_cases"]
        finding = {
            "status": "incomplete",
            "reason": "The saved payment outcome was not verified.",
            "obligations": [{"requirement_id": "REQ-001", "status": "unmet", "obligation": "Verify saved payment", "reason": "No saved result"}],
        }
        with (
            patch.object(suite_review, "start_workflow_run", return_value="review-run"),
            patch.object(suite_review, "complete_workflow_run"),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", return_value=finding),
        ):
            reviewed = self._review_saved_suite(project)
        snapshot = reviewed.current_snapshots["test_cases"]
        self.assertFalse(snapshot.approved)
        self.assertEqual(snapshot.payload["test_cases"], original.payload["test_cases"])
        self.assertEqual(snapshot.payload["substantive_assessment"], finding)
        self.assertIn("Verify saved payment", snapshot.payload["generation_tasks"][0]["reason"])
        self.assertNotIn("review_retry_available", snapshot.metadata)

    def test_generated_suite_retry_reconstructs_selected_workbench_context(self):
        project, _ = self._generated_suite_pending_review()
        suite = project.current_snapshots["test_cases"]
        context = EnrichInput.model_validate(
            {
                "requirements": approved_generation_requirements(project),
                "diagram_links": [],
                "image_links": [],
                "grounded_context": {
                    "artifact_sources": [{"id": "kept", "label": "Selected"}, {"id": "removed", "label": "Omitted"}],
                    "ui_elements": [
                        {"id": "1", "source_id": "kept", "name": "Book", "description": "Submit"},
                        {"id": "2", "source_id": "removed", "name": "Admin", "description": "Open settings"},
                    ],
                },
            }
        )
        expected = context.model_copy(
            update={
                "project_id": suite.project_id,
                "base_project_revision": suite.project_revision - 1,
                "diagram_links": None,
                "image_links": None,
                "grounded_context": context.grounded_context.model_copy(
                    update={"artifact_sources": context.grounded_context.artifact_sources[:1], "ui_elements": context.grounded_context.ui_elements[:1]}
                ),
            }
        )
        suite.metadata.update(source_context_hash=case_source_hash(expected.model_dump(mode="json")), source_context_artifact_ids=["kept"])
        self.assertEqual(suite_review._generated_review_context(suite, context), expected)
        suite.metadata.pop("source_context_artifact_ids")
        with self.assertRaises(HTTPException) as mismatch:
            suite_review._generated_review_context(suite, context)
        self.assertEqual(mismatch.exception.status_code, 409)

    def test_saved_suite_review_preserves_exact_cases_and_can_reopen_approval(self):
        project, path = self._suite_pending_review()
        saved = deepcopy(self.store[path])
        self.assertTrue(any(case["status"] == "Deprecated" for case in saved["payload"]["test_cases"]))
        artifacts = {key: deepcopy(value) for key, value in self.store.items() if key.startswith("test_case_sets/")}
        assessment = {"status": "assessed_complete", "design_status": "assessed_complete", "input_hash": "exact-test-review"}
        with (
            patch.object(suite_review, "start_workflow_run", return_value="review-run"),
            patch.object(suite_review, "complete_workflow_run"),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", return_value=assessment) as critic,
        ):
            updated = self._review_saved_suite(project)
            replay = self._review_saved_suite(project)
        self.assertEqual(replay.current_revision, updated.current_revision)
        self.assertEqual(critic.call_count, 1)
        tested_cases, reviewed_requirements, _ = critic.call_args.args
        self.assertEqual({case["id"] for case in tested_cases}, {case["id"] for case in saved["payload"]["test_cases"] if case["status"] != "Deprecated"})
        self.assertNotIn("REQ-005", {requirement.id for requirement in reviewed_requirements})
        snapshot = updated.current_snapshots["test_cases"]
        self.assertTrue(snapshot.approved)
        self.assertTrue(snapshot.payload["review"]["approved"])
        self.assertEqual(snapshot.source_snapshot_id, saved["snapshot_id"])
        self.assertEqual(snapshot.payload["test_cases"], saved["payload"]["test_cases"])
        self.assertEqual(snapshot.metadata["source_use_case_snapshot_id"], updated.current_snapshots["use_cases"].snapshot_id)
        self.assertEqual(self.store[path], saved)
        self.assertEqual({key: value for key, value in self.store.items() if key.startswith("test_case_sets/")}, artifacts)

    def test_saved_suite_review_keeps_required_work_and_independent_findings(self):
        task = {"requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-01"], "reason": "Unfinished generation work"}
        project, _ = self._suite_pending_review(pending=[task])
        assessment = {
            "status": "incomplete",
            "design_status": "incomplete",
            "reason": "Independent review found missing saved-state coverage.",
            "obligations": [{"requirement_id": "REQ-002", "status": "unmet", "obligation": "Verify saved value", "reason": "No result was checked"}],
        }
        with (
            patch.object(suite_review, "start_workflow_run", return_value="review-run"),
            patch.object(suite_review, "complete_workflow_run"),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", return_value=assessment),
        ):
            updated = self._review_saved_suite(project)
        snapshot = updated.current_snapshots["test_cases"]
        self.assertFalse(snapshot.approved)
        self.assertEqual(snapshot.payload["generation_tasks"][0], task)
        self.assertIn("Verify saved value", snapshot.payload["generation_tasks"][1]["reason"])
        self.assertFalse(updated.stage_state["test_cases"].approved)

    def test_saved_suite_review_rejects_stale_and_unavailable_results_without_publishing(self):
        project, path = self._suite_pending_review()
        saved = deepcopy(self.store[path])
        with (
            patch.object(suite_review, "start_workflow_run", return_value="review-run"),
            patch.object(suite_review, "complete_workflow_run"),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", return_value={"status": "unknown"}) as critic,
        ):
            with self.assertRaises(HTTPException) as wrong:
                self._review_saved_suite(project, "older-suite")
            self.assertEqual(wrong.exception.status_code, 409)
            critic.assert_not_called()
            with self.assertRaises(HTTPException) as unavailable:
                self._review_saved_suite(project)
            self.assertEqual(unavailable.exception.status_code, 503)
            self.assertEqual(get_project(project.project_id, actor=self.actor).current_revision, project.current_revision)
            critic.return_value = {"status": "unknown", "diagnostics": {"failure_reason": "review_input_too_large"}}
            with self.assertRaises(HTTPException) as oversized:
                self._review_saved_suite(project)
            self.assertEqual(oversized.exception.status_code, 422)
            self.assertEqual(get_project(project.project_id, actor=self.actor).current_revision, project.current_revision)

            def concurrent_review(*args, **kwargs):
                update_project(
                    project_id=project.project_id, actor=self.actor, request_id="other", name="Another revision", base_project_revision=project.current_revision
                )
                return {"status": "assessed_complete"}

            critic.side_effect = concurrent_review
            with self.assertRaises(ProjectConflictError):
                self._review_saved_suite(project)
        self.assertEqual(self.store[path], saved)
        self.assertEqual(get_project(project.project_id, actor=self.actor).current_snapshots["test_cases"].snapshot_id, saved["snapshot_id"])

    def test_corrected_suite_can_be_reviewed_after_current_use_cases_are_approved(self):
        project, _ = self._suite_pending_review()
        context = append_stage_snapshot(
            project_id=project.project_id,
            stage="context",
            payload={"notes": "The test fixture has been provisioned."},
            operation="quality.feedback.apply",
            actor=self.actor,
            request_id="correction",
        )
        corrected = append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload=project.current_snapshots["test_cases"].payload,
            operation="quality.feedback.apply",
            actor=self.actor,
            request_id="correction",
            metadata={"source_context_snapshot_id": context.snapshot_id},
        )
        with patch.object(suite_review, "assess_delivered_suite") as critic:
            with self.assertRaises(HTTPException) as upstream:
                self._review_saved_suite(get_project(project.project_id, actor=self.actor))
            self.assertEqual(upstream.exception.status_code, 409)
            critic.assert_not_called()
        plan = append_stage_snapshot(
            project_id=project.project_id,
            stage="use_cases",
            payload=project.current_snapshots["use_cases"].payload,
            operation="use_cases.generate",
            actor=self.actor,
            request_id="new-plan",
            approved=True,
        )
        state = self.store[f"qa_projects/{project.project_id}"]["stage_state"]["use_cases"]
        state["metadata"]["latest_human_review"] = {"snapshot_id": plan.snapshot_id, "decision": "approve"}
        pending = get_project(project.project_id, actor=self.actor)
        self.assertTrue(pending.stage_state["test_cases"].stale)
        with (
            patch.object(suite_review, "start_workflow_run", return_value="review-run"),
            patch.object(suite_review, "complete_workflow_run"),
            patch.object(suite_review, "get_settings", return_value=object()),
            patch.object(suite_review, "assess_delivered_suite", return_value={"status": "assessed_complete"}) as critic,
        ):
            updated = self._review_saved_suite(pending)
        self.assertEqual(critic.call_args.args[2].notes, "The test fixture has been provisioned.")
        self.assertTrue(updated.stage_state["test_cases"].approved)
        self.assertFalse(updated.stage_state["test_cases"].stale)
        self.assertEqual(updated.current_snapshots["test_cases"].payload["test_cases"], corrected.payload["test_cases"])

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
