from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.auth.authorization import require_billing_admin, require_org_or_billing_admin
from app.models import AuthUser


class BillingEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_user = AuthUser(
            sub="billing-user",
            email="billing@example.com",
            name="Billing User",
            provider="google.com",
        )
        app.dependency_overrides[get_current_user] = lambda: self.current_user

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_entitlements_me_returns_service_payload(self) -> None:
        service_payload = {
            "generated_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "account": {
                "account_id": "individual:billing-user",
                "scope_type": "individual",
                "scope_key": "user:billing-user",
                "owner_user_id": "billing-user",
                "plan_tier": "pilot",
                "account_state": "active",
                "pilot_started_at": datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
                "pilot_requirement_limit": 200,
                "pilot_requirement_used": 12,
                "pilot_test_case_limit": 200,
                "pilot_test_case_used": 30,
                "pricing_version": "pilot-v1",
                "balance_units": 0,
                "support_contact_email": "billing@example.com",
            },
            "requirements": {"limit": 200, "used": 12, "remaining": 188, "exhausted": False},
            "test_cases": {"limit": 200, "used": 30, "remaining": 170, "exhausted": False},
            "wallet": {"balance_units": 0, "token_unit_size": 4, "balance_token_display": "0", "can_spend": False},
            "pricing": [
                {
                    "billing_key": "requirements.parse",
                    "display_name": "Requirement generation",
                    "unit": "requirement",
                    "units_per_item": 4,
                    "billable": True,
                }
            ],
            "shadow_mode": True,
            "warnings": ["Billing is currently in shadow mode; balances are informational until enforcement is enabled."],
        }

        with patch("app.main.resolve_billing_entitlements", return_value=service_payload) as resolve_entitlements:
            with TestClient(app) as client:
                response = client.get("/entitlements/me")

        self.assertEqual(response.status_code, 200)
        resolve_entitlements.assert_called_once()
        self.assertEqual(resolve_entitlements.call_args.kwargs["current_user"], self.current_user)
        payload = response.json()
        self.assertEqual(payload["account"]["plan_tier"], "pilot")
        self.assertEqual(payload["requirements"]["remaining"], 188)
        self.assertTrue(payload["shadow_mode"])

    def test_billing_ledger_me_returns_service_payload(self) -> None:
        service_payload = {
            "generated_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "account": {
                "account_id": "individual:billing-user",
                "scope_type": "individual",
                "scope_key": "user:billing-user",
                "owner_user_id": "billing-user",
                "plan_tier": "premium",
                "account_state": "active",
                "pricing_version": "pilot-v1",
                "balance_units": 16,
            },
            "entries": [
                {
                    "entry_id": "ledger-1",
                    "account_id": "individual:billing-user",
                    "entry_type": "grant",
                    "units_delta": 16,
                    "reason": "Manual grant",
                    "created_at": datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
                }
            ],
        }

        with patch("app.main.get_my_billing_ledger", return_value=service_payload):
            with TestClient(app) as client:
                response = client.get("/billing/ledger/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entries"][0]["entry_type"], "grant")

    def test_billing_admin_grant_credits_returns_service_payload(self) -> None:
        app.dependency_overrides[require_billing_admin] = lambda: self.current_user
        service_payload = {
            "generated_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "account": {
                "account_id": "individual:billing-user",
                "scope_type": "individual",
                "scope_key": "user:billing-user",
                "owner_user_id": "billing-user",
                "plan_tier": "premium",
                "account_state": "active",
                "pricing_version": "pilot-v1",
                "balance_units": 12,
            },
            "granted_units": 12,
            "granted_token_quantity": "3",
            "ledger_entry": {
                "entry_id": "ledger-1",
                "account_id": "individual:billing-user",
                "entry_type": "grant",
                "units_delta": 12,
                "reason": "Manual grant",
                "created_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            },
        }

        with patch("app.main.grant_billing_credits", return_value=service_payload):
            with TestClient(app) as client:
                response = client.post(
                    "/billing/admin/credits/grant",
                    json={"scope_type": "individual", "target_user_id": "billing-user", "plan_tier": "premium", "token_quantity": 3, "reason": "Manual grant"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["granted_units"], 12)

    def test_parse_requirements_returns_402_when_billing_access_is_blocked(self) -> None:
        with patch("app.main.enforce_billing_access", side_effect=HTTPException(status_code=402, detail={"code": "pilot_quota_exhausted", "message": "Blocked"})):
            with patch("app.main.extract_requirements") as extract_requirements:
                with TestClient(app) as client:
                    response = client.post(
                        "/requirements/parse",
                        files={"file": ("requirements.md", BytesIO(b"# Requirement\nThe system shall allow login"), "text/markdown")},
                    )

        self.assertEqual(response.status_code, 402)
        extract_requirements.assert_not_called()

    def test_generate_test_cases_returns_402_when_billing_access_is_blocked(self) -> None:
        payload = {
            "requirements": [{"id": "REQ-1", "text": "The system shall allow login"}],
            "template": {"name": "default", "format": "table", "fields": ["id", "title", "steps"]},
            "context": None,
            "feedback": None,
            "workflow_settings": None,
        }

        with patch("app.main.enforce_billing_access", side_effect=HTTPException(status_code=402, detail={"code": "insufficient_credits", "message": "Blocked"})):
            with patch("app.main.generate_test_cases") as generate_test_cases:
                with TestClient(app) as client:
                    response = client.post("/testcases/generate", json=payload)

        self.assertEqual(response.status_code, 402)
        generate_test_cases.assert_not_called()


if __name__ == "__main__":
    unittest.main()
