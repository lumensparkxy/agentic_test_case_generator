from pathlib import Path
import sys
import unittest
from typing import Any
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import AuthUser
from app.services.workflow_project_service import (
    ProjectConflictError,
    append_stage_snapshot,
    create_project,
    get_project,
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
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        self.collection_patch = patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=self.collection,
        )
        self.collection_patch.start()

    def tearDown(self) -> None:
        self.collection_patch.stop()

    def test_create_list_and_load_project(self) -> None:
        project = create_project(name="Checkout QA", description="Regression scope", actor=self.actor, request_id="req-1")

        self.assertEqual(project.name, "Checkout QA")
        self.assertEqual(project.current_revision, 1)
        self.assertEqual(project.timeline[0].event_type, "project.created")

        projects = list_projects(actor=self.actor)
        self.assertEqual([item.project_id for item in projects], [project.project_id])

        loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(loaded.description, "Regression scope")

    def test_append_stage_snapshot_versions_and_marks_downstream_stale(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
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
        self.assertTrue(loaded.stage_state["test_cases"].stale)
        self.assertIn("requirements changed", loaded.stage_state["test_cases"].stale_reason)

    def test_record_execution_run_is_returned_with_project_detail(self) -> None:
        project = create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-1")
        execution_snapshot = append_stage_snapshot(
            project_id=project.project_id,
            stage="execution",
            payload={"run_id": "run-1", "status": "failed"},
            operation="automation.execution.run",
            actor=self.actor,
            request_id="req-2",
            approved=False,
            title="Staging execution run",
        )
        record_execution_run(
            project_id=project.project_id,
            actor=self.actor,
            request_id="req-2",
            run_id="run-1",
            target_environment="staging",
            status_value="failed",
            summary={"passed": 2, "failed": 1},
            test_case_count=3,
            snapshot_id=execution_snapshot.snapshot_id,
            workflow_run_id="workflow-1",
            source_event_id="event-1",
            project_revision=execution_snapshot.project_revision,
        )

        loaded = get_project(project.project_id, actor=self.actor)
        self.assertEqual(len(loaded.execution_runs), 1)
        self.assertEqual(loaded.execution_runs[0].target_environment, "staging")
        self.assertEqual(loaded.execution_runs[0].summary["failed"], 1)

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
