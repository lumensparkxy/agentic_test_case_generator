from pathlib import Path
import sys
import unittest
from typing import Any
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import AuthUser, Requirement, TestCase, TestStep
from app.services.versioning_service import persist_requirement_versions, persist_test_case_versions


class FakeDocument:
    def __init__(self, path: str, recorder: list[tuple[str, str, dict[str, Any], dict[str, Any]]]):
        self.path = path
        self.recorder = recorder

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        self.recorder.append(("set", self.path, payload, {"merge": merge}))

    def update(self, payload: dict[str, Any]) -> None:
        self.recorder.append(("update", self.path, payload, {}))

    def collection(self, name: str):
        return FakeCollection(f"{self.path}/{name}", self.recorder)


class FakeCollection:
    def __init__(self, path: str, recorder: list[tuple[str, str, dict[str, Any], dict[str, Any]]]):
        self.path = path
        self.recorder = recorder

    def document(self, doc_id: str):
        return FakeDocument(f"{self.path}/{doc_id}", self.recorder)


class FakeFirestoreClient:
    def __init__(self):
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []

    def collection(self, name: str):
        return FakeCollection(name, self.calls)


class VersioningServiceTests(unittest.TestCase):
    def test_persist_requirement_versions_assigns_metadata_and_links_previous_version(self) -> None:
        client = FakeFirestoreClient()
        actor = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")
        previous = Requirement(
            id="REQ-1",
            text="Old text",
            artifact_set_id="req-set-1",
            artifact_item_id="req-item-1",
            artifact_version_id="req-ver-1",
            artifact_version_number=1,
        )
        current = [Requirement(id="REQ-1", text="Updated text")]

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            result = persist_requirement_versions(
                current_requirements=current,
                previous_requirements=[previous],
                actor=actor,
                request_id="req-123",
                workflow_run_id="run-123",
                source_event_id="event-123",
                operation="requirements.refine",
                approved=True,
            )

        self.assertEqual(result[0].artifact_set_id, "req-set-1")
        self.assertEqual(result[0].artifact_item_id, "req-item-1")
        self.assertEqual(result[0].artifact_version_number, 2)
        version_payloads = [payload for _, path, payload, _ in client.calls if "/versions/" in path]
        self.assertEqual(len(version_payloads), 1)
        self.assertEqual(version_payloads[0]["previous_version_id"], "req-ver-1")
        self.assertEqual(version_payloads[0]["source_event_id"], "event-123")

    def test_persist_test_case_versions_assigns_metadata_for_new_case(self) -> None:
        client = FakeFirestoreClient()
        actor = AuthUser(sub="firebase-user-2", email="user2@example.com", name="User Two")
        current = [
            TestCase(
                id="TC-1",
                title="Login test",
                steps=[TestStep(step=1, action="Act", expected="Observe")],
            )
        ]

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            result = persist_test_case_versions(
                current_test_cases=current,
                actor=actor,
                request_id="req-456",
                workflow_run_id="run-456",
                source_event_id="event-456",
                operation="testcases.generate",
                approved=False,
            )

        self.assertTrue(result[0].artifact_set_id)
        self.assertTrue(result[0].artifact_item_id)
        self.assertTrue(result[0].artifact_version_id)
        self.assertEqual(result[0].artifact_version_number, 1)
        version_paths = [path for _, path, _, _ in client.calls if "/versions/" in path]
        self.assertTrue(any(path.startswith("test_case_sets/") for path in version_paths))

    def test_persist_requirement_versions_returns_original_models_when_firestore_unavailable(self) -> None:
        current = [Requirement(id="REQ-1", text="The system shall do something")]

        with patch("app.services.firestore_repository.get_firestore_client", side_effect=RuntimeError("firestore unavailable")):
            result = persist_requirement_versions(
                current_requirements=current,
                actor=None,
                request_id="req-789",
                workflow_run_id="run-789",
                source_event_id="event-789",
                operation="requirements.parse",
            )

        self.assertEqual(result, current)
        self.assertIsNone(result[0].artifact_set_id)


if __name__ == "__main__":
    unittest.main()
