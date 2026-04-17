import logging

from fastapi import HTTPException, status
from firebase_admin import auth as firebase_admin_auth

from ..models import AuthUser
from .identity import extract_email_domain, is_public_email_domain, normalize_roles
from ..services.firebase_admin import get_firebase_admin_app


def _auth_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_firebase_access_token(token: str) -> AuthUser:
    if not token:
        raise _auth_error("Missing bearer access token")

    try:
        app = get_firebase_admin_app()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    try:
        decoded_token = firebase_admin_auth.verify_id_token(token, app=app, check_revoked=True)
    except firebase_admin_auth.ExpiredIdTokenError as exc:
        raise _auth_error("Firebase access token has expired") from exc
    except firebase_admin_auth.RevokedIdTokenError as exc:
        raise _auth_error("Firebase access token has been revoked") from exc
    except firebase_admin_auth.InvalidIdTokenError as exc:
        raise _auth_error("Invalid Firebase access token") from exc
    except Exception as exc:  # pragma: no cover - firebase-admin raises multiple credential/runtime errors
        logging.warning("Firebase token verification failed: %s", exc)
        raise _auth_error("Invalid Firebase access token") from exc

    subject = str(decoded_token.get("uid") or decoded_token.get("sub") or "").strip()
    if not subject:
        raise _auth_error("Firebase access token payload is missing a subject")

    firebase_claims = decoded_token.get("firebase") or {}
    provider = firebase_claims.get("sign_in_provider") if isinstance(firebase_claims, dict) else None
    email = decoded_token.get("email") or None
    name = decoded_token.get("name") or email or subject
    tenant_id = (
        decoded_token.get("tenant_id")
        or decoded_token.get("tenant")
        or (firebase_claims.get("tenant") if isinstance(firebase_claims, dict) else None)
        or None
    )
    organization_domain = (
        str(decoded_token.get("organization_domain") or decoded_token.get("org_domain") or "").strip().lower() or None
    )
    if not organization_domain and email:
        email_domain = extract_email_domain(str(email).strip().lower())
        if not is_public_email_domain(email_domain):
            organization_domain = email_domain
    roles = normalize_roles(decoded_token.get("roles"), decoded_token.get("role"), decoded_token.get("tenant_role"))
    is_org_admin = bool(
        decoded_token.get("is_org_admin")
        or decoded_token.get("org_admin")
        or decoded_token.get("tenant_admin")
        or decoded_token.get("is_tenant_admin")
    )

    return AuthUser(
        sub=subject,
        email=email,
        name=name,
        picture=decoded_token.get("picture"),
        provider=provider,
        email_verified=decoded_token.get("email_verified"),
        organization_domain=organization_domain,
        tenant_id=str(tenant_id).strip() or None if tenant_id is not None else None,
        roles=roles,
        is_org_admin=is_org_admin,
    )