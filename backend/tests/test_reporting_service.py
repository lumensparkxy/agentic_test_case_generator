from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.reporting_service import build_usage_report
from app.models import AuthUser


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

        with patch("app.services.firestore_repository.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
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

    def test_build_usage_report_limits_corporate_viewer_to_their_organization(self) -> None:
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
                "actor_user_id": "erin-id",
                "occurred_at": datetime(2026, 4, 17, 13, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "erin-id", "email": "erin@otherco.com", "name": "Erin", "provider": "google.com"},
                "metadata": {"requirements_modified_count": 3},
                "quantity": 3,
            },
        ]

        viewer = AuthUser(sub="report-user", email="reporter@acme.com", name="Reporter")

        with patch("app.services.firestore_repository.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
            report = build_usage_report(current_user=viewer, scope="organization")

        self.assertEqual(report.total_groups, 1)
        self.assertEqual(report.total_events, 2)
        self.assertEqual(report.groups[0].scope_key, "org:acme.com")
        self.assertEqual(report.groups[0].unique_user_count, 2)
        self.assertEqual(report.groups[0].test_cases_generated_count, 5)
        self.assertEqual(report.groups[0].test_cases_modified_count, 2)

    def test_build_usage_report_limits_public_domain_viewer_to_self(self) -> None:
        payloads = [
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
                "actor_user_id": "mallory-id",
                "occurred_at": datetime(2026, 4, 17, 13, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "mallory-id", "email": "mallory@gmail.com", "name": "Mallory", "provider": "google.com"},
                "metadata": {"requirements_modified_count": 4},
                "quantity": 4,
            },
            {
                "event_type": "testcases.generated",
                "actor_user_id": "alice-id",
                "occurred_at": datetime(2026, 4, 17, 14, 0, tzinfo=timezone.utc),
                "actor": {"user_id": "alice-id", "email": "alice@acme.com", "name": "Alice", "provider": "google.com"},
                "metadata": {"test_cases_generated_count": 5},
                "quantity": 5,
            },
        ]

        viewer = AuthUser(sub="carol-id", email="carol@gmail.com", name="Carol")

        with patch("app.services.firestore_repository.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
            report = build_usage_report(current_user=viewer, scope="self")

        self.assertEqual(report.total_groups, 1)
        self.assertEqual(report.total_events, 1)
        self.assertEqual(report.groups[0].scope_key, "user:carol-id")
        self.assertEqual(report.groups[0].scope_type, "individual")
        self.assertEqual(report.groups[0].requirements_generated_count, 7)
        self.assertEqual(report.groups[0].unique_user_count, 1)

    def test_build_usage_report_self_scope_keeps_corporate_user_individual(self) -> None:
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
        ]

        viewer = AuthUser(sub="alice-id", email="alice@acme.com", name="Alice")

        with patch("app.services.firestore_repository.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
            report = build_usage_report(current_user=viewer, scope="self")

        self.assertEqual(report.total_groups, 1)
        self.assertEqual(report.total_events, 1)
        self.assertEqual(report.groups[0].scope_key, "user:alice-id")
        self.assertEqual(report.groups[0].scope_type, "individual")
        self.assertEqual(report.groups[0].test_cases_generated_count, 5)

    def test_build_usage_report_returns_warning_when_firestore_unavailable(self) -> None:
        with patch("app.services.firestore_repository.get_firestore_client", side_effect=RuntimeError("firestore unavailable")):
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

        with patch("app.services.firestore_repository.get_firestore_client", return_value=FakeFirestoreClient(payloads)):
            report = build_usage_report(start_at=datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc))

        self.assertEqual(report.total_events, 1)
        self.assertEqual(report.groups[0].test_cases_generated_count, 6)
