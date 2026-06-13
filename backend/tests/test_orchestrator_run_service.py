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
from app.models import AuthUser, OrchestratorBlocker
from app.services.orchestrator_run_service import (
    block_orchestrator_run,
    complete_orchestrator_run,
    list_orchestrator_runs,
    record_orchestrator_event,
    save_orchestrator_checkpoint,
    start_orchestrator_run,
)
from app.services.workflow_project_service import create_project


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


class OrchestratorRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.collection = FakeCollection("qa_projects", self.store)
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        self.workflow_collection_patch = patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=self.collection,
        )
        self.orchestrator_collection_patch = patch(
            "app.services.orchestrator_run_service.get_required_firestore_collection",
            return_value=self.collection,
        )
        self.workflow_collection_patch.start()
        self.orchestrator_collection_patch.start()
        app.dependency_overrides[get_current_user] = lambda: self.actor

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.orchestrator_collection_patch.stop()
        self.workflow_collection_patch.stop()

    def _create_project(self):
        return create_project(name="Checkout QA", description=None, actor=self.actor, request_id="req-create")

    def _start_run(self, project_id: str, request_id: str = "req-run"):
        return start_orchestrator_run(
            project_id=project_id,
            action="generate",
            stage="test_cases",
            actor=self.actor,
            request_id=request_id,
            metadata={"reason": "first suite"},
        )

    def _stored_paths(self, segment: str) -> list[str]:
        return sorted(path for path in self.store if f"/{segment}/" in path)

    def test_start_run_is_idempotent_and_resumable_from_persistence(self) -> None:
        project = self._create_project()

        first = self._start_run(project.project_id)
        second = self._start_run(project.project_id)
        listing = list_orchestrator_runs(project_id=project.project_id, actor=self.actor)

        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(len(self._stored_paths("orchestrator_runs")), 1)
        self.assertEqual(len([event for event in listing.events if event.event_type == "run_started"]), 1)
        self.assertEqual(listing.runs[0].run_id, first.run_id)
        self.assertEqual(listing.runs[0].request_id, "req-run")

    def test_checkpoint_retry_updates_run_without_duplicate_events(self) -> None:
        project = self._create_project()
        run = self._start_run(project.project_id)

        first = save_orchestrator_checkpoint(
            project_id=project.project_id,
            run_id=run.run_id,
            action="generate",
            stage="test_cases",
            actor=self.actor,
            request_id="req-checkpoint",
            source_snapshot_ids={"requirements": "snap-req"},
            output_snapshot_ids={"test_cases": "snap-tests"},
            agent_output_refs=[{"agent_kind": "test_cases", "task_id": "task-1"}],
            execution_run_ids=["exec-1"],
            idempotency_key="generate:req-checkpoint",
        )
        second = save_orchestrator_checkpoint(
            project_id=project.project_id,
            run_id=run.run_id,
            action="generate",
            stage="test_cases",
            actor=self.actor,
            request_id="req-checkpoint",
            source_snapshot_ids={"requirements": "snap-req"},
            output_snapshot_ids={"test_cases": "snap-tests"},
            agent_output_refs=[{"agent_kind": "test_cases", "task_id": "task-1"}],
            execution_run_ids=["exec-1"],
            idempotency_key="generate:req-checkpoint",
        )
        listing = list_orchestrator_runs(project_id=project.project_id, actor=self.actor)

        self.assertEqual(first.checkpoint_id, second.checkpoint_id)
        self.assertEqual(len(self._stored_paths("orchestrator_checkpoints")), 1)
        self.assertEqual(len([event for event in listing.events if event.event_type == "checkpoint_saved"]), 1)
        self.assertEqual(listing.runs[0].produced_snapshot_ids["test_cases"], "snap-tests")
        self.assertEqual(listing.runs[0].execution_run_ids, ["exec-1"])

    def test_run_can_retain_multiple_checkpoints(self) -> None:
        project = self._create_project()
        run = self._start_run(project.project_id)

        first = save_orchestrator_checkpoint(
            project_id=project.project_id,
            run_id=run.run_id,
            action="generate",
            stage="test_cases",
            actor=self.actor,
            request_id="req-checkpoint-1",
            output_snapshot_ids={"test_cases": "snap-tests"},
            idempotency_key="generate:req-checkpoint-1",
        )
        second = save_orchestrator_checkpoint(
            project_id=project.project_id,
            run_id=run.run_id,
            action="review",
            stage="review",
            actor=self.actor,
            request_id="req-checkpoint-2",
            output_snapshot_ids={"review": "snap-review"},
            idempotency_key="review:req-checkpoint-2",
        )
        listing = list_orchestrator_runs(project_id=project.project_id, actor=self.actor)

        self.assertNotEqual(first.checkpoint_id, second.checkpoint_id)
        self.assertEqual(len(self._stored_paths("orchestrator_checkpoints")), 2)
        self.assertEqual(listing.runs[0].current_checkpoint_id, second.checkpoint_id)
        self.assertEqual({checkpoint.checkpoint_id for checkpoint in listing.checkpoints}, {first.checkpoint_id, second.checkpoint_id})

    def test_decision_agent_approval_and_retry_events_are_idempotent(self) -> None:
        project = self._create_project()
        run = self._start_run(project.project_id)

        for event_type in ("decision_recorded", "agent_invoked", "approval_recorded", "retry_recorded"):
            first = record_orchestrator_event(
                project_id=project.project_id,
                run_id=run.run_id,
                event_type=event_type,
                summary=f"{event_type} persisted.",
                actor=self.actor,
                request_id=f"req-{event_type}",
                metadata={"event_type": event_type},
            )
            second = record_orchestrator_event(
                project_id=project.project_id,
                run_id=run.run_id,
                event_type=event_type,
                summary=f"{event_type} persisted again.",
                actor=self.actor,
                request_id=f"req-{event_type}",
                metadata={"event_type": event_type, "retry": True},
            )
            self.assertEqual(first.event_id, second.event_id)

        listing = list_orchestrator_runs(project_id=project.project_id, actor=self.actor)
        event_types = [event.event_type for event in listing.events]
        self.assertEqual(event_types.count("decision_recorded"), 1)
        self.assertEqual(event_types.count("agent_invoked"), 1)
        self.assertEqual(event_types.count("approval_recorded"), 1)
        self.assertEqual(event_types.count("retry_recorded"), 1)

    def test_blocked_run_records_blocker_and_unblock_action(self) -> None:
        project = self._create_project()
        run = self._start_run(project.project_id)
        blocker = OrchestratorBlocker(
            code="missing_approval",
            message="Requirements approval is required.",
            stage="requirements",
            action="generate",
            source_stage="requirements",
        )

        blocked = block_orchestrator_run(
            project_id=project.project_id,
            run_id=run.run_id,
            actor=self.actor,
            request_id="req-blocked",
            blockers=[blocker],
            next_unblock_action="approve",
        )
        listing = list_orchestrator_runs(project_id=project.project_id, actor=self.actor)

        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(blocked.blockers[0].code, "missing_approval")
        self.assertEqual(blocked.next_unblock_action, "approve")
        self.assertEqual([event.event_type for event in listing.events if event.event_type == "blocked"], ["blocked"])

    def test_completed_run_links_snapshots_and_execution_runs(self) -> None:
        project = self._create_project()
        run = self._start_run(project.project_id)
        save_orchestrator_checkpoint(
            project_id=project.project_id,
            run_id=run.run_id,
            action="generate",
            stage="test_cases",
            actor=self.actor,
            request_id="req-checkpoint",
            output_snapshot_ids={"test_cases": "snap-tests"},
            execution_run_ids=["exec-1"],
        )

        completed = complete_orchestrator_run(
            project_id=project.project_id,
            run_id=run.run_id,
            actor=self.actor,
            request_id="req-complete",
            produced_snapshot_ids={"reports": "snap-report"},
            execution_run_ids=["exec-2"],
        )
        listing = list_orchestrator_runs(project_id=project.project_id, actor=self.actor)

        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(completed.completed_at)
        self.assertEqual(completed.produced_snapshot_ids["test_cases"], "snap-tests")
        self.assertEqual(completed.produced_snapshot_ids["reports"], "snap-report")
        self.assertEqual(completed.execution_run_ids, ["exec-1", "exec-2"])
        self.assertEqual([event.event_type for event in listing.events if event.event_type == "action_completed"], ["action_completed"])

    def test_orchestrator_runs_endpoint_returns_timeline_friendly_payload(self) -> None:
        project = self._create_project()
        run = self._start_run(project.project_id)
        save_orchestrator_checkpoint(
            project_id=project.project_id,
            run_id=run.run_id,
            action="generate",
            stage="test_cases",
            actor=self.actor,
            request_id="req-checkpoint",
            output_snapshot_ids={"test_cases": "snap-tests"},
        )

        response = TestClient(app).get(f"/projects/{project.project_id}/orchestrator/runs")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runs"][0]["run_id"], run.run_id)
        self.assertEqual(payload["events"][0]["project_id"], project.project_id)
        self.assertEqual(payload["checkpoints"][0]["output_snapshot_ids"]["test_cases"], "snap-tests")


if __name__ == "__main__":
    unittest.main()
