from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.google_auth import verify_google_credential
from ..auth.jwt_auth import create_access_token, get_current_user
from ..config import AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT, get_auth_settings
from ..models import AuthTokenResponse, AuthUser, GoogleLoginRequest, LogoutResponse

router = APIRouter()


@router.post("/auth/google/login", response_model=AuthTokenResponse)
async def auth_google_login(payload: GoogleLoginRequest) -> AuthTokenResponse:
    settings = get_auth_settings()
    if settings.auth_token_mode != AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="/auth/google/login is disabled unless AUTH_TOKEN_MODE=firebase-or-backend-jwt",
        )

    user_claims = verify_google_credential(payload.credential, settings.google_client_ids, payload.client_id)
    user = AuthUser(**user_claims)
    access_token, expires_in = create_access_token(user)
    return AuthTokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=user,
    )


@router.get("/auth/me", response_model=AuthUser)
async def auth_me(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    return current_user


@router.post("/auth/logout", response_model=LogoutResponse)
async def auth_logout() -> LogoutResponse:
    return LogoutResponse(status="ok")
