from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from ..auth.authorization import require_billing_admin, require_org_or_billing_admin
from ..auth.jwt_auth import get_current_user
from ..models import (
    AuthUser,
    BillingAllocationRequest,
    BillingAllocationResponse,
    BillingCreditGrantRequest,
    BillingCreditGrantResponse,
    BillingEntitlementResponse,
    BillingLedgerResponse,
    BillingOrgSummaryResponse,
)
from ..services.billing_service import (
    allocate_organization_credits,
    build_organization_billing_summary,
    get_my_billing_ledger,
    grant_billing_credits,
    resolve_billing_entitlements,
)

router = APIRouter()


@router.get("/entitlements/me", response_model=BillingEntitlementResponse)
async def billing_entitlements_me(
    current_user: AuthUser = Depends(get_current_user),
) -> BillingEntitlementResponse:
    return await run_in_threadpool(resolve_billing_entitlements, current_user=current_user)


@router.get("/billing/ledger/me", response_model=BillingLedgerResponse)
async def billing_ledger_me(
    current_user: AuthUser = Depends(get_current_user),
) -> BillingLedgerResponse:
    return await run_in_threadpool(get_my_billing_ledger, current_user=current_user)


@router.post("/billing/admin/credits/grant", response_model=BillingCreditGrantResponse)
async def billing_admin_grant_credits(
    payload: BillingCreditGrantRequest,
    current_user: AuthUser = Depends(require_billing_admin),
) -> BillingCreditGrantResponse:
    return await run_in_threadpool(grant_billing_credits, current_user=current_user, payload=payload)


@router.post("/billing/admin/allocations", response_model=BillingAllocationResponse)
async def billing_admin_allocate_credits(
    payload: BillingAllocationRequest,
    current_user: AuthUser = Depends(require_org_or_billing_admin),
) -> BillingAllocationResponse:
    return await run_in_threadpool(allocate_organization_credits, current_user=current_user, payload=payload)


@router.get("/billing/admin/org-summary", response_model=BillingOrgSummaryResponse)
async def billing_admin_org_summary(
    current_user: AuthUser = Depends(require_org_or_billing_admin),
    organization_domain: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> BillingOrgSummaryResponse:
    return await run_in_threadpool(
        build_organization_billing_summary,
        current_user=current_user,
        organization_domain=organization_domain,
        tenant_id=tenant_id,
    )
