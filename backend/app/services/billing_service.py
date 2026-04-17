from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, status

from ..auth.identity import resolve_organization_domain
from ..config import get_billing_settings
from ..models import (
    AuthUser,
    BillingAccount,
    BillingAllocation,
    BillingAllocationRequest,
    BillingAllocationResponse,
    BillingAllocationSummary,
    BillingCatalogEntry,
    BillingConsumptionRecord,
    BillingCreditGrantRequest,
    BillingCreditGrantResponse,
    BillingEntitlementResponse,
    BillingErrorDetail,
    BillingLedgerEntry,
    BillingLedgerResponse,
    BillingOrgSummaryResponse,
    BillingQuotaSummary,
    BillingUserProfile,
    BillingWalletSummary,
    UsageReportResponse,
    UsageReportUserSummary,
)
from .billing_catalog import (
    calculate_units_for_quantity,
    format_units_as_tokens,
    get_billing_catalog_entries,
    get_billing_catalog_entry,
    get_minimum_start_units,
)
from .billing_repository import (
    append_billing_ledger_entry,
    append_consumption_record,
    build_allocation_id,
    ensure_billing_account_for_user,
    ensure_organization_billing_account,
    get_billing_account,
    get_billing_allocation,
    get_consumption_record_by_source_event,
    get_ledger_entries_for_account,
    get_user_profile,
    list_billing_allocations,
    sync_pilot_usage,
    upsert_billing_account,
    upsert_billing_allocation,
    upsert_user_profile,
)
from .reporting_service import build_usage_report


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class BillingAccessContext:
    account: BillingAccount
    profile: BillingUserProfile
    pricing_entry: Optional[BillingCatalogEntry]
    allocation: Optional[BillingAllocation] = None
    shadow_mode: bool = True
    warnings: list[str] = field(default_factory=list)


def _extract_self_usage_summary(report: UsageReportResponse, user: AuthUser) -> UsageReportUserSummary:
    subject = str(user.sub or "").strip()
    email = str(user.email or "").strip().lower()

    for group in report.groups or []:
        for candidate in group.users or []:
            candidate_user_id = str(candidate.user_id or "").strip()
            candidate_email = str(candidate.email or "").strip().lower()
            if (subject and candidate_user_id == subject) or (email and candidate_email == email):
                return candidate

        scope_key = str(group.scope_key or "").strip()
        display_name = str(group.display_name or "").strip().lower()
        if group.scope_type == "individual" and ((subject and scope_key == f"user:{subject}") or (email and display_name == email)):
            return UsageReportUserSummary(
                user_id=subject or "current-user",
                email=user.email,
                name=user.name,
                provider=user.provider,
                total_events=group.total_events,
                requirements_generated_count=group.requirements_generated_count,
                requirements_modified_count=group.requirements_modified_count,
                test_cases_generated_count=group.test_cases_generated_count,
                test_cases_modified_count=group.test_cases_modified_count,
                latest_event_at=group.latest_event_at,
            )

    return UsageReportUserSummary(
        user_id=subject or "current-user",
        email=user.email,
        name=user.name,
        provider=user.provider,
    )


def _allocation_remaining_units(allocation: Optional[BillingAllocation]) -> int:
    if allocation is None:
        return 0
    return max(0, int(allocation.allocated_units or 0) - int(allocation.consumed_units or 0))


def _build_quota_summary(*, limit: int, used: int) -> BillingQuotaSummary:
    normalized_limit = max(0, int(limit))
    normalized_used = max(0, int(used))
    remaining = max(0, normalized_limit - normalized_used)
    return BillingQuotaSummary(
        limit=normalized_limit,
        used=normalized_used,
        remaining=remaining,
        exhausted=remaining <= 0,
    )


def _build_zero_quota_summary() -> BillingQuotaSummary:
    return BillingQuotaSummary(limit=0, used=0, remaining=0, exhausted=False)


def _derive_account_state(
    account: BillingAccount,
    requirements: BillingQuotaSummary,
    test_cases: BillingQuotaSummary,
    allocation: Optional[BillingAllocation] = None,
) -> str:
    if account.account_state == "suspended":
        return "suspended"
    if account.plan_tier == "pilot" and requirements.exhausted and test_cases.exhausted:
        return "exhausted"
    if account.plan_tier in {"premium", "enterprise"} and int(account.balance_units or 0) <= 0:
        return "exhausted"
    if account.scope_type == "organization" and allocation is not None and _allocation_remaining_units(allocation) <= 0:
        return account.account_state
    return "active"


def _resolve_usage_quota_summaries(*, account: BillingAccount, current_user: AuthUser) -> tuple[BillingQuotaSummary, BillingQuotaSummary, list[str]]:
    if account.plan_tier != "pilot":
        return _build_zero_quota_summary(), _build_zero_quota_summary(), []

    usage_report = build_usage_report(
        start_at=account.pilot_started_at,
        current_user=current_user,
        scope="self",
    )
    usage_summary = _extract_self_usage_summary(usage_report, current_user)

    requirements_used = int(usage_summary.requirements_generated_count or 0) + int(usage_summary.requirements_modified_count or 0)
    test_cases_used = int(usage_summary.test_cases_generated_count or 0) + int(usage_summary.test_cases_modified_count or 0)
    requirements = _build_quota_summary(limit=account.pilot_requirement_limit, used=requirements_used)
    test_cases = _build_quota_summary(limit=account.pilot_test_case_limit, used=test_cases_used)
    return requirements, test_cases, list(usage_report.warnings or [])


def _allocation_to_summary(allocation: Optional[BillingAllocation]) -> Optional[BillingAllocationSummary]:
    if allocation is None:
        return None

    return BillingAllocationSummary(
        allocation_id=allocation.allocation_id,
        account_id=allocation.account_id,
        user_id=allocation.user_id,
        allocated_units=int(allocation.allocated_units or 0),
        consumed_units=int(allocation.consumed_units or 0),
        remaining_units=_allocation_remaining_units(allocation),
        granted_by_user_id=allocation.granted_by_user_id,
        reason=allocation.reason,
        updated_at=allocation.updated_at,
    )


def _build_wallet_summary(account: BillingAccount, allocation: Optional[BillingAllocation]) -> BillingWalletSummary:
    settings = get_billing_settings()
    balance_units = max(0, int(account.balance_units or 0))
    spendable_units = balance_units

    if account.scope_type == "organization":
        allocation_remaining_units = _allocation_remaining_units(allocation)
        spendable_units = min(balance_units, allocation_remaining_units) if allocation is not None else 0

    return BillingWalletSummary(
        balance_units=spendable_units,
        token_unit_size=settings.token_unit_size,
        balance_token_display=format_units_as_tokens(spendable_units, settings.token_unit_size),
        can_spend=bool(account.plan_tier in {"premium", "enterprise"} and spendable_units > 0),
    )


def _billing_error(
    *,
    code: str,
    message: str,
    current_user: AuthUser,
    account: BillingAccount,
    requirements: Optional[BillingQuotaSummary] = None,
    test_cases: Optional[BillingQuotaSummary] = None,
    allocation: Optional[BillingAllocation] = None,
    status_code: int = status.HTTP_402_PAYMENT_REQUIRED,
) -> HTTPException:
    detail = BillingErrorDetail(
        code=code,
        message=message,
        contact_email=account.support_contact_email or get_billing_settings().contact_email,
        plan_tier=account.plan_tier,
        account_id=account.account_id,
        requirements_remaining=requirements.remaining if requirements else None,
        test_cases_remaining=test_cases.remaining if test_cases else None,
        balance_units=max(0, int(account.balance_units or 0)),
        allocation_remaining_units=_allocation_remaining_units(allocation) if allocation else None,
    )
    return HTTPException(status_code=status_code, detail=detail.model_dump(exclude_none=True))


def resolve_billing_entitlements(*, current_user: AuthUser) -> BillingEntitlementResponse:
    settings = get_billing_settings()
    account, profile, repository_warnings = ensure_billing_account_for_user(user=current_user, settings=settings)
    allocation = get_billing_allocation(account.account_id, current_user.sub) if account.scope_type == "organization" else None
    requirements, test_cases, usage_warnings = _resolve_usage_quota_summaries(account=account, current_user=current_user)
    account_state = _derive_account_state(account, requirements, test_cases, allocation)

    account = BillingAccount(
        **{
            **account.model_dump(),
            "pilot_requirement_used": requirements.used,
            "pilot_test_case_used": test_cases.used,
            "account_state": account_state,
            "updated_at": _utcnow(),
        }
    )
    account = upsert_billing_account(account)
    profile = upsert_user_profile(
        BillingUserProfile(
            **{
                **profile.model_dump(),
                "billing_account_id": account.account_id,
                "plan_tier": account.plan_tier,
                "updated_at": _utcnow(),
            }
        )
    )

    if account.plan_tier == "pilot":
        sync_pilot_usage(
            account.account_id,
            pilot_requirement_used=requirements.used,
            pilot_test_case_used=test_cases.used,
            account_state=account_state,
        )

    wallet = _build_wallet_summary(account, allocation)

    warnings = [*repository_warnings, *usage_warnings]
    if account.scope_type == "organization" and allocation is None:
        warnings.append("This enterprise account requires an admin allocation before the user can spend shared credits.")
    if settings.shadow_mode:
        warnings.append("Billing is currently in shadow mode; balances are informational until enforcement is enabled.")

    return BillingEntitlementResponse(
        generated_at=_utcnow(),
        account=account,
        requirements=requirements,
        test_cases=test_cases,
        wallet=wallet,
        allocation=_allocation_to_summary(allocation),
        pricing=get_billing_catalog_entries(),
        shadow_mode=settings.shadow_mode,
        warnings=warnings,
    )


def enforce_billing_access(*, current_user: AuthUser, billing_key: str) -> BillingAccessContext:
    entitlements = resolve_billing_entitlements(current_user=current_user)
    pricing_entry = get_billing_catalog_entry(billing_key)
    allocation = get_billing_allocation(entitlements.account.account_id, current_user.sub) if entitlements.account.scope_type == "organization" else None

    context = BillingAccessContext(
        account=entitlements.account,
        profile=get_user_profile(current_user.sub) or BillingUserProfile(user_id=current_user.sub, plan_tier=entitlements.account.plan_tier),
        pricing_entry=pricing_entry,
        allocation=allocation,
        shadow_mode=entitlements.shadow_mode,
        warnings=list(entitlements.warnings or []),
    )

    if pricing_entry is None or not pricing_entry.billable or entitlements.shadow_mode:
        return context

    if entitlements.account.account_state == "suspended":
        raise _billing_error(
            code="account_suspended",
            message="This billing account is suspended. Please contact support to resume workflow processing.",
            current_user=current_user,
            account=entitlements.account,
            requirements=entitlements.requirements,
            test_cases=entitlements.test_cases,
            allocation=allocation,
        )

    if entitlements.account.plan_tier == "pilot":
        if billing_key.startswith("requirements.") and entitlements.requirements.exhausted:
            raise _billing_error(
                code="pilot_quota_exhausted",
                message="Your lifetime pilot requirement quota is exhausted. Please upgrade to premium to continue.",
                current_user=current_user,
                account=entitlements.account,
                requirements=entitlements.requirements,
                test_cases=entitlements.test_cases,
                allocation=allocation,
            )

        if billing_key.startswith("testcases.") and entitlements.test_cases.exhausted:
            raise _billing_error(
                code="pilot_quota_exhausted",
                message="Your lifetime pilot test-case quota is exhausted. Please upgrade to premium to continue.",
                current_user=current_user,
                account=entitlements.account,
                requirements=entitlements.requirements,
                test_cases=entitlements.test_cases,
                allocation=allocation,
            )

        return context

    minimum_start_units = get_minimum_start_units(billing_key)
    spendable_units = max(0, int(entitlements.account.balance_units or 0))
    if entitlements.account.scope_type == "organization":
        if allocation is None:
            raise _billing_error(
                code="org_allocation_required",
                message="This enterprise account requires an organization admin to allocate shared credits to your user before you can run billable workflows.",
                current_user=current_user,
                account=entitlements.account,
                requirements=entitlements.requirements,
                test_cases=entitlements.test_cases,
                allocation=allocation,
            )
        spendable_units = min(spendable_units, _allocation_remaining_units(allocation))

    if spendable_units < minimum_start_units:
        raise _billing_error(
            code="insufficient_credits",
            message="There are not enough credits available to start this workflow. Add more credits or request an allocation from your admin.",
            current_user=current_user,
            account=entitlements.account,
            requirements=entitlements.requirements,
            test_cases=entitlements.test_cases,
            allocation=allocation,
        )

    return context


def record_billing_consumption(
    *,
    current_user: AuthUser,
    billing_context: BillingAccessContext,
    source_event_id: str,
    request_id: str,
    workflow_run_id: Optional[str],
    billing_key: str,
    quantity: int,
    unit: str,
    metadata: Optional[dict] = None,
) -> BillingConsumptionRecord:
    existing_record = get_consumption_record_by_source_event(billing_context.account.account_id, source_event_id)
    if existing_record is not None:
        return existing_record

    settings = get_billing_settings()
    units_charged = calculate_units_for_quantity(billing_key=billing_key, quantity=quantity)
    is_paid_account = billing_context.account.plan_tier in {"premium", "enterprise"}
    applied = bool(is_paid_account and not billing_context.shadow_mode and units_charged > 0)

    consumption_record = append_consumption_record(
        BillingConsumptionRecord(
            consumption_id=str(uuid4()),
            account_id=billing_context.account.account_id,
            actor_user_id=current_user.sub,
            source_event_id=source_event_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            billing_key=billing_key,
            quantity=max(0, int(quantity or 0)),
            unit=unit,
            units_charged=units_charged,
            pricing_version=billing_context.account.pricing_version,
            applied=applied,
            metadata={
                "plan_tier": billing_context.account.plan_tier,
                "shadow_mode": billing_context.shadow_mode,
                **(metadata or {}),
            },
            created_at=_utcnow(),
        )
    )

    if not applied:
        return consumption_record

    projected_balance_units = int(billing_context.account.balance_units or 0) - units_charged
    overdraft_units = max(0, -projected_balance_units)
    if overdraft_units > settings.max_overdraft_units:
        projected_state = "suspended"
    elif projected_balance_units <= 0:
        projected_state = "exhausted"
    else:
        projected_state = "active"

    updated_account = upsert_billing_account(
        BillingAccount(
            **{
                **billing_context.account.model_dump(),
                "balance_units": projected_balance_units,
                "account_state": projected_state,
                "updated_at": _utcnow(),
            }
        )
    )

    if updated_account.scope_type == "organization" and billing_context.allocation is not None:
        updated_allocation = upsert_billing_allocation(
            BillingAllocation(
                **{
                    **billing_context.allocation.model_dump(),
                    "consumed_units": int(billing_context.allocation.consumed_units or 0) + units_charged,
                    "updated_at": _utcnow(),
                }
            )
        )
        billing_context.allocation = updated_allocation

    append_billing_ledger_entry(
        BillingLedgerEntry(
            entry_id=str(uuid4()),
            account_id=updated_account.account_id,
            entry_type="consumption",
            units_delta=-units_charged,
            reason=f"Consumption for {billing_key}",
            actor_user_id=current_user.sub,
            target_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=source_event_id,
            billing_key=billing_key,
            pricing_version=updated_account.pricing_version,
            metadata={
                "quantity": max(0, int(quantity or 0)),
                "unit": unit,
                "overdraft_units": overdraft_units,
            },
            created_at=_utcnow(),
        )
    )
    billing_context.account = updated_account
    return consumption_record


def get_my_billing_ledger(*, current_user: AuthUser) -> BillingLedgerResponse:
    entitlements = resolve_billing_entitlements(current_user=current_user)
    entries = get_ledger_entries_for_account(entitlements.account.account_id, limit=50)
    return BillingLedgerResponse(
        generated_at=_utcnow(),
        account=entitlements.account,
        entries=entries,
    )


def grant_billing_credits(*, current_user: AuthUser, payload: BillingCreditGrantRequest) -> BillingCreditGrantResponse:
    settings = get_billing_settings()
    granted_units = max(0, int(payload.token_quantity or 0)) * settings.token_unit_size

    if payload.scope_type == "individual":
        if not str(payload.target_user_id or "").strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target_user_id is required for individual grants")
        if payload.plan_tier == "enterprise":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enterprise grants must target an organization account")

        target_user = AuthUser(sub=str(payload.target_user_id).strip(), name=str(payload.target_user_id).strip())
        account, profile, _warnings = ensure_billing_account_for_user(user=target_user, settings=settings)
        updated_account = upsert_billing_account(
            BillingAccount(
                **{
                    **account.model_dump(),
                    "plan_tier": payload.plan_tier,
                    "balance_units": int(account.balance_units or 0) + granted_units,
                    "account_state": "active" if granted_units > 0 or payload.plan_tier != "pilot" else account.account_state,
                    "support_contact_email": settings.contact_email,
                    "updated_at": _utcnow(),
                }
            )
        )
        upsert_user_profile(
            BillingUserProfile(
                **{
                    **profile.model_dump(),
                    "billing_account_id": updated_account.account_id,
                    "plan_tier": updated_account.plan_tier,
                    "updated_at": _utcnow(),
                }
            )
        )
    else:
        if not (str(payload.organization_domain or "").strip() or str(payload.tenant_id or "").strip()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_domain or tenant_id is required for organization grants")

        updated_account, _warnings = ensure_organization_billing_account(
            organization_domain=payload.organization_domain,
            tenant_id=payload.tenant_id,
            settings=settings,
        )
        updated_account = upsert_billing_account(
            BillingAccount(
                **{
                    **updated_account.model_dump(),
                    "plan_tier": "enterprise",
                    "balance_units": int(updated_account.balance_units or 0) + granted_units,
                    "account_state": "active",
                    "support_contact_email": settings.contact_email,
                    "updated_at": _utcnow(),
                }
            )
        )

    ledger_entry = append_billing_ledger_entry(
        BillingLedgerEntry(
            entry_id=str(uuid4()),
            account_id=updated_account.account_id,
            entry_type="grant",
            units_delta=granted_units,
            reason=payload.reason,
            actor_user_id=current_user.sub,
            target_user_id=payload.target_user_id if payload.scope_type == "individual" else None,
            pricing_version=updated_account.pricing_version,
            metadata={
                "scope_type": payload.scope_type,
                "organization_domain": payload.organization_domain,
                "token_quantity": int(payload.token_quantity or 0),
                "plan_tier": updated_account.plan_tier,
            },
            created_at=_utcnow(),
        )
    )
    return BillingCreditGrantResponse(
        generated_at=_utcnow(),
        account=updated_account,
        granted_units=granted_units,
        granted_token_quantity=format_units_as_tokens(granted_units, settings.token_unit_size),
        ledger_entry=ledger_entry,
    )


def allocate_organization_credits(*, current_user: AuthUser, payload: BillingAllocationRequest) -> BillingAllocationResponse:
    settings = get_billing_settings()
    organization_domain = str(payload.organization_domain or resolve_organization_domain(current_user) or "").strip().lower() or None
    tenant_id = str(payload.tenant_id or current_user.tenant_id or "").strip() or None
    if not organization_domain and not tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_domain or tenant_id is required to allocate organization credits")

    account, _warnings = ensure_organization_billing_account(
        organization_domain=organization_domain,
        tenant_id=tenant_id,
        settings=settings,
    )
    granted_units = max(0, int(payload.token_quantity or 0)) * settings.token_unit_size
    allocations = list_billing_allocations(account.account_id)
    total_reserved_units = sum(_allocation_remaining_units(allocation) for allocation in allocations)
    existing_allocation = get_billing_allocation(account.account_id, payload.member_user_id)
    available_unreserved_units = max(0, int(account.balance_units or 0) - total_reserved_units)
    if granted_units > available_unreserved_units and not settings.shadow_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot allocate {payload.token_quantity} tokens because only {format_units_as_tokens(available_unreserved_units, settings.token_unit_size)} unreserved tokens remain in the organization wallet.",
        )

    allocation = upsert_billing_allocation(
        BillingAllocation(
            allocation_id=build_allocation_id(account.account_id, payload.member_user_id),
            account_id=account.account_id,
            user_id=payload.member_user_id,
            allocated_units=int(existing_allocation.allocated_units or 0) + granted_units if existing_allocation else granted_units,
            consumed_units=int(existing_allocation.consumed_units or 0) if existing_allocation else 0,
            granted_by_user_id=current_user.sub,
            reason=payload.reason,
            created_at=existing_allocation.created_at if existing_allocation else _utcnow(),
            updated_at=_utcnow(),
        )
    )

    existing_profile = get_user_profile(payload.member_user_id)
    upsert_user_profile(
        BillingUserProfile(
            user_id=payload.member_user_id,
            email=existing_profile.email if existing_profile else None,
            name=existing_profile.name if existing_profile else None,
            provider=existing_profile.provider if existing_profile else None,
            organization_domain=organization_domain or (existing_profile.organization_domain if existing_profile else None),
            tenant_id=tenant_id or (existing_profile.tenant_id if existing_profile else None),
            billing_account_id=account.account_id,
            plan_tier="enterprise",
            created_at=existing_profile.created_at if existing_profile else _utcnow(),
            updated_at=_utcnow(),
        )
    )

    ledger_entry = append_billing_ledger_entry(
        BillingLedgerEntry(
            entry_id=str(uuid4()),
            account_id=account.account_id,
            entry_type="allocation",
            units_delta=0,
            reason=payload.reason,
            actor_user_id=current_user.sub,
            target_user_id=payload.member_user_id,
            pricing_version=account.pricing_version,
            metadata={
                "allocated_units": granted_units,
                "token_quantity": int(payload.token_quantity or 0),
                "member_user_id": payload.member_user_id,
            },
            created_at=_utcnow(),
        )
    )

    return BillingAllocationResponse(
        generated_at=_utcnow(),
        account=account,
        allocation=_allocation_to_summary(allocation),
        ledger_entry=ledger_entry,
    )


def build_organization_billing_summary(
    *,
    current_user: AuthUser,
    organization_domain: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> BillingOrgSummaryResponse:
    settings = get_billing_settings()
    resolved_domain = str(organization_domain or resolve_organization_domain(current_user) or "").strip().lower() or None
    resolved_tenant_id = str(tenant_id or current_user.tenant_id or "").strip() or None
    if not resolved_domain and not resolved_tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_domain or tenant_id is required to load organization billing summary")

    account = get_billing_account(build_organization_account_id := ensure_organization_billing_account(
        organization_domain=resolved_domain,
        tenant_id=resolved_tenant_id,
        settings=settings,
    )[0].account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization billing account was not found")

    allocations = [_allocation_to_summary(allocation) for allocation in list_billing_allocations(account.account_id)]
    return BillingOrgSummaryResponse(
        generated_at=_utcnow(),
        account=account,
        allocations=[allocation for allocation in allocations if allocation is not None],
        recent_ledger=get_ledger_entries_for_account(account.account_id, limit=50),
    )
