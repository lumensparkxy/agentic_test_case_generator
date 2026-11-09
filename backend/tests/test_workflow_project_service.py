from base64 import b64encode
from copy import deepcopy
from pathlib import Path
from random import Random
import sys
import unittest
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from test_use_case_review_service import FakeFirestoreClient, _run_transaction

from app.models import AuthUser
from app.routers.projects import get_current_user, router
from app.services.workflow_project_service import (
    ProjectConflictError,
    append_stage_snapshot,
    create_project,
    get_project,
    get_project_execution_run,
    get_project_stage_snapshot,
    list_projects,
    record_execution_run,
)


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


class WorkflowProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.collection = FakeCollection("qa_projects", self.store)
        self.transaction_client = FakeFirestoreClient(self.store)
        for target, kwargs in [
            ("app.services.workflow_project_service.get_required_firestore_client", {"return_value": self.transaction_client}),
            ("app.services.workflow_project_service.transactional", {"side_effect": _run_transaction}),
        ]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        self.collection_patch = patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=self.collection,
        )
        self.collection_patch.start()

    def tearDown(self) -> None:
        self.collection_patch.stop()

    def test_testing_intent_round_trips_separately_from_saved_sources(self) -> None:
        project = create_project(name="Booking", description="Project description", actor=self.actor, request_id="create")
        append_stage_snapshot(
            project_id=project.project_id,
            stage="context",
            payload={"notes": "Source facts"},
            operation="requirements.enrich",
            actor=self.actor,
            request_id="context",
            approved=True,
        )
        baseline = get_project(project.project_id, actor=self.actor)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        path = f"/projects/{project.project_id}"
        with TestClient(app) as client:
            self.assertIsNone(client.get(path).json()["testing_intent"])
            intent = {"objective": "Check high-risk booking changes", "risk_emphasis": "edge_cases"}
            response = client.patch(path, json={"base_project_revision": baseline.current_revision, "testing_intent": intent})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["testing_intent"], intent)
            self.assertEqual(response.json()["description"], "Project description")
            after = get_project(project.project_id, actor=self.actor)
            self.assertEqual(after.current_revision, baseline.current_revision + 1)
            self.assertEqual(after.input_revision, baseline.input_revision)
            self.assertEqual(after.current_snapshots, baseline.current_snapshots)
            self.assertEqual(after.stage_state, baseline.stage_state)

            renamed = client.patch(path, json={"name": "Renamed booking"})
            self.assertEqual(renamed.status_code, 200, renamed.text)
            self.assertEqual(renamed.json()["testing_intent"], intent)
            reset = client.patch(path, json={"base_project_revision": renamed.json()["current_revision"], "testing_intent": {"objective": ""}})
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual(client.get(path).json()["testing_intent"], {"objective": "", "risk_emphasis": "balanced"})

    def test_testing_intent_requires_revision_owner_and_active_project(self) -> None:
        project = create_project(name="Booking", description=None, actor=self.actor, request_id="create")
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        path = f"/projects/{project.project_id}"
        intent = {"objective": "Check state transitions", "risk_emphasis": "regression"}
        valid = {"base_project_revision": project.current_revision, "testing_intent": intent}
        before = deepcopy(self.store)
        with TestClient(app) as client:
            for payload, expected in (
                ({"testing_intent": intent}, 422),
                ({**valid, "base_project_revision": project.current_revision - 1}, 409),
                ({**valid, "testing_intent": {**intent, "objective": "a" * 501}}, 422),
                ({**valid, "testing_intent": {**intent, "risk_emphasis": "unknown"}}, 422),
            ):
                with self.subTest(payload=payload):
                    self.assertEqual(client.patch(path, json=payload).status_code, expected)
                    self.assertEqual(self.store, before)

            app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="outsider", name="Outsider")
            self.assertEqual(client.patch(path, json=valid).status_code, 403)
            self.assertEqual(self.store, before)
            app.dependency_overrides[get_current_user] = lambda: self.actor
            archived = client.patch(path, json={"status": "archived"})
            self.assertEqual(archived.status_code, 200, archived.text)
            before = deepcopy(self.store)
            self.assertEqual(client.patch(path, json={**valid, "base_project_revision": archived.json()["current_revision"]}).status_code, 409)
            self.assertEqual(self.store, before)

    def test_create_list_and_load_project(self) -> None:
        project = create_project(name="Checkout QA", description="Regression scope", actor=self.actor, request_id="req-1")

        self.assertEqual(project.name, "Checkout QA")
        self.assertEqual(project.current_revision, 1)
        self.assertEqual(project.timeline[0].event_type, "project.created")

        projects = list_projects(actor=self.actor)
        self.assertEqual([item.project_id for item in projects], [project.project_id])

        loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(loaded.description, "Regression scope")

    def test_use_case_regeneration_carries_only_unchanged_reviewed_rows(self):
        project = create_project(name="Review carry", description=None, actor=self.actor, request_id="create")
        payload = {"coverage_plan": [{"requirement_id": "R1", "scenarios": [{"id": "S1", "title": "Original"}]}]}
        first = append_stage_snapshot(
            project_id=project.project_id, stage="use_cases", payload=payload, operation="generate", actor=self.actor, request_id="first"
        )
        metadata = self.store[f"qa_projects/{project.project_id}"]["stage_state"]["use_cases"]["metadata"]
        metadata["scenario_reviews"]["items"]['["R1","S1"]'].update(status="approved", reviewer_user_id="user-1")
        second = append_stage_snapshot(
            project_id=project.project_id, stage="use_cases", payload=payload, operation="generate", actor=self.actor, request_id="second"
        )
        state = get_project(project.project_id, actor=self.actor).stage_state["use_cases"]
        item = state.metadata["scenario_reviews"]["items"]['["R1","S1"]']
        self.assertEqual(item["status"], "approved")
        self.assertEqual(item["carried_from_snapshot_id"], first.snapshot_id)
        self.assertEqual(state.metadata["scenario_reviews"]["snapshot_id"], second.snapshot_id)
        self.assertFalse(state.approved)
        changed = {"coverage_plan": [{"requirement_id": "R1", "scenarios": [{"id": "S1", "title": "Changed"}]}]}
        append_stage_snapshot(project_id=project.project_id, stage="use_cases", payload=changed, operation="generate", actor=self.actor, request_id="third")
        state = get_project(project.project_id, actor=self.actor).stage_state["use_cases"]
        self.assertEqual(state.metadata["scenario_reviews"]["items"]['["R1","S1"]']["status"], "needs_review")

    def test_append_stage_snapshot_versions_and_marks_downstream_stale(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        append_stage_snapshot(
            project_id=project.project_id,
            stage="impact_analysis",
            payload={"summary": {"changed_item_count": 1}},
            operation="impact.analysis",
            actor=self.actor,
            request_id="req-impact",
            approved=False,
            title="Impact analysis saved",
        )
        append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={"test_cases": [{"id": "TC-1"}]},
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-2",
            approved=True,
            title="Test cases saved",
        )

        requirement_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="requirements",
            payload={"requirements": [{"id": "REQ-1", "text": "Login"}]},
            operation="requirements.refine",
            actor=self.actor,
            request_id="req-3",
            approved=True,
            title="Requirements refined",
        )

        loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(requirement_snapshot.version, 1)
        self.assertEqual(loaded.stage_state["requirements"].current_snapshot_id, requirement_snapshot.snapshot_id)
        self.assertTrue(loaded.stage_state["impact_analysis"].stale)
        self.assertIn("requirements changed", loaded.stage_state["impact_analysis"].stale_reason)
        self.assertTrue(loaded.stage_state["test_cases"].stale)
        self.assertIn("requirements changed", loaded.stage_state["test_cases"].stale_reason)

    def test_record_execution_run_is_returned_with_project_detail(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        test_case_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={"test_cases": [{"id": "TC-001", "title": "Checkout"}]},
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-test",
            approved=True,
            title="Test cases",
        )
        execution_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "failed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-2",
            approved=False,
            source_snapshot_id=test_case_snapshot.snapshot_id,
            title="Staging execution run",
        )
        record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="req-2",
            run_id="run-1",
            target_environment="staging",
            target_base_url="https://staging.example.test",
            status_value="failed",
            summary={"passed": 2, "failed": 1},
            test_case_count=3,
            snapshot_id=execution_snapshot.snapshot_id,
            source_snapshot_id=test_case_snapshot.snapshot_id,
            selected_test_case_ids=["TC-001"],
            workflow_run_id="workflow-1",
            source_event_id="event-1",
            project_revision=execution_snapshot.project_revision,
        )

        loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(len(loaded.execution_runs), 1)
        self.assertEqual(loaded.execution_runs[0].target_environment, "staging")
        self.assertEqual(loaded.execution_runs[0].target_base_url, "https://staging.example.test")
        self.assertEqual(loaded.execution_runs[0].source_snapshot_id, test_case_snapshot.snapshot_id)
        self.assertEqual(loaded.execution_runs[0].selected_test_case_ids, ["TC-001"])
        self.assertEqual(loaded.execution_runs[0].summary["failed"], 1)

    def test_execution_run_endpoint_reads_only_the_bound_historical_case_outcomes(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="create")
        other = create_project(name="Other QA", description=None, actor=self.actor, request_id="other")
        result = {
            "id": "candidate-1",
            "source_test_case_id": "TC-001",
            "title": "Checkout confirmation",
            "status": "failed",
            "issues": [{"path": "$.results[0].error", "code": "playwright.error", "message": "Expected receipt to be visible"}],
        }
        execution = append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "failed", "results": [{**result, "stdout": "runtime-only"}]},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="run",
            source_snapshot_id="suite-v1",
        )
        run = record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="run",
            run_id="run-1",
            target_environment="staging",
            target_base_url="https://staging.example.test",
            status_value="failed",
            summary={"passed": 0, "failed": 1},
            test_case_count=1,
            snapshot_id=execution.snapshot_id,
            source_snapshot_id="suite-v1",
            workflow_run_id=None,
            source_event_id=None,
            project_revision=execution.project_revision,
        )
        append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"executable": []},
            operation="automation.execution.preview",
            actor=self.actor,
            request_id="preview",
            source_snapshot_id="suite-v2",
        )

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        path = f"/projects/{project.project_id}/execution-runs/{run.run_record_id}"
        with TestClient(app) as client:
            response = client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            evidence = response.json()
            self.assertEqual(evidence["run_id"], "run-1")
            self.assertEqual(evidence["source_snapshot_id"], "suite-v1")
            self.assertEqual(evidence["target_base_url"], "https://staging.example.test")
            self.assertEqual(evidence["results"], [result])
            self.assertEqual(client.get(f"/projects/{other.project_id}/execution-runs/{run.run_record_id}").status_code, 404)
            self.assertEqual(client.get(f"/projects/{project.project_id}/execution-runs/unknown").status_code, 404)

            stored = self.store[f"qa_projects/{project.project_id}/snapshots/{execution.snapshot_id}"]
            for field, wrong_value in (("project_id", other.project_id), ("operation", "automation.execution.preview"), ("source_snapshot_id", "suite-v2")):
                with self.subTest(field=field), patch.dict(stored, {field: wrong_value}):
                    self.assertIsNone(client.get(path).json()["results"])
            with patch.dict(stored["payload"], {"run_id": "different-run"}):
                self.assertIsNone(client.get(path).json()["results"])
            del stored["payload"]["results"]
            summary = client.get(path).json()
            self.assertIsNone(summary["results"])
            self.assertEqual(summary["summary"], {"passed": 0, "failed": 1})

            app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="outsider", email="other@example.test", name="Outsider")
            self.assertEqual(client.get(path).status_code, 403)

    def test_saved_case_endpoint_returns_exact_historical_case_and_bound_source(self) -> None:
        project = create_project(name="Case handoff", description=None, actor=self.actor, request_id="create")
        other = create_project(name="Other QA", description=None, actor=self.actor, request_id="other")
        project_id = project.project_id
        case_id = "TC-01 + / Ω"
        case = {
            "id": case_id,
            "title": "Original checkout",
            "tags": ["REQ-001"],
            "steps": [{"step": 4, "action": "Clear the editor", "expected": "The editor is empty", "test_data": ""}],
        }

        def save(stage, payload, **metadata):
            return append_stage_snapshot(
                project_id=project_id,
                stage=stage,
                payload=payload,
                operation="generate",
                actor=self.actor,
                request_id=f"save-{stage}",
                metadata=metadata,
            )

        original_requirement = {"id": "REQ-001", "text": "Original requirement text"}
        source = save("requirements", {"requirements": [original_requirement, {"id": "REQ-OTHER", "text": "Other source"}]})
        evidence = {"test_case_id": case_id, "step": 4, "action": case["steps"][0]["action"], "expected": case["steps"][0]["expected"]}
        obligation = {"obligation": "Clear the editor", "status": "covered", "source_quote": "Original requirement text"}
        original = save(
            "test_cases",
            {
                "test_cases": [case, {**case, "id": "TC-OTHER", "title": "Unrelated case"}],
                "substantive_assessment": {
                    "obligations": [
                        {**obligation, "evidence": [evidence, {**evidence, "test_case_id": "TC-OTHER"}]},
                        {"obligation": "Unrelated obligation", "evidence": [{**evidence, "test_case_id": "TC-OTHER"}]},
                    ],
                },
            },
            source_snapshot_ids={"requirements": source.snapshot_id},
        )
        save("requirements", {"requirements": [{**original_requirement, "text": "New requirement text"}]})
        newer = save("test_cases", {"test_cases": [{**case, "title": "Replacement checkout"}]})
        ambiguous = save("test_cases", {"test_cases": [case, case]})
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        path = f"/projects/{project_id}/test-case-evidence"
        params = {"snapshot_id": original.snapshot_id, "case_id": case_id}
        before = deepcopy(self.store)
        with TestClient(app) as client:
            response = client.get(path, params=params)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["historical"])
            self.assertEqual(body["snapshot_id"], original.snapshot_id)
            self.assertEqual(body["suite_version"], original.version)
            self.assertEqual(body["project_revision"], original.project_revision)
            self.assertEqual(body["test_case"]["title"], case["title"])
            self.assertEqual(body["test_case"]["steps"], case["steps"])
            self.assertEqual(body["requirements"], [original_requirement])
            self.assertEqual(body["obligations"], [{**obligation, "evidence": [evidence]}])
            replacement = client.get(path, params={**params, "snapshot_id": newer.snapshot_id}).json()
            self.assertEqual(replacement["test_case"]["title"], "Replacement checkout")
            self.assertEqual(replacement["requirements"], [])
            for wrong in (source.snapshot_id, ambiguous.snapshot_id, "unknown"):
                self.assertEqual(client.get(path, params={**params, "snapshot_id": wrong}).status_code, 404)
            self.assertEqual(client.get(path, params={**params, "case_id": "unknown"}).status_code, 404)
            self.assertEqual(client.get(path, params={**params, "snapshot_id": "path/nested"}).status_code, 422)
            self.assertEqual(client.get(f"/projects/{other.project_id}/test-case-evidence", params=params).status_code, 404)
            app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="outsider", name="Outsider")
            self.assertEqual(client.get(path, params=params).status_code, 403)
        self.assertEqual(self.store, before)

    def test_large_execution_parts_commit_atomically_and_restore_the_bound_run(self) -> None:
        project = create_project(name="Large execution", description=None, actor=self.actor, request_id="create")
        project_path = f"qa_projects/{project.project_id}"
        results = [
            {
                "id": f"candidate-{index}",
                "source_test_case_id": f"TC-{index}",
                "title": f"Case {index}",
                "status": "failed" if index else "passed",
                "generated_spec_path": f"/artifacts/candidate-{index}.spec.ts",
                "issues": [{"path": "$.results[0].error", "code": "playwright.error", "message": b64encode(Random(index).randbytes(240_000)).decode()}]
                if index
                else [],
            }
            for index in range(3)
        ]
        payload = {"run_id": "large-run", "status": "failed", "summary": {"passed": 1, "failed": 2}, "results": results}
        request = {
            "project_id": project.project_id,
            "stage": "execution",
            "payload": payload,
            "operation": "automation.execution.run",
            "actor": self.actor,
            "request_id": "run",
            "base_project_revision": project.current_revision,
            "idempotency_key": "large-run",
        }
        before = set(self.store)
        self.transaction_client.fail_on_create_segment = "/timeline/"
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic create failure"):
                append_stage_snapshot(**request)
        finally:
            self.transaction_client.fail_on_create_segment = None
        self.assertEqual(set(self.store), before)
        self.assertEqual(self.store[project_path]["current_revision"], project.current_revision)

        snapshot = append_stage_snapshot(**request)
        snapshot_path = f"{project_path}/snapshots/{snapshot.snapshot_id}"
        manifest = self.store[snapshot_path]["execution_result_parts"]
        self.assertGreater(manifest["count"], 1)
        self.assertNotIn("results", self.store[snapshot_path]["payload"])
        self.assertEqual(snapshot.payload["results"], results)
        self.assertEqual(get_project(project.project_id, actor=self.actor).current_snapshots["execution"].payload["results"], results)
        retry = append_stage_snapshot(**{**request, "payload": {**payload, "results": []}})
        self.assertEqual(retry, snapshot)
        self.assertEqual(len([path for path in self.store if "/quality_findings/" in path]), 2)

        run = record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="run",
            run_id="large-run",
            target_environment="staging",
            status_value="failed",
            summary=payload["summary"],
            test_case_count=len(results),
            snapshot_id=snapshot.snapshot_id,
            workflow_run_id=None,
            source_event_id=None,
            project_revision=snapshot.project_revision,
        )
        append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"executable": []},
            operation="automation.execution.preview",
            actor=self.actor,
            request_id="later-preview",
        )
        exact = get_project_execution_run(project.project_id, run.run_record_id, actor=self.actor)
        self.assertEqual([row.id for row in exact.results], [row["id"] for row in results])
        self.assertEqual(exact.results[2].issues[0].message, results[2]["issues"][0]["message"])

        part_path = f"{snapshot_path}/execution_result_parts/0000"
        part = self.store[part_path]
        for replacement in (None, {**part, "data": b"corrupt"}):
            with self.subTest(replacement=replacement):
                if replacement is None:
                    del self.store[part_path]
                else:
                    self.store[part_path] = replacement
                with self.assertLogs(level="WARNING"):
                    unavailable = get_project_execution_run(project.project_id, run.run_record_id, actor=self.actor)
                self.assertIsNone(unavailable.results)
                self.assertEqual(unavailable.summary, payload["summary"])

    def test_exceptional_diagnostics_are_abbreviated_without_dropping_unicode_cases(self) -> None:
        project = create_project(name="Noisy execution", description=None, actor=self.actor, request_id="create")
        alphabet = [chr(codepoint) for codepoint in range(0x4E00, 0x9FFF)]
        message = "Expected a confirmation receipt\n" + "".join(Random(383).choices(alphabet, k=240_000))
        issue = {"path": "$.results[0].error", "code": "playwright.error", "message": message}
        rows = [
            {"id": "case-a", "source_test_case_id": "TC-1", "title": "受付", "status": "failed", "issues": [issue] * 6},
            {"id": "case-b", "source_test_case_id": "TC-2", "title": "成功", "status": "passed", "issues": []},
        ]
        snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "noisy-run", "results": rows},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="noisy",
        )
        saved = get_project_stage_snapshot(project.project_id, snapshot.snapshot_id, actor=self.actor).payload["results"]
        self.assertEqual([(row["title"], row["status"]) for row in saved], [("受付", "failed"), ("成功", "passed")])
        saved_issues = saved[0]["issues"]
        self.assertLess(len(saved_issues), len(rows[0]["issues"]))
        self.assertTrue(saved_issues[0]["message"].startswith("Expected a confirmation receipt"))
        self.assertIn("[Saved diagnostic shortened.]", saved_issues[0]["message"])
        self.assertIn("[2 additional diagnostics omitted from saved evidence.]", saved_issues[-1]["message"])
        self.assertNotIn("\ufffd", saved_issues[0]["message"])
        self.assertEqual(rows[0]["issues"][0]["message"], message)

    def test_multiple_environment_execution_runs_preserve_separate_history(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        test_case_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={"test_cases": [{"id": "TC-001", "title": "Checkout"}]},
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-test",
            approved=True,
            title="Test cases",
        )

        for environment, status_value, summary in (
            ("staging", "failed", {"passed": 0, "failed": 1}),
            ("production-like", "passed", {"passed": 1, "failed": 0}),
        ):
            execution_snapshot = append_stage_snapshot(
                project_id=project.project_id,
                stage="execution",
                payload={"run_id": f"run-{environment}", "status": status_value},
                operation="automation.execution.run",
                actor=self.actor,
                request_id=f"req-{environment}",
                approved=status_value == "passed",
                source_snapshot_id=test_case_snapshot.snapshot_id,
                title=f"{environment} execution run",
            )
            record_execution_run(
                project_id=project.project_id,
                actor=self.actor,
                request_id=f"req-{environment}",
                run_id=f"run-{environment}",
                target_environment=environment,
                status_value=status_value,
                summary=summary,
                test_case_count=1,
                snapshot_id=execution_snapshot.snapshot_id,
                source_snapshot_id=test_case_snapshot.snapshot_id,
                selected_test_case_ids=["TC-001"],
                workflow_run_id=f"workflow-{environment}",
                source_event_id=f"event-{environment}",
                project_revision=execution_snapshot.project_revision,
            )

        loaded = get_project(project.project_id, actor=self.actor)
        by_environment = {run.target_environment: run for run in loaded.execution_runs}
        self.assertEqual(set(by_environment), {"staging", "production-like"})
        self.assertEqual(by_environment["staging"].status, "failed")
        self.assertEqual(by_environment["production-like"].status, "passed")
        self.assertEqual(by_environment["staging"].source_snapshot_id, test_case_snapshot.snapshot_id)
        self.assertEqual(by_environment["production-like"].source_snapshot_id, test_case_snapshot.snapshot_id)

    def test_record_execution_run_idempotency_preserves_source_snapshot(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        test_case_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="test_cases",
            payload={"test_cases": [{"id": "TC-001", "title": "Checkout"}]},
            operation="testcases.generate",
            actor=self.actor,
            request_id="req-test",
            approved=True,
            title="Test cases",
        )
        execution_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-staging", "status": "failed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-staging",
            approved=False,
            source_snapshot_id=test_case_snapshot.snapshot_id,
            title="Staging execution run",
            idempotency_key="automation.execution.run:req-staging:staging",
        )

        first = record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="req-staging",
            run_id="run-staging",
            target_environment="staging",
            status_value="failed",
            summary={"passed": 0, "failed": 1},
            test_case_count=1,
            snapshot_id=execution_snapshot.snapshot_id,
            source_snapshot_id=test_case_snapshot.snapshot_id,
            selected_test_case_ids=["TC-001"],
            workflow_run_id="workflow-staging",
            source_event_id="event-staging",
            project_revision=execution_snapshot.project_revision,
            idempotency_key="automation.execution.run_record:req-staging:staging",
        )
        second = record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="req-staging",
            run_id="run-staging-retry",
            target_environment="staging",
            status_value="passed",
            summary={"passed": 1, "failed": 0},
            test_case_count=1,
            snapshot_id=execution_snapshot.snapshot_id,
            source_snapshot_id=test_case_snapshot.snapshot_id,
            selected_test_case_ids=["TC-001"],
            workflow_run_id="workflow-staging",
            source_event_id="event-staging",
            project_revision=execution_snapshot.project_revision,
            idempotency_key="automation.execution.run_record:req-staging:staging",
        )

        loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(first.run_record_id, second.run_record_id)
        self.assertEqual(len(loaded.execution_runs), 1)
        self.assertEqual(loaded.execution_runs[0].status, "failed")
        self.assertEqual(loaded.execution_runs[0].source_snapshot_id, test_case_snapshot.snapshot_id)
        self.assertEqual(loaded.execution_runs[0].selected_test_case_ids, ["TC-001"])

    def test_stage_snapshot_idempotency_key_returns_existing_snapshot(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")

        first = append_stage_snapshot(
            project_id=project.project_id,
            stage="requirements",
            payload={"requirements": [{"id": "REQ-1", "text": "Login"}]},
            operation="requirements.parse",
            actor=self.actor,
            request_id="req-2",
            idempotency_key="requirements:req-2",
        )
        second = append_stage_snapshot(
            project_id=project.project_id,
            stage="requirements",
            payload={"requirements": [{"id": "REQ-1", "text": "Login retry"}]},
            operation="requirements.parse",
            actor=self.actor,
            request_id="req-2",
            base_project_revision=0,
            idempotency_key="requirements:req-2",
        )

        loaded = get_project(project.project_id, actor=self.actor)
        snapshot_paths = [path for path in self.store if "/snapshots/" in path]
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(second.payload["requirements"][0]["text"], "Login")
        self.assertEqual(len(snapshot_paths), 1)
        self.assertEqual(loaded.stage_state["requirements"].version, 1)

    def test_execution_run_idempotency_key_returns_existing_record(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        execution_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "passed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-2",
            approved=True,
            title="Staging execution run",
        )

        first = record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="req-2",
            run_id="run-1",
            target_environment="staging",
            status_value="passed",
            summary={"passed": 1, "failed": 0},
            test_case_count=1,
            snapshot_id=execution_snapshot.snapshot_id,
            workflow_run_id="workflow-1",
            source_event_id="event-1",
            project_revision=execution_snapshot.project_revision,
            idempotency_key="execution:req-2",
        )
        second = record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="req-2",
            run_id="run-retry",
            target_environment="staging",
            status_value="failed",
            summary={"passed": 0, "failed": 1},
            test_case_count=1,
            snapshot_id=execution_snapshot.snapshot_id,
            workflow_run_id="workflow-retry",
            source_event_id="event-retry",
            project_revision=execution_snapshot.project_revision,
            idempotency_key="execution:req-2",
        )

        loaded = get_project(project.project_id, actor=self.actor)
        execution_paths = [path for path in self.store if "/execution_runs/" in path]
        self.assertEqual(first.run_record_id, second.run_record_id)
        self.assertEqual(second.run_id, "run-1")
        self.assertEqual(len(execution_paths), 1)
        self.assertEqual(len(loaded.execution_runs), 1)
        self.assertEqual(loaded.execution_runs[0].status, "passed")

    def test_base_revision_conflict_raises(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")

        with self.assertRaises(ProjectConflictError) as context:
            append_stage_snapshot(
                project_id=project.project_id,
                stage="requirements",
                payload={"requirements": []},
                operation="requirements.parse",
                actor=self.actor,
                request_id="req-2",
                base_project_revision=0,
            )

        self.assertEqual(context.exception.latest_revision, 1)


if __name__ == "__main__":
    unittest.main()
