from typing import Any, Dict

from fastapi import HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def verify_google_credential(credential: str, audience: str) -> Dict[str, Any]:
    """Verify a Google Identity Services credential (ID token) and return normalized user claims."""
    if not credential:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Google credential token")
    if not audience:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_CLIENT_ID is not configured on the server",
        )

    try:
        id_info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            audience=audience,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google credential token") from exc

    issuer = id_info.get("iss")
    if issuer not in VALID_ISSUERS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token issuer")

    sub = id_info.get("sub")
    email = id_info.get("email")
    email_verified = id_info.get("email_verified", False)
    if not sub or not email or not email_verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google account is not eligible for login")

    name = id_info.get("name") or email.split("@")[0]
    return {
        "sub": sub,
        "email": email,
        "name": name,
        "picture": id_info.get("picture"),
    }
