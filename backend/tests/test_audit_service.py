from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import AuthUser
from app.services.audit_service import (
    build_actor_snapshot,
    clear_audit_dead_letters,
    complete_workflow_run,
    get_audit_dead_letters,
    record_usage_event,
    start_workflow_run,
)


class AuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_audit_dead_letters()

    def tearDown(self) -> None:
        clear_audit_dead_letters()

    def test_build_actor_snapshot_returns_minimal_identity_projection(self) -> None:
        user = AuthUser(
            sub="firebase-uid-123",
            email="user@example.com",
            name="Example User",
            provider="google.com",
            email_verified=True,
        )

        snapshot = build_actor_snapshot(user)

        self.assertEqual(snapshot["user_id"], "firebase-uid-123")
        self.assertEqual(snapshot["email"], "user@example.com")
        self.assertEqual(snapshot["provider"], "google.com")
        self.assertTrue(snapshot["email_verified"])

    def test_start_workflow_run_writes_firestore_document(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        collection.document.return_value = document
        user = AuthUser(sub="user-1", email="user@example.com", name="User One")

        with patch("app.services.firestore_repository.get_firestore_client") as get_client:
            get_client.return_value.collection.return_value = collection
            run_id = start_workflow_run(
                operation="requirements.parse",
                actor=user,
                request_id="req-123",
                metadata={"document_count": 2},
            )

        UUID(run_id)
        collection.document.assert_called_once_with(run_id)
        payload = document.set.call_args[0][0]
        self.assertEqual(payload["operation"], "requirements.parse")
        self.assertEqual(payload["request_id"], "req-123")
        self.assertEqual(payload["actor_user_id"], "user-1")
        self.assertEqual(payload["metadata"]["document_count"], 2)

    def test_complete_workflow_run_updates_status_document(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        collection.document.return_value = document

        with patch("app.services.firestore_repository.get_firestore_client") as get_client:
            get_client.return_value.collection.return_value = collection
            complete_workflow_run(
                "run-123",
                status="completed",
                metadata={"requirement_count": 4},
            )

        collection.document.assert_called_once_with("run-123")
        payload = document.update.call_args[0][0]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["result"]["requirement_count"], 4)

    def test_record_usage_event_writes_append_only_document(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        collection.document.return_value = document
        user = AuthUser(sub="user-2", email="user2@example.com", name="User Two")

        with patch("app.services.firestore_repository.get_firestore_client") as get_client:
            get_client.return_value.collection.return_value = collection
            event_id = record_usage_event(
                event_type="requirements.parsed",
                billing_key="requirements.parse",
                quantity=6,
                unit="requirement",
                actor=user,
                request_id="req-456",
                workflow_run_id="run-123",
                status="completed",
                metadata={"requirements_generated_count": 6},
            )

        UUID(event_id)
        collection.document.assert_called_once_with(event_id)
        payload = document.set.call_args[0][0]
        self.assertEqual(payload["event_type"], "requirements.parsed")
        self.assertEqual(payload["billing_key"], "requirements.parse")
        self.assertEqual(payload["quantity"], 6)
        self.assertEqual(payload["actor_user_id"], "user-2")
        self.assertEqual(payload["workflow_run_id"], "run-123")
        self.assertEqual(payload["metadata"]["requirements_generated_count"], 6)

    def test_start_workflow_run_does_not_raise_when_firestore_write_fails(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        document.set.side_effect = RuntimeError("firestore unavailable")
        collection.document.return_value = document
        user = AuthUser(sub="user-3", email="user3@example.com", name="User Three")

        with patch("app.services.firestore_repository.get_firestore_client") as get_client:
            get_client.return_value.collection.return_value = collection
            run_id = start_workflow_run(
                operation="requirements.parse",
                actor=user,
                request_id="req-789",
            )

        self.assertTrue(run_id)
        collection.document.assert_called_once_with(run_id)

    def test_record_usage_event_does_not_raise_when_firestore_write_fails(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        document.set.side_effect = RuntimeError("firestore unavailable")
        collection.document.return_value = document
        user = AuthUser(sub="user-4", email="user4@example.com", name="User Four")

        with patch("app.services.firestore_repository.get_firestore_client") as get_client:
            get_client.return_value.collection.return_value = collection
            event_id = record_usage_event(
                event_type="requirements.parsed",
                billing_key="requirements.parse",
                quantity=1,
                unit="requirement",
                actor=user,
                request_id="req-999",
                workflow_run_id="run-999",
                status="completed",
            )

        self.assertTrue(event_id)
        collection.document.assert_called_once_with(event_id)

    def test_start_workflow_run_retries_transient_firestore_write_failure(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        document.set.side_effect = [RuntimeError("temporary outage"), None]
        collection.document.return_value = document
        user = AuthUser(sub="user-5", email="user5@example.com", name="User Five")

        with patch.dict(
            "os.environ",
            {"AUDIT_WRITE_RETRY_ATTEMPTS": "1", "AUDIT_WRITE_RETRY_DELAY_SECONDS": "0"},
            clear=False,
        ):
            with patch("app.services.firestore_repository.get_firestore_client") as get_client:
                get_client.return_value.collection.return_value = collection
                run_id = start_workflow_run(
                    operation="requirements.parse",
                    actor=user,
                    request_id="req-retry",
                )

        self.assertTrue(run_id)
        collection.document.assert_called_once_with(run_id)
        self.assertEqual(document.set.call_count, 2)
        self.assertEqual(get_audit_dead_letters(), [])

    def test_record_usage_event_dead_letters_after_exhausted_retries(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        document.set.side_effect = RuntimeError("firestore unavailable")
        collection.document.return_value = document
        user = AuthUser(sub="user-6", email="user6@example.com", name="User Six")

        with patch.dict(
            "os.environ",
            {"AUDIT_WRITE_RETRY_ATTEMPTS": "1", "AUDIT_WRITE_RETRY_DELAY_SECONDS": "0"},
            clear=False,
        ):
            with patch("app.services.firestore_repository.get_firestore_client") as get_client:
                get_client.return_value.collection.return_value = collection
                event_id = record_usage_event(
                    event_type="requirements.parsed",
                    billing_key="requirements.parse",
                    quantity=1,
                    unit="requirement",
                    actor=user,
                    request_id="req-dead-letter",
                    workflow_run_id="run-dead-letter",
                    status="failed",
                    metadata={"error_message": "example"},
                )

        self.assertTrue(event_id)
        collection.document.assert_called_once_with(event_id)
        self.assertEqual(document.set.call_count, 2)
        dead_letters = get_audit_dead_letters()
        self.assertEqual(len(dead_letters), 1)
        self.assertEqual(dead_letters[0]["collection_name"], "usage_events")
        self.assertEqual(dead_letters[0]["operation"], "usage_event_record")
        self.assertEqual(dead_letters[0]["attempts"], 2)
        self.assertEqual(dead_letters[0]["payload"]["request_id"], "req-dead-letter")
        self.assertEqual(dead_letters[0]["payload"]["workflow_run_id"], "run-dead-letter")
        self.assertIn("payload_hash", dead_letters[0]["payload"])
        self.assertNotIn("metadata", dead_letters[0]["payload"])


if __name__ == "__main__":
    unittest.main()
