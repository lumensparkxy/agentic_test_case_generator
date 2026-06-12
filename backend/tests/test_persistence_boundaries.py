from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import AuthUser
from app.services import audit_service, reporting_service
from app.services.audit_repository import AuditWriteFailure
from app.services.billing_repository import BILLING_ACCOUNTS_COLLECTION, get_billing_account


class FakeAuditRepository:
    def __init__(self) -> None:
        self.started: list[tuple[str, dict]] = []
        self.completed: list[tuple[str, dict]] = []
        self.usage_events: list[tuple[str, dict]] = []

    def record_workflow_run_start(self, run_id: str, payload: dict) -> AuditWriteFailure | None:
        self.started.append((run_id, payload))
        return None

    def record_workflow_run_complete(self, run_id: str, payload: dict) -> AuditWriteFailure | None:
        self.completed.append((run_id, payload))
        return None

    def record_usage_event(self, event_id: str, payload: dict) -> AuditWriteFailure | None:
        self.usage_events.append((event_id, payload))
        return None


class FakeUsageDocument:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return self._payload


class FakeUsageEventRepository:
    def __init__(self, payloads: list[dict], warnings: list[str] | None = None) -> None:
        self.payloads = payloads
        self.warnings = warnings or []
        self.calls = 0

    def iter_usage_events(self):
        self.calls += 1
        return [FakeUsageDocument(payload) for payload in self.payloads], list(self.warnings)


class FakeFirestoreSnapshot:
    exists = True

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return self._payload


class FakeFirestoreDocument:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get(self) -> FakeFirestoreSnapshot:
        return FakeFirestoreSnapshot(self.payload)


class FakeFirestoreCollection:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.document_ids: list[str] = []

    def document(self, document_id: str) -> FakeFirestoreDocument:
        self.document_ids.append(document_id)
        return FakeFirestoreDocument(self.payload)


class FakeFirestoreClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.collections: dict[str, FakeFirestoreCollection] = {}

    def collection(self, collection_name: str) -> FakeFirestoreCollection:
        collection = FakeFirestoreCollection(self.payload)
        self.collections[collection_name] = collection
        return collection


class PersistenceBoundaryTests(unittest.TestCase):
    def tearDown(self) -> None:
        audit_service.reset_audit_repository_for_testing()
        audit_service.clear_audit_dead_letters()
        reporting_service.reset_usage_event_repository_for_testing()

    def test_audit_service_uses_configured_repository_boundary(self) -> None:
        repository = FakeAuditRepository()
        audit_service.set_audit_repository_for_testing(repository)
        user = AuthUser(sub="user-1", email="user@example.com", name="User One")

        run_id = audit_service.start_workflow_run(
            operation="requirements.parse",
            actor=user,
            request_id="req-123",
            metadata={"document_count": 2},
        )
        audit_service.complete_workflow_run(run_id, status="completed", metadata={"requirement_count": 4})
        event_id = audit_service.record_usage_event(
            event_type="requirements.parsed",
            billing_key="requirements.parse",
            quantity=4,
            unit="requirement",
            actor=user,
            request_id="req-123",
            workflow_run_id=run_id,
            status="completed",
        )

        self.assertEqual(repository.started[0][0], run_id)
        self.assertEqual(repository.started[0][1]["operation"], "requirements.parse")
        self.assertEqual(repository.completed[0][0], run_id)
        self.assertEqual(repository.completed[0][1]["status"], "completed")
        self.assertEqual(repository.usage_events[0][0], event_id)
        self.assertEqual(repository.usage_events[0][1]["billing_key"], "requirements.parse")

    def test_reporting_service_uses_configured_usage_event_repository(self) -> None:
        repository = FakeUsageEventRepository(
            [
                {
                    "event_type": "testcases.generated",
                    "actor_user_id": "user-1",
                    "occurred_at": datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
                    "actor": {"user_id": "user-1", "email": "user@acme.com", "name": "User"},
                    "metadata": {"test_cases_generated_count": 3},
                    "quantity": 3,
                }
            ],
            warnings=["repository warning"],
        )
        reporting_service.set_usage_event_repository_for_testing(repository)

        report = reporting_service.build_usage_report()

        self.assertEqual(repository.calls, 1)
        self.assertEqual(report.total_events, 1)
        self.assertEqual(report.groups[0].scope_key, "org:acme.com")
        self.assertEqual(report.warnings, ["repository warning"])

    def test_billing_repository_reads_through_firestore_adapter_boundary(self) -> None:
        payload = {
            "account_id": "individual:user-1",
            "scope_type": "individual",
            "scope_key": "user:user-1",
            "owner_user_id": "user-1",
            "plan_tier": "pilot",
            "account_state": "active",
        }
        client = FakeFirestoreClient(payload)

        from unittest.mock import patch

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            account = get_billing_account("individual:user-1")

        self.assertIsNotNone(account)
        self.assertEqual(account.account_id, "individual:user-1")
        self.assertIn(BILLING_ACCOUNTS_COLLECTION, client.collections)
        self.assertEqual(client.collections[BILLING_ACCOUNTS_COLLECTION].document_ids, ["individual:user-1"])


if __name__ == "__main__":
    unittest.main()
