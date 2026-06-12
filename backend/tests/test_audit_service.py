from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import AuthUser
from app.observability.metrics import render_prometheus_metrics, reset_metrics
from app.services.audit_repository import AuditWriteFailure, FirestoreAuditDeadLetterSink
from app.services.audit_service import (
    build_actor_snapshot,
    clear_audit_dead_letters,
    complete_workflow_run,
    get_audit_dead_letters,
    record_usage_event,
    reset_audit_dead_letter_sink_for_testing,
    reset_audit_repository_for_testing,
    set_audit_dead_letter_sink_for_testing,
    set_audit_repository_for_testing,
    start_workflow_run,
)


class FailingAuditRepository:
    def __init__(self, *, error: Exception | str = "collection_unavailable", attempts: int = 2) -> None:
        self.error = error
        self.attempts = attempts
        self.usage_payloads: list[dict] = []

    def record_workflow_run_start(self, run_id: str, payload: dict) -> AuditWriteFailure | None:
        return None

    def record_workflow_run_complete(self, run_id: str, payload: dict) -> AuditWriteFailure | None:
        return None

    def record_usage_event(self, event_id: str, payload: dict) -> AuditWriteFailure | None:
        self.usage_payloads.append(payload)
        return AuditWriteFailure(
            collection_name="usage_events",
            operation="usage_event_record",
            payload=payload,
            error=self.error,
            attempts=self.attempts,
        )


class RecordingDeadLetterSink:
    backend = "firestore"

    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def record_dead_letter(self, dead_letter_id: str, payload: dict) -> None:
        self.records.append((dead_letter_id, payload))


class FailingDeadLetterSink:
    backend = "firestore"

    def __init__(self) -> None:
        self.calls = 0

    def record_dead_letter(self, dead_letter_id: str, payload: dict) -> None:
        self.calls += 1
        raise RuntimeError("secret-token-value should not be logged")


class AuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_metrics()
        clear_audit_dead_letters()
        set_audit_dead_letter_sink_for_testing(None)

    def tearDown(self) -> None:
        clear_audit_dead_letters()
        reset_audit_repository_for_testing()
        reset_audit_dead_letter_sink_for_testing()
        reset_metrics()

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

    def test_record_usage_event_writes_sanitized_durable_dead_letter_after_exhausted_retries(self) -> None:
        sink = RecordingDeadLetterSink()
        set_audit_repository_for_testing(FailingAuditRepository(error=RuntimeError("secret-token-value")))
        set_audit_dead_letter_sink_for_testing(sink)
        user = AuthUser(sub="user-7", email="user7@example.com", name="User Seven")

        with self.assertLogs(level="INFO") as log_output:
            event_id = record_usage_event(
                event_type="requirements.parsed",
                billing_key="requirements.parse",
                quantity=1,
                unit="requirement",
                actor=user,
                request_id="req-durable-dead-letter",
                workflow_run_id="run-durable-dead-letter",
                status="failed",
                metadata={
                    "error_message": "secret-token-value",
                    "raw_document": "client document text should never be stored",
                },
            )

        self.assertTrue(event_id)
        self.assertEqual(len(sink.records), 1)
        dead_letter_id, durable_payload = sink.records[0]
        self.assertEqual(dead_letter_id, durable_payload["dead_letter_id"])
        self.assertEqual(durable_payload["collection_name"], "usage_events")
        self.assertEqual(durable_payload["operation"], "usage_event_record")
        self.assertEqual(durable_payload["error_type"], "RuntimeError")
        self.assertEqual(durable_payload["payload"]["request_id"], "req-durable-dead-letter")
        self.assertEqual(durable_payload["payload"]["workflow_run_id"], "run-durable-dead-letter")
        self.assertIn("payload_hash", durable_payload["payload"])
        serialized = str(durable_payload)
        self.assertNotIn("secret-token-value", serialized)
        self.assertNotIn("client document text", serialized)
        self.assertIn("Audit dead-letter durable sink write completed", "\n".join(log_output.output))
        rendered_metrics = render_prometheus_metrics()
        self.assertIn(
            'audit_dead_letter_sink_writes_total{backend="firestore",collection="usage_events",operation="usage_event_record",status="success"} 1',
            rendered_metrics,
        )

    def test_dead_letter_sink_failure_does_not_block_workflow(self) -> None:
        sink = FailingDeadLetterSink()
        set_audit_repository_for_testing(FailingAuditRepository(error="collection_unavailable", attempts=0))
        set_audit_dead_letter_sink_for_testing(sink)

        with self.assertLogs(level="ERROR") as log_output:
            event_id = record_usage_event(
                event_type="requirements.parsed",
                billing_key="requirements.parse",
                quantity=1,
                unit="requirement",
                actor=None,
                request_id="req-sink-failure",
                workflow_run_id="run-sink-failure",
                status="failed",
                metadata={"raw_document": "client document text should never be stored"},
            )

        self.assertTrue(event_id)
        self.assertEqual(sink.calls, 1)
        self.assertEqual(len(get_audit_dead_letters()), 1)
        self.assertIn("Audit dead-letter durable sink write failed", "\n".join(log_output.output))
        self.assertNotIn("secret-token-value", "\n".join(log_output.output))
        rendered_metrics = render_prometheus_metrics()
        self.assertIn(
            'audit_dead_letter_sink_writes_total{backend="firestore",collection="usage_events",operation="usage_event_record",status="failure"} 1',
            rendered_metrics,
        )

    def test_firestore_dead_letter_sink_writes_configured_collection(self) -> None:
        collection = MagicMock()
        document = MagicMock()
        collection.document.return_value = document
        payload = {
            "dead_letter_id": "dead-letter-1",
            "collection_name": "usage_events",
            "operation": "usage_event_record",
            "payload": {"payload_hash": "payload-hash"},
        }
        sink = FirestoreAuditDeadLetterSink(collection_name="audit_dead_letters_test")

        with patch("app.services.firestore_repository.get_firestore_client") as get_client:
            get_client.return_value.collection.return_value = collection
            sink.record_dead_letter("dead-letter-1", payload)

        get_client.return_value.collection.assert_called_once_with("audit_dead_letters_test")
        collection.document.assert_called_once_with("dead-letter-1")
        document.set.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
