from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.reporting_service import build_usage_report


class FakeDocumentSnapshot:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class FakeCollection:
    def __init__(self, payloads):
        self._payloads = payloads

    def stream(self):
        for payload in self._payloads:
            yield FakeDocumentSnapshot(payload)


class FakeFirestoreClient:
    def __init__(self, payloads):
        self._payloads = payloads

    def collection(self, _name):
        return FakeCollection(self._payloads)


class ReportingServiceTests(unittest.TestCase):
    def test_build_usage_report_groups_corporate_domain_and_public_domains_differently(self) -> None:
        payloads = [
            {
                "event_type": "testcases.generated",
                "actor_user_id": "alice-id",
                "occurred_at": datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "alice-id", "email": "alice@acme.com", "name": "Alice", "provider": "google.com"},
                "metadata": {"test_cases_generated_count": 5},
                "quantity": 5,
            },
            {
                "event_type": "testcases.refined",
                "actor_user_id": "bob-id",
                "occurred_at": datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "bob-id", "email": "bob@acme.com", "name": "Bob", "provider": "google.com"},
                "metadata": {"test_cases_modified_count": 2},
                "quantity": 2,
            },
            {
                "event_type": "requirements.parsed",
                "actor_user_id": "carol-id",
                "occurred_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "carol-id", "email": "carol@gmail.com", "name": "Carol", "provider": "google.com"},
                "metadata": {"requirements_generated_count": 7},
                "quantity": 7,
            },
            {
                "event_type": "requirements.refined",
                "actor_user_id": "dave-id",
                "occurred_at": datetime(2026, 4, 17, 13, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "dave-id", "email": "dave@live.com", "name": "Dave", "provider": "live.com"},
                "metadata": {"requirements_modified_count": 3},
                "quantity": 3,
            },
        ]

        with patch("app.services.reporting_service.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
            report = build_usage_report()

        self.assertEqual(report.total_groups, 3)
        self.assertEqual(report.total_events, 4)
        groups = {group.scope_key: group for group in report.groups}
        self.assertIn("org:acme.com", groups)
        self.assertIn("user:carol-id", groups)
        self.assertIn("user:dave-id", groups)
        self.assertEqual(groups["org:acme.com"].unique_user_count, 2)
        self.assertEqual(groups["org:acme.com"].test_cases_generated_count, 5)
        self.assertEqual(groups["org:acme.com"].test_cases_modified_count, 2)
        self.assertEqual(groups["user:carol-id"].requirements_generated_count, 7)
        self.assertEqual(groups["user:dave-id"].requirements_modified_count, 3)

    def test_build_usage_report_returns_warning_when_firestore_unavailable(self) -> None:
        with patch("app.services.reporting_service.get_firestore_client", side_effect=RuntimeError("firestore unavailable")):
            report = build_usage_report()

        self.assertEqual(report.total_groups, 0)
        self.assertEqual(report.total_events, 0)
        self.assertTrue(report.warnings)

    def test_build_usage_report_applies_date_filters(self) -> None:
        payloads = [
            {
                "event_type": "testcases.generated",
                "actor_user_id": "user-1",
                "occurred_at": datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "user-1", "email": "user@acme.com", "name": "User"},
                "metadata": {"test_cases_generated_count": 4},
                "quantity": 4,
            },
            {
                "event_type": "testcases.generated",
                "actor_user_id": "user-1",
                "occurred_at": datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "user-1", "email": "user@acme.com", "name": "User"},
                "metadata": {"test_cases_generated_count": 6},
                "quantity": 6,
            },
        ]

        with patch("app.services.reporting_service.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
            report = build_usage_report(start_at=datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc))

        self.assertEqual(report.total_events, 1)
        self.assertEqual(report.groups[0].test_cases_generated_count, 6)