from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import AUTH_TOKEN_MODE_FIREBASE_ONLY, AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT, get_auth_settings
from ..models import AuthUser
from .identity import normalize_roles
from .firebase_auth import verify_firebase_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def _auth_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _get_signing_config() -> tuple[str, str, int]:
    settings = get_auth_settings()
    if not settings.jwt_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET_KEY is not configured on the server",
        )
    return settings.jwt_secret_key, settings.jwt_algorithm, settings.jwt_expiration_minutes


def create_access_token(user: AuthUser) -> Tuple[str, int]:
    """Create a signed access token for the authenticated user."""
    secret, algorithm, expiration_minutes = _get_signing_config()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(1, expiration_minutes))

    payload = {
        "sub": user.sub,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "organization_domain": user.organization_domain,
        "tenant_id": user.tenant_id,
        "roles": list(user.roles or []),
        "is_org_admin": user.is_org_admin,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token, max(1, expiration_minutes) * 60


def decode_access_token(token: str) -> AuthUser:
    """Decode and validate an application access token."""
    secret, algorithm, _ = _get_signing_config()

    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise _auth_error("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _auth_error("Invalid access token") from exc

    try:
        return AuthUser(
            sub=str(payload["sub"]),
            email=str(payload["email"]) if payload.get("email") is not None else None,
            name=str(payload.get("name") or payload.get("email") or payload["sub"]),
            picture=payload.get("picture"),
            organization_domain=str(payload.get("organization_domain") or "").strip().lower() or None,
            tenant_id=str(payload.get("tenant_id") or "").strip() or None,
            roles=normalize_roles(payload.get("roles"), payload.get("role"), payload.get("tenant_role")),
            is_org_admin=bool(payload.get("is_org_admin") or payload.get("org_admin") or payload.get("tenant_admin")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _auth_error("Invalid access token payload") from exc


def try_decode_legacy_access_token(token: str) -> Optional[AuthUser]:
    settings = get_auth_settings()
    if not settings.jwt_secret_key:
        return None

    try:
        return decode_access_token(token)
    except HTTPException as exc:
        if exc.detail == "Access token has expired":
            raise
        if exc.detail in {"Invalid access token", "Invalid access token payload"}:
            return None
        raise


def _token_matches_backend_jwt(token: str) -> bool:
    settings = get_auth_settings()
    if not settings.jwt_secret_key:
        return False

    try:
        decode_access_token(token)
    except HTTPException as exc:
        if exc.detail in {"Access token has expired", "Invalid access token payload"}:
            return True
        if exc.detail == "Invalid access token":
            return False
        raise
    return True


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthUser:
    """FastAPI dependency that resolves the current user from Authorization Bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _auth_error("Missing bearer access token")

    settings = get_auth_settings()
    if settings.auth_token_mode == AUTH_TOKEN_MODE_FIREBASE_ONLY:
        if _token_matches_backend_jwt(credentials.credentials):
            raise _auth_error("Backend-issued access tokens are disabled in AUTH_TOKEN_MODE=firebase-only")
        return verify_firebase_access_token(credentials.credentials)

    if settings.auth_token_mode != AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT:
        raise _auth_error("Unsupported authentication token mode")

    legacy_user = try_decode_legacy_access_token(credentials.credentials)
    if legacy_user is not None:
        return legacy_user

    return verify_firebase_access_token(credentials.credentials)
