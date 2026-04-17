import logging

from fastapi import HTTPException, status
from firebase_admin import auth as firebase_admin_auth

from ..models import AuthUser
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

    return AuthUser(
        sub=subject,
        email=email,
        name=name,
        picture=decoded_token.get("picture"),
        provider=provider,
        email_verified=decoded_token.get("email_verified"),
    )