from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import get_auth_settings
from ..models import AuthUser

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
            email=str(payload["email"]),
            name=str(payload.get("name") or payload["email"]),
            picture=payload.get("picture"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _auth_error("Invalid access token payload") from exc


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthUser:
    """FastAPI dependency that resolves the current user from Authorization Bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _auth_error("Missing bearer access token")
    return decode_access_token(credentials.credentials)
