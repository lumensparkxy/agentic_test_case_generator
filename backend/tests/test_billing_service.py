from datetime import datetime, timezone
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_billing_settings
from app.models import (
    AuthUser,
    BillingAccount,
    BillingAllocationRequest,
    BillingCreditGrantRequest,
    BillingEntitlementResponse,
    BillingQuotaSummary,
    BillingUserProfile,
    BillingWalletSummary,
    UsageReportGroup,
    UsageReportResponse,
    UsageReportUserSummary,
)
from app.services.billing_catalog import format_units_as_tokens, get_billing_catalog
from app.services.billing_service import allocate_organization_credits, enforce_billing_access, grant_billing_credits, resolve_billing_entitlements


class BillingServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_billing_settings.cache_clear()
        get_billing_catalog.cache_clear()

    def test_billing_catalog_uses_integer_credit_units(self) -> None:
        with patch.dict(os.environ, {"BILLING_TOKEN_UNIT_SIZE": "4"}, clear=False):
            get_billing_settings.cache_clear()
            get_billing_catalog.cache_clear()
            catalog = get_billing_catalog()

        self.assertEqual(catalog["requirements.parse"].units_per_item, 4)
        self.assertEqual(catalog["requirements.refine"].units_per_item, 2)
        self.assertEqual(catalog["testcases.generate"].units_per_item, 2)
        self.assertEqual(catalog["testcases.refine"].units_per_item, 1)
        self.assertEqual(format_units_as_tokens(3, 4), "0.75")
        self.assertEqual(format_units_as_tokens(8, 4), "2")

    def test_resolve_billing_entitlements_calculates_pilot_usage_remaining(self) -> None:
        user = AuthUser(
            sub="pilot-user",
            email="pilot@example.com",
            name="Pilot User",
            provider="google.com",
        )
        profile = BillingUserProfile(
            user_id="pilot-user",
            email="pilot@example.com",
            name="Pilot User",
            provider="google.com",
            billing_account_id="individual:pilot-user",
            plan_tier="pilot",
        )
        account = BillingAccount(
            account_id="individual:pilot-user",
            scope_type="individual",
            scope_key="user:pilot-user",
            owner_user_id="pilot-user",
            plan_tier="pilot",
            account_state="active",
            pilot_started_at=datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
            pilot_requirement_limit=200,
            pilot_test_case_limit=200,
            support_contact_email="billing@example.com",
        )
        report = UsageReportResponse(
            generated_at=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            total_groups=1,
            total_events=4,
            groups=[
                UsageReportGroup(
                    scope_type="individual",
                    scope_key="user:pilot-user",
                    display_name="pilot@example.com",
                    total_events=4,
                    unique_user_count=1,
                    requirements_generated_count=12,
                    requirements_modified_count=3,
                    test_cases_generated_count=18,
                    test_cases_modified_count=4,
                    users=[
                        UsageReportUserSummary(
                            user_id="pilot-user",
                            email="pilot@example.com",
                            name="Pilot User",
                            provider="google.com",
                            total_events=4,
                            requirements_generated_count=12,
                            requirements_modified_count=3,
                            test_cases_generated_count=18,
                            test_cases_modified_count=4,
                            latest_event_at=datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
                        )
                    ],
                )
            ],
        )

        with patch.dict(os.environ, {"BILLING_SHADOW_MODE": "false", "BILLING_TOKEN_UNIT_SIZE": "4"}, clear=False):
            get_billing_settings.cache_clear()
            get_billing_catalog.cache_clear()
            with patch("app.services.billing_service.ensure_billing_account_for_user", return_value=(account, profile, [])):
                with patch("app.services.billing_service.build_usage_report", return_value=report):
                    with patch("app.services.billing_service.get_billing_allocation", return_value=None):
                        with patch("app.services.billing_service.upsert_billing_account", side_effect=lambda value: value):
                            with patch("app.services.billing_service.upsert_user_profile", side_effect=lambda value: value):
                                with patch("app.services.billing_service.sync_pilot_usage") as sync_pilot_usage:
                                    entitlements = resolve_billing_entitlements(current_user=user)

        self.assertFalse(entitlements.shadow_mode)
        self.assertEqual(entitlements.requirements.used, 15)
        self.assertEqual(entitlements.requirements.remaining, 185)
        self.assertEqual(entitlements.test_cases.used, 22)
        self.assertEqual(entitlements.test_cases.remaining, 178)
        self.assertEqual(entitlements.account.account_state, "active")
        self.assertEqual(entitlements.wallet.balance_token_display, "0")
        sync_pilot_usage.assert_called_once_with(
            "individual:pilot-user",
            pilot_requirement_used=15,
            pilot_test_case_used=22,
            account_state="active",
        )

    def test_enforce_billing_access_blocks_exhausted_pilot_requirements(self) -> None:
        user = AuthUser(sub="pilot-user", email="pilot@example.com", name="Pilot User")
        entitlements = BillingEntitlementResponse(
            generated_at=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            account=BillingAccount(
                account_id="individual:pilot-user",
                scope_type="individual",
                scope_key="user:pilot-user",
                owner_user_id="pilot-user",
                plan_tier="pilot",
                account_state="active",
                support_contact_email="billing@example.com",
            ),
            requirements=BillingQuotaSummary(limit=200, used=200, remaining=0, exhausted=True),
            test_cases=BillingQuotaSummary(limit=200, used=40, remaining=160, exhausted=False),
            wallet=BillingWalletSummary(balance_units=0, token_unit_size=4, balance_token_display="0", can_spend=False),
            shadow_mode=False,
        )

        with patch("app.services.billing_service.resolve_billing_entitlements", return_value=entitlements):
            with patch("app.services.billing_service.get_user_profile", return_value=BillingUserProfile(user_id="pilot-user", plan_tier="pilot")):
                with patch("app.services.billing_service.get_billing_allocation", return_value=None):
                    with self.assertRaises(HTTPException) as error_context:
                        enforce_billing_access(current_user=user, billing_key="requirements.parse")

        self.assertEqual(error_context.exception.status_code, 402)
        self.assertEqual(error_context.exception.detail["code"], "pilot_quota_exhausted")

    def test_grant_billing_credits_converts_tokens_to_units(self) -> None:
        admin_user = AuthUser(sub="admin-user", email="ops@example.com", name="Ops")
        target_account = BillingAccount(
            account_id="individual:target-user",
            scope_type="individual",
            scope_key="user:target-user",
            owner_user_id="target-user",
            plan_tier="pilot",
            account_state="active",
            balance_units=0,
        )
        target_profile = BillingUserProfile(
            user_id="target-user",
            billing_account_id="individual:target-user",
            plan_tier="pilot",
        )
        request = BillingCreditGrantRequest(
            scope_type="individual",
            target_user_id="target-user",
            plan_tier="premium",
            token_quantity=3,
            reason="Manual upgrade",
        )

        with patch.dict(os.environ, {"BILLING_TOKEN_UNIT_SIZE": "4"}, clear=False):
            get_billing_settings.cache_clear()
            with patch("app.services.billing_service.ensure_billing_account_for_user", return_value=(target_account, target_profile, [])):
                with patch("app.services.billing_service.upsert_billing_account", side_effect=lambda value: value):
                    with patch("app.services.billing_service.upsert_user_profile", side_effect=lambda value: value):
                        with patch("app.services.billing_service.append_billing_ledger_entry", side_effect=lambda value: value):
                            response = grant_billing_credits(current_user=admin_user, payload=request)

        self.assertEqual(response.granted_units, 12)
        self.assertEqual(response.account.plan_tier, "premium")
        self.assertEqual(response.account.balance_units, 12)
        self.assertEqual(response.granted_token_quantity, "3")

    def test_allocate_organization_credits_creates_member_allocation(self) -> None:
        admin_user = AuthUser(sub="org-admin", email="admin@acme.com", name="Org Admin", organization_domain="acme.com")
        org_account = BillingAccount(
            account_id="organization:acme.com",
            scope_type="organization",
            scope_key="org:acme.com",
            organization_domain="acme.com",
            plan_tier="enterprise",
            account_state="active",
            balance_units=40,
        )

        with patch.dict(os.environ, {"BILLING_TOKEN_UNIT_SIZE": "4", "BILLING_SHADOW_MODE": "false"}, clear=False):
            get_billing_settings.cache_clear()
            with patch("app.services.billing_service.ensure_organization_billing_account", return_value=(org_account, [])):
                with patch("app.services.billing_service.list_billing_allocations", return_value=[]):
                    with patch("app.services.billing_service.get_billing_allocation", return_value=None):
                        with patch("app.services.billing_service.upsert_billing_allocation", side_effect=lambda value: value):
                            with patch("app.services.billing_service.get_user_profile", return_value=None):
                                with patch("app.services.billing_service.upsert_user_profile", side_effect=lambda value: value):
                                    with patch("app.services.billing_service.append_billing_ledger_entry", side_effect=lambda value: value):
                                        response = allocate_organization_credits(
                                            current_user=admin_user,
                                            payload=BillingAllocationRequest(
                                                member_user_id="member-1",
                                                organization_domain="acme.com",
                                                token_quantity=5,
                                                reason="Seat allocation",
                                            ),
                                        )

        self.assertEqual(response.account.account_id, "organization:acme.com")
        self.assertEqual(response.allocation.user_id, "member-1")
        self.assertEqual(response.allocation.allocated_units, 20)
        self.assertEqual(response.allocation.remaining_units, 20)


if __name__ == "__main__":
    unittest.main()
