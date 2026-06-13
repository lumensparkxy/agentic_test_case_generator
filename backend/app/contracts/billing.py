from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


class BillingCatalogEntry(BaseModel):
    billing_key: str
    display_name: str
    unit: Literal["requirement", "test_case", "artifact_source", "request"] = "request"
    units_per_item: int = 0
    billable: bool = True


class BillingUserProfile(BaseModel):
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    provider: Optional[str] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    billing_account_id: Optional[str] = None
    plan_tier: Literal["pilot", "premium", "enterprise"] = "pilot"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BillingAccount(BaseModel):
    account_id: str
    scope_type: Literal["individual", "organization"] = "individual"
    scope_key: str
    owner_user_id: Optional[str] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    plan_tier: Literal["pilot", "premium", "enterprise"] = "pilot"
    account_state: Literal["active", "exhausted", "suspended"] = "active"
    pilot_started_at: Optional[datetime] = None
    pilot_requirement_limit: int = 200
    pilot_requirement_used: int = 0
    pilot_test_case_limit: int = 200
    pilot_test_case_used: int = 0
    pricing_version: str = "pilot-v1"
    balance_units: int = 0
    support_contact_email: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BillingLedgerEntry(BaseModel):
    entry_id: str
    account_id: str
    entry_type: Literal["grant", "purchase", "allocation", "deallocation", "consumption", "reversal", "adjustment"]
    units_delta: int
    reason: Optional[str] = None
    actor_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    request_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    source_event_id: Optional[str] = None
    billing_key: Optional[str] = None
    pricing_version: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class BillingConsumptionRecord(BaseModel):
    consumption_id: str
    account_id: str
    actor_user_id: str
    source_event_id: str
    request_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    billing_key: str
    quantity: int = 0
    unit: str = "request"
    units_charged: int = 0
    pricing_version: Optional[str] = None
    applied: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class BillingAllocation(BaseModel):
    allocation_id: str
    account_id: str
    user_id: str
    allocated_units: int = 0
    consumed_units: int = 0
    granted_by_user_id: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BillingAllocationSummary(BaseModel):
    allocation_id: str
    account_id: str
    user_id: str
    allocated_units: int = 0
    consumed_units: int = 0
    remaining_units: int = 0
    granted_by_user_id: Optional[str] = None
    reason: Optional[str] = None
    updated_at: Optional[datetime] = None


class BillingQuotaSummary(BaseModel):
    limit: int = 0
    used: int = 0
    remaining: int = 0
    exhausted: bool = False


class BillingWalletSummary(BaseModel):
    balance_units: int = 0
    token_unit_size: int = 4
    balance_token_display: str = "0"
    can_spend: bool = False


class BillingEntitlementResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    requirements: BillingQuotaSummary
    test_cases: BillingQuotaSummary
    wallet: BillingWalletSummary
    allocation: Optional[BillingAllocationSummary] = None
    pricing: List[BillingCatalogEntry] = Field(default_factory=list)
    shadow_mode: bool = False
    warnings: List[str] = Field(default_factory=list)


class BillingErrorDetail(BaseModel):
    code: str
    message: str
    contact_email: Optional[str] = None
    plan_tier: Optional[str] = None
    account_id: Optional[str] = None
    requirements_remaining: Optional[int] = None
    test_cases_remaining: Optional[int] = None
    balance_units: Optional[int] = None
    allocation_remaining_units: Optional[int] = None


class BillingCreditGrantRequest(BaseModel):
    scope_type: Literal["individual", "organization"] = "individual"
    target_user_id: Optional[str] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    plan_tier: Literal["pilot", "premium", "enterprise"] = "premium"
    token_quantity: int = Field(default=0, ge=0)
    reason: str = Field(default="Manual credit grant", min_length=1)


class BillingCreditGrantResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    granted_units: int = 0
    granted_token_quantity: str = "0"
    ledger_entry: Optional[BillingLedgerEntry] = None


class BillingAllocationRequest(BaseModel):
    member_user_id: str
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    token_quantity: int = Field(default=0, ge=0)
    reason: str = Field(default="Manual organization allocation", min_length=1)


class BillingAllocationResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    allocation: BillingAllocationSummary
    ledger_entry: Optional[BillingLedgerEntry] = None


class BillingLedgerResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    entries: List[BillingLedgerEntry] = Field(default_factory=list)


class BillingOrgSummaryResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    allocations: List[BillingAllocationSummary] = Field(default_factory=list)
    recent_ledger: List[BillingLedgerEntry] = Field(default_factory=list)


__all__ = [
    "BillingCatalogEntry",
    "BillingUserProfile",
    "BillingAccount",
    "BillingLedgerEntry",
    "BillingConsumptionRecord",
    "BillingAllocation",
    "BillingAllocationSummary",
    "BillingQuotaSummary",
    "BillingWalletSummary",
    "BillingEntitlementResponse",
    "BillingErrorDetail",
    "BillingCreditGrantRequest",
    "BillingCreditGrantResponse",
    "BillingAllocationRequest",
    "BillingAllocationResponse",
    "BillingLedgerResponse",
    "BillingOrgSummaryResponse",
]
