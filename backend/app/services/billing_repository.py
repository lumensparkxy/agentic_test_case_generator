import logging
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Optional

from ..auth.identity import resolve_organization_domain
from ..config import BillingSettings
from ..models import AuthUser, BillingAccount, BillingAllocation, BillingConsumptionRecord, BillingLedgerEntry, BillingUserProfile
from .firestore_repository import get_optional_firestore_collection

USER_PROFILES_COLLECTION = "user_profiles"
BILLING_ACCOUNTS_COLLECTION = "billing_accounts"
BILLING_LEDGER_COLLECTION = "billing_wallet_ledger"
BILLING_ALLOCATIONS_COLLECTION = "billing_allocations"
BILLING_CONSUMPTION_COLLECTION = "billing_consumption"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, datetime)):
        return value

    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items() if item is not None}

    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value if item is not None]

    if hasattr(value, "model_dump"):
        return _serialize_value(value.model_dump())

    return str(value)


def _get_collection(collection_name: str):
    return get_optional_firestore_collection(
        collection_name,
        unavailable_message=f"Firestore client unavailable for {collection_name} reads/writes",
    )


def _safe_set(document_ref, payload: dict[str, Any], *, operation: str, merge: bool = False) -> None:
    try:
        document_ref.set(payload, merge=merge)
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore %s skipped because write failed: %s", operation, exc)


def _safe_get_model(document_ref, model_cls, *, operation: str):
    try:
        snapshot = document_ref.get()
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore %s skipped because read failed: %s", operation, exc)
        return None

    if not snapshot.exists:
        return None

    payload = snapshot.to_dict() or {}
    try:
        return model_cls(**payload)
    except Exception as exc:
        logging.warning("Firestore %s skipped because payload was invalid: %s", operation, exc)
        return None


def build_individual_account_id(user_id: str) -> str:
    return f"individual:{user_id.strip() or 'unknown-user'}"


def build_individual_scope_key(user_id: str) -> str:
    return f"user:{user_id.strip() or 'unknown-user'}"


def build_organization_account_id(organization_domain: Optional[str], tenant_id: Optional[str] = None) -> str:
    normalized_domain = str(organization_domain or "").strip().lower()
    normalized_tenant_id = str(tenant_id or "").strip()
    scope_value = normalized_domain or normalized_tenant_id or "unknown-organization"
    return f"organization:{scope_value}"


def build_organization_scope_key(organization_domain: Optional[str], tenant_id: Optional[str] = None) -> str:
    normalized_domain = str(organization_domain or "").strip().lower()
    normalized_tenant_id = str(tenant_id or "").strip()
    scope_value = normalized_domain or normalized_tenant_id or "unknown-organization"
    return f"org:{scope_value}"


def build_allocation_id(account_id: str, user_id: str) -> str:
    return f"{account_id}::{user_id.strip() or 'unknown-user'}"


def _build_default_individual_account(user: AuthUser, settings: BillingSettings) -> BillingAccount:
    now = _utcnow()
    return BillingAccount(
        account_id=build_individual_account_id(user.sub),
        scope_type="individual",
        scope_key=build_individual_scope_key(user.sub),
        owner_user_id=user.sub,
        organization_domain=resolve_organization_domain(user),
        tenant_id=user.tenant_id,
        plan_tier="pilot",
        account_state="active",
        pilot_started_at=settings.launch_date or now,
        pilot_requirement_limit=settings.pilot_requirements_limit,
        pilot_test_case_limit=settings.pilot_test_cases_limit,
        pricing_version=settings.pricing_version,
        balance_units=0,
        support_contact_email=settings.contact_email,
        created_at=now,
        updated_at=now,
    )


def _build_default_user_profile(user: AuthUser, account_id: str) -> BillingUserProfile:
    now = _utcnow()
    return BillingUserProfile(
        user_id=user.sub,
        email=user.email,
        name=user.name,
        provider=user.provider,
        organization_domain=resolve_organization_domain(user),
        tenant_id=user.tenant_id,
        billing_account_id=account_id,
        plan_tier="pilot",
        created_at=now,
        updated_at=now,
    )


def _build_default_organization_account(
    *,
    organization_domain: Optional[str],
    tenant_id: Optional[str],
    settings: BillingSettings,
) -> BillingAccount:
    now = _utcnow()
    return BillingAccount(
        account_id=build_organization_account_id(organization_domain, tenant_id),
        scope_type="organization",
        scope_key=build_organization_scope_key(organization_domain, tenant_id),
        owner_user_id=None,
        organization_domain=str(organization_domain or "").strip().lower() or None,
        tenant_id=str(tenant_id or "").strip() or None,
        plan_tier="enterprise",
        account_state="active",
        pilot_started_at=settings.launch_date or now,
        pilot_requirement_limit=settings.pilot_requirements_limit,
        pilot_test_case_limit=settings.pilot_test_cases_limit,
        pricing_version=settings.pricing_version,
        balance_units=0,
        support_contact_email=settings.contact_email,
        created_at=now,
        updated_at=now,
    )


def get_user_profile(user_id: str) -> Optional[BillingUserProfile]:
    user_collection = _get_collection(USER_PROFILES_COLLECTION)
    if user_collection is None:
        return None

    return _safe_get_model(
        user_collection.document(user_id),
        BillingUserProfile,
        operation="billing_user_profile_read",
    )


def upsert_user_profile(profile: BillingUserProfile) -> BillingUserProfile:
    user_collection = _get_collection(USER_PROFILES_COLLECTION)
    if user_collection is None:
        return profile

    payload = profile.model_dump()
    payload["updated_at"] = _utcnow()
    if payload.get("created_at") is None:
        payload["created_at"] = _utcnow()
    normalized_profile = BillingUserProfile(**payload)
    _safe_set(
        user_collection.document(normalized_profile.user_id),
        _serialize_value(normalized_profile),
        operation="billing_user_profile_upsert",
        merge=True,
    )
    return normalized_profile


def get_billing_account(account_id: str) -> Optional[BillingAccount]:
    account_collection = _get_collection(BILLING_ACCOUNTS_COLLECTION)
    if account_collection is None:
        return None

    return _safe_get_model(
        account_collection.document(account_id),
        BillingAccount,
        operation="billing_account_read",
    )


def upsert_billing_account(account: BillingAccount) -> BillingAccount:
    account_collection = _get_collection(BILLING_ACCOUNTS_COLLECTION)
    if account_collection is None:
        return account

    payload = account.model_dump()
    payload["updated_at"] = _utcnow()
    if payload.get("created_at") is None:
        payload["created_at"] = _utcnow()
    normalized_account = BillingAccount(**payload)
    _safe_set(
        account_collection.document(normalized_account.account_id),
        _serialize_value(normalized_account),
        operation="billing_account_upsert",
        merge=True,
    )
    return normalized_account


def ensure_organization_billing_account(
    *,
    organization_domain: Optional[str],
    tenant_id: Optional[str],
    settings: BillingSettings,
) -> tuple[BillingAccount, list[str]]:
    warnings: list[str] = []
    default_account = _build_default_organization_account(
        organization_domain=organization_domain,
        tenant_id=tenant_id,
        settings=settings,
    )
    existing_account = get_billing_account(default_account.account_id)
    if existing_account is None:
        account = upsert_billing_account(default_account)
        return account, warnings

    merged_payload = default_account.model_dump()
    merged_payload.update(existing_account.model_dump(exclude_none=True))
    merged_payload.update(
        {
            "scope_type": "organization",
            "scope_key": build_organization_scope_key(organization_domain or existing_account.organization_domain, tenant_id or existing_account.tenant_id),
            "organization_domain": str(organization_domain or existing_account.organization_domain or "").strip().lower() or None,
            "tenant_id": str(tenant_id or existing_account.tenant_id or "").strip() or None,
            "plan_tier": "enterprise",
            "updated_at": _utcnow(),
        }
    )
    account = upsert_billing_account(BillingAccount(**merged_payload))
    return account, warnings


def ensure_individual_billing_account(*, user: AuthUser, settings: BillingSettings) -> tuple[BillingAccount, list[str]]:
    warnings: list[str] = []
    account_id = build_individual_account_id(user.sub)
    default_account = _build_default_individual_account(user, settings)
    default_profile = _build_default_user_profile(user, account_id)

    user_collection = _get_collection(USER_PROFILES_COLLECTION)
    account_collection = _get_collection(BILLING_ACCOUNTS_COLLECTION)
    if user_collection is None or account_collection is None:
        warnings.append("Firestore billing storage is unavailable; using computed billing defaults.")
        return default_account, warnings

    existing_account = _safe_get_model(
        account_collection.document(account_id),
        BillingAccount,
        operation="billing_account_read",
    )

    if existing_account is not None:
        merged_payload = default_account.model_dump()
        merged_payload.update(existing_account.model_dump(exclude_none=True))
        merged_payload.update(
            {
                "owner_user_id": user.sub,
                "scope_key": build_individual_scope_key(user.sub),
                "organization_domain": resolve_organization_domain(user) or existing_account.organization_domain,
                "tenant_id": user.tenant_id or existing_account.tenant_id,
                "support_contact_email": existing_account.support_contact_email or settings.contact_email,
                "pricing_version": existing_account.pricing_version or settings.pricing_version,
                "updated_at": _utcnow(),
            }
        )
        account = BillingAccount(**merged_payload)
    else:
        account = default_account

    profile_payload = default_profile.model_dump()
    profile_payload.update(
        {
            "plan_tier": account.plan_tier,
            "updated_at": _utcnow(),
        }
    )
    user_profile = BillingUserProfile(**profile_payload)

    _safe_set(
        user_collection.document(user.sub),
        _serialize_value(user_profile),
        operation="billing_user_profile_upsert",
        merge=True,
    )
    _safe_set(
        account_collection.document(account.account_id),
        _serialize_value(account),
        operation="billing_account_upsert",
        merge=True,
    )

    return account, warnings


def ensure_billing_account_for_user(*, user: AuthUser, settings: BillingSettings) -> tuple[BillingAccount, BillingUserProfile, list[str]]:
    warnings: list[str] = []
    existing_profile = get_user_profile(user.sub)

    if existing_profile and existing_profile.billing_account_id:
        existing_account = get_billing_account(existing_profile.billing_account_id)
        if existing_account is not None:
            merged_profile_payload = existing_profile.model_dump(exclude_none=True)
            merged_profile_payload.update(
                {
                    "email": user.email,
                    "name": user.name,
                    "provider": user.provider,
                    "organization_domain": resolve_organization_domain(user) or existing_profile.organization_domain,
                    "tenant_id": user.tenant_id or existing_profile.tenant_id,
                    "updated_at": _utcnow(),
                }
            )
            profile = upsert_user_profile(BillingUserProfile(**merged_profile_payload))

            merged_account_payload = existing_account.model_dump(exclude_none=True)
            merged_account_payload.update(
                {
                    "support_contact_email": existing_account.support_contact_email or settings.contact_email,
                    "pricing_version": existing_account.pricing_version or settings.pricing_version,
                    "updated_at": _utcnow(),
                }
            )
            account = upsert_billing_account(BillingAccount(**merged_account_payload))
            return account, profile, warnings

        warnings.append("Stored billing account reference was missing; falling back to the individual billing account.")

    account, account_warnings = ensure_individual_billing_account(user=user, settings=settings)
    warnings.extend(account_warnings)
    profile = upsert_user_profile(
        BillingUserProfile(
            user_id=user.sub,
            email=user.email,
            name=user.name,
            provider=user.provider,
            organization_domain=resolve_organization_domain(user),
            tenant_id=user.tenant_id,
            billing_account_id=account.account_id,
            plan_tier=account.plan_tier,
            created_at=existing_profile.created_at if existing_profile else None,
            updated_at=_utcnow(),
        )
    )
    return account, profile, warnings


def sync_pilot_usage(
    account_id: str,
    *,
    pilot_requirement_used: int,
    pilot_test_case_used: int,
    account_state: str,
) -> None:
    account_collection = _get_collection(BILLING_ACCOUNTS_COLLECTION)
    if account_collection is None:
        return

    _safe_set(
        account_collection.document(account_id),
        _serialize_value(
            {
                "pilot_requirement_used": max(0, int(pilot_requirement_used)),
                "pilot_test_case_used": max(0, int(pilot_test_case_used)),
                "account_state": account_state,
                "updated_at": _utcnow(),
            }
        ),
        operation="billing_account_usage_sync",
        merge=True,
    )


def append_billing_ledger_entry(entry: BillingLedgerEntry) -> BillingLedgerEntry:
    ledger_collection = _get_collection(BILLING_LEDGER_COLLECTION)
    if ledger_collection is None:
        return entry

    payload = entry.model_dump()
    if payload.get("entry_id") is None:
        payload["entry_id"] = str(uuid4())
    if payload.get("created_at") is None:
        payload["created_at"] = _utcnow()
    normalized_entry = BillingLedgerEntry(**payload)
    _safe_set(
        ledger_collection.document(normalized_entry.entry_id),
        _serialize_value(normalized_entry),
        operation="billing_ledger_append",
        merge=False,
    )
    return normalized_entry


def get_ledger_entries_for_account(account_id: str, *, limit: int = 50) -> list[BillingLedgerEntry]:
    ledger_collection = _get_collection(BILLING_LEDGER_COLLECTION)
    if ledger_collection is None:
        return []

    try:
        documents = ledger_collection.stream()
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore billing ledger list skipped because query failed: %s", exc)
        return []

    entries: list[BillingLedgerEntry] = []
    for document in documents:
        payload = document.to_dict() or {}
        if str(payload.get("account_id") or "").strip() != account_id:
            continue
        try:
            entries.append(BillingLedgerEntry(**payload))
        except Exception as exc:
            logging.warning("Skipping invalid billing ledger payload: %s", exc)

    entries.sort(key=lambda entry: entry.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return entries[: max(0, int(limit or 0))] if limit else entries


def get_consumption_record_by_source_event(account_id: str, source_event_id: str) -> Optional[BillingConsumptionRecord]:
    consumption_collection = _get_collection(BILLING_CONSUMPTION_COLLECTION)
    if consumption_collection is None:
        return None

    try:
        documents = consumption_collection.stream()
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore consumption lookup skipped because query failed: %s", exc)
        return None

    for document in documents:
        payload = document.to_dict() or {}
        if str(payload.get("account_id") or "").strip() != account_id:
            continue
        if str(payload.get("source_event_id") or "").strip() != str(source_event_id or "").strip():
            continue
        try:
            return BillingConsumptionRecord(**payload)
        except Exception as exc:
            logging.warning("Skipping invalid billing consumption payload: %s", exc)
            return None

    return None


def append_consumption_record(record: BillingConsumptionRecord) -> BillingConsumptionRecord:
    consumption_collection = _get_collection(BILLING_CONSUMPTION_COLLECTION)
    if consumption_collection is None:
        return record

    payload = record.model_dump()
    if payload.get("consumption_id") is None:
        payload["consumption_id"] = str(uuid4())
    if payload.get("created_at") is None:
        payload["created_at"] = _utcnow()
    normalized_record = BillingConsumptionRecord(**payload)
    _safe_set(
        consumption_collection.document(normalized_record.consumption_id),
        _serialize_value(normalized_record),
        operation="billing_consumption_append",
        merge=False,
    )
    return normalized_record


def get_billing_allocation(account_id: str, user_id: str) -> Optional[BillingAllocation]:
    allocation_collection = _get_collection(BILLING_ALLOCATIONS_COLLECTION)
    if allocation_collection is None:
        return None

    allocation_id = build_allocation_id(account_id, user_id)
    return _safe_get_model(
        allocation_collection.document(allocation_id),
        BillingAllocation,
        operation="billing_allocation_read",
    )


def upsert_billing_allocation(allocation: BillingAllocation) -> BillingAllocation:
    allocation_collection = _get_collection(BILLING_ALLOCATIONS_COLLECTION)
    if allocation_collection is None:
        return allocation

    payload = allocation.model_dump()
    payload["updated_at"] = _utcnow()
    if payload.get("created_at") is None:
        payload["created_at"] = _utcnow()
    normalized_allocation = BillingAllocation(**payload)
    _safe_set(
        allocation_collection.document(normalized_allocation.allocation_id),
        _serialize_value(normalized_allocation),
        operation="billing_allocation_upsert",
        merge=True,
    )
    return normalized_allocation


def list_billing_allocations(account_id: str) -> list[BillingAllocation]:
    allocation_collection = _get_collection(BILLING_ALLOCATIONS_COLLECTION)
    if allocation_collection is None:
        return []

    try:
        documents = allocation_collection.stream()
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore billing allocation list skipped because query failed: %s", exc)
        return []

    allocations: list[BillingAllocation] = []
    for document in documents:
        payload = document.to_dict() or {}
        if str(payload.get("account_id") or "").strip() != account_id:
            continue
        try:
            allocations.append(BillingAllocation(**payload))
        except Exception as exc:
            logging.warning("Skipping invalid billing allocation payload: %s", exc)

    allocations.sort(key=lambda allocation: allocation.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return allocations
