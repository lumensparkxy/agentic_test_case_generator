from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    sub: str
    email: Optional[str] = None
    name: str
    picture: Optional[str] = None
    provider: Optional[str] = None
    email_verified: Optional[bool] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    is_org_admin: bool = False


class GoogleLoginRequest(BaseModel):
    credential: str
    client_id: Optional[str] = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: AuthUser


class LogoutResponse(BaseModel):
    status: str = "ok"


__all__ = [
    "AuthUser",
    "GoogleLoginRequest",
    "AuthTokenResponse",
    "LogoutResponse",
]
