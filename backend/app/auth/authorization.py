from __future__ import annotations

from fastapi import Depends, HTTPException, status

from ..models import AuthUser
from .identity import resolve_organization_domain, user_has_org_admin_access
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