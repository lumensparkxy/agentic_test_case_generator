from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser


class UsageReportEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_user = AuthUser(
            sub="report-user",
            email="reporter@acme.com",
            name="Reporter",
            provider="google.com",
        )
        app.dependency_overrides[get_current_user] = lambda: self.current_user

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_usage_report_endpoint_returns_service_payload(self) -> None:
        service_payload = {
            "generated_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "start_at": None,
            "end_at": None,
            "total_groups": 1,
            "total_events": 2,
            "groups": [
                {
                    "scope_type": "individual",
                    "scope_key": "user:report-user",
                    "display_name": "reporter@acme.com",
                    "organization_domain": None,
                    "total_events": 2,
                    "unique_user_count": 1,
                    "requirements_generated_count": 3,
                    "requirements_modified_count": 1,
                    "test_cases_generated_count": 4,
                    "test_cases_modified_count": 2,
                    "event_breakdown": {"testcases.generated": 1, "testcases.refined": 1},
                    "latest_event_at": datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
                    "users": [
                        {
                            "user_id": "report-user",
                            "email": "reporter@acme.com",
                            "name": "Reporter",
                            "provider": "google.com",
                            "total_events": 2,
                            "requirements_generated_count": 3,
                            "requirements_modified_count": 1,
                            "test_cases_generated_count": 4,
                            "test_cases_modified_count": 2,
                            "latest_event_at": datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
                        }
                    ],
                }
            ],
            "warnings": [],
        }

        with patch("app.main.build_usage_report", return_value=service_payload) as build_report:
            with TestClient(app) as client:
                response = client.get("/reports/usage/me")

        self.assertEqual(response.status_code, 200)
        build_report.assert_called_once()
        self.assertEqual(build_report.call_args.kwargs["current_user"], self.current_user)
        self.assertIsNone(build_report.call_args.kwargs["start_at"])
        self.assertIsNone(build_report.call_args.kwargs["end_at"])
        self.assertEqual(build_report.call_args.kwargs["scope"], "self")
        self.assertEqual(response.json()["groups"][0]["scope_key"], "user:report-user")
        self.assertEqual(response.json()["groups"][0]["test_cases_generated_count"], 4)

    def test_usage_report_org_endpoint_requires_admin_access(self) -> None:
        with TestClient(app) as client:
            response = client.get("/reports/usage/org")

        self.assertEqual(response.status_code, 403)

    def test_usage_report_org_endpoint_passes_organization_scope_for_admins(self) -> None:
        self.current_user = AuthUser(
            sub="report-admin",
            email="admin@acme.com",
            name="Admin",
            provider="google.com",
            organization_domain="acme.com",
            tenant_id="tenant-acme",
            roles=["tenant_admin"],
            is_org_admin=True,
        )
        app.dependency_overrides[get_current_user] = lambda: self.current_user

        service_payload = {
            "generated_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "start_at": None,
            "end_at": None,
            "total_groups": 1,
            "total_events": 3,
            "groups": [
                {
                    "scope_type": "organization",
                    "scope_key": "org:acme.com",
                    "display_name": "acme.com",
                    "organization_domain": "acme.com",
                    "total_events": 3,
                    "unique_user_count": 2,
                    "requirements_generated_count": 3,
                    "requirements_modified_count": 1,
                    "test_cases_generated_count": 4,
                    "test_cases_modified_count": 2,
                    "event_breakdown": {"requirements.parsed": 1, "testcases.generated": 2},
                    "latest_event_at": datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
                    "users": [],
                }
            ],
            "warnings": [],
        }

        with patch("app.main.build_usage_report", return_value=service_payload) as build_report:
            with TestClient(app) as client:
                response = client.get("/reports/usage/org")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_report.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(build_report.call_args.kwargs["scope"], "organization")
        self.assertEqual(response.json()["groups"][0]["scope_key"], "org:acme.com")

    def test_usage_report_endpoint_rejects_invalid_date_range(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/reports/usage/me",
                params={
                    "start_at": "2026-04-18T00:00:00Z",
                    "end_at": "2026-04-17T00:00:00Z",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("start_at", response.json()["detail"])