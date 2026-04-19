from __future__ import annotations

from typing import Any, Optional

from ..models import AuthUser

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "google.com",
    "live.com",
    "outlook.com",
    "hotmail.com",
    "msn.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}

ORG_ADMIN_ROLE_NAMES = {
    "admin",
    "owner",
    "org_admin",
    "organization_admin",
    "tenant_admin",
    "tenant_owner",
    "billing_admin",
    "reporting_admin",
}

INTERNAL_BILLING_ADMIN_ROLE_NAMES = {
    "billing_admin",
    "super_admin",
    "platform_admin",
    "platform_owner",
}


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def extract_email_domain(email: str) -> Optional[str]:
    return email.split("@", 1)[1] if "@" in email else None


def is_public_email_domain(domain: Optional[str]) -> bool:
    normalized = str(domain or "").strip().lower()
    return not normalized or normalized in PUBLIC_EMAIL_DOMAINS


def normalize_roles(*raw_values: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        candidate = str(value or "").strip()
        if not candidate:
            return
        lowered = candidate.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        normalized.append(candidate)

    for raw_value in raw_values:
        if raw_value is None:
            continue
        if isinstance(raw_value, str):
            _add(raw_value)
            continue
        if isinstance(raw_value, (list, tuple, set)):
            for item in raw_value:
                _add(item)
            continue
        _add(raw_value)

    return normalized


def resolve_organization_domain(user: Optional[AuthUser]) -> Optional[str]:
    if user is None:
        return None

    explicit_domain = str(user.organization_domain or "").strip().lower()
    if explicit_domain:
        return explicit_domain

    email_domain = extract_email_domain(normalize_email(user.email))
    if is_public_email_domain(email_domain):
        return None

    return email_domain


def user_has_org_admin_access(user: Optional[AuthUser]) -> bool:
    if user is None:
        return False

    normalized_roles = {role.strip().lower() for role in (user.roles or []) if str(role).strip()}
    return bool(user.is_org_admin or normalized_roles.intersection(ORG_ADMIN_ROLE_NAMES))


def user_has_internal_billing_admin_role(user: Optional[AuthUser]) -> bool:
    if user is None:
        return False

    normalized_roles = {role.strip().lower() for role in (user.roles or []) if str(role).strip()}
    return bool(normalized_roles.intersection(INTERNAL_BILLING_ADMIN_ROLE_NAMES))