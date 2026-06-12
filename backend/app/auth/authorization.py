from __future__ import annotations

from fastapi import Depends, HTTPException, status

from ..config import get_billing_settings
from ..models import AuthUser
from .identity import normalize_email, resolve_organization_domain, user_has_internal_billing_admin_role, user_has_org_admin_access
from .jwt_auth import get_current_user


def require_org_admin(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if not resolve_organization_domain(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization reports are only available for managed organization accounts.",
        )

    if not user_has_org_admin_access(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin privileges are required for organization usage reports.",
        )

    return current_user


def require_billing_admin(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    settings = get_billing_settings()
    normalized_email = normalize_email(current_user.email)
    normalized_admin_emails = {normalize_email(email) for email in (settings.admin_emails or [])}

    if normalized_email and normalized_email in normalized_admin_emails:
        return current_user

    if user_has_internal_billing_admin_role(current_user):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Billing admin privileges are required for this action.",
    )


def require_org_or_billing_admin(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    settings = get_billing_settings()
    normalized_email = normalize_email(current_user.email)
    normalized_admin_emails = {normalize_email(email) for email in (settings.admin_emails or [])}

    if (normalized_email and normalized_email in normalized_admin_emails) or user_has_internal_billing_admin_role(current_user):
        return current_user

    return require_org_admin(current_user)
