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
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="report-user",
            email="reporter@acme.com",
            name="Reporter",
            provider="google.com",
        )

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
                    "scope_type": "organization",
                    "scope_key": "org:acme.com",
                    "display_name": "acme.com",
                    "organization_domain": "acme.com",
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
                            "user_id": "user-1",
                            "email": "user@acme.com",
                            "name": "User One",
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
                response = client.get("/reports/usage")

        self.assertEqual(response.status_code, 200)
        build_report.assert_called_once()
        self.assertEqual(response.json()["groups"][0]["scope_key"], "org:acme.com")
        self.assertEqual(response.json()["groups"][0]["test_cases_generated_count"], 4)

    def test_usage_report_endpoint_rejects_invalid_date_range(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/reports/usage",
                params={
                    "start_at": "2026-04-18T00:00:00Z",
                    "end_at": "2026-04-17T00:00:00Z",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("start_at", response.json()["detail"])