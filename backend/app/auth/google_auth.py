import logging
from typing import Any, Dict, Sequence

from fastapi import HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def _normalize_audiences(audiences: str | Sequence[str]) -> list[str]:
    raw_values = [audiences] if isinstance(audiences, str) else list(audiences)
    normalized: list[str] = []
    seen: set[str] = set()

    for value in raw_values:
        candidate = (value or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)

    return normalized


def verify_google_credential(
    credential: str,
    audiences: str | Sequence[str],
    requested_client_id: str | None = None,
) -> Dict[str, Any]:
    """Verify a Google Identity Services credential (ID token) and return normalized user claims."""
    allowed_audiences = _normalize_audiences(audiences)
    requested_client_id = (requested_client_id or "").strip() or None

    if not credential:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Google credential token")
    if not allowed_audiences:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_CLIENT_ID or GOOGLE_CLIENT_IDS is not configured on the server",
        )

    try:
        id_info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            audience=None,
        )
    except ValueError as exc:
        logging.warning("Google credential verification failed before audience validation: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google credential token") from exc

    issuer = id_info.get("iss")
    if issuer not in VALID_ISSUERS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token issuer")

    token_audience = str(id_info.get("aud") or "").strip()
    if token_audience not in allowed_audiences:
        logging.warning(
            "Google credential audience mismatch: token_aud=%s allowed=%s requested_client_id=%s",
            token_audience or "<missing>",
            allowed_audiences,
            requested_client_id or "<missing>",
        )
        expected_list = ", ".join(allowed_audiences)
        detail_parts = [
            "Google credential audience mismatch.",
            f"Expected one of: {expected_list}.",
            f"Received: {token_audience or 'unknown'}.",
        ]
        if requested_client_id and requested_client_id not in allowed_audiences:
            detail_parts.append(f"Frontend sent client ID: {requested_client_id}.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=" ".join(detail_parts),
        )

    if requested_client_id and requested_client_id != token_audience:
        logging.warning(
            "Google credential client hint mismatch: token_aud=%s requested_client_id=%s",
            token_audience,
            requested_client_id,
        )

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
