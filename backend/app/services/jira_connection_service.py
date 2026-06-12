from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from ..adapters.jira import JiraAdapter
from ..config import get_jira_settings
from ..models import (
    AuthUser,
    JiraConnectionInput,
    JiraConnectionStatusResponse,
    JiraConnectionSummary,
    JiraStoredConnection,
)
from .firebase_admin import get_firestore_client

JIRA_CONNECTIONS_COLLECTION = "jira_user_connections"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_collection(*, required: bool) -> Optional[object]:
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime state
        if required:
            raise RuntimeError("Firestore client unavailable for JIRA connection persistence") from exc
        logging.warning("Firestore unavailable for JIRA connection reads: %s", exc)
        return None
    return client.collection(JIRA_CONNECTIONS_COLLECTION)


def _build_fernet() -> Fernet:
    settings = get_jira_settings()
    raw_secret = str(settings.connection_secret_key or "").strip()
    if not raw_secret:
        raise RuntimeError("JIRA connection encryption is not configured. Set JIRA_CONNECTION_SECRET_KEY or JWT_SECRET_KEY.")
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(raw_secret.encode("utf-8")).digest())
    return Fernet(derived_key)


def _encrypt_api_token(api_token: str) -> str:
    return _build_fernet().encrypt(str(api_token or "").encode("utf-8")).decode("utf-8")


def _decrypt_api_token(ciphertext: str) -> str:
    try:
        return _build_fernet().decrypt(str(ciphertext or "").encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored JIRA API token could not be decrypted with the configured secret") from exc


def _token_hint(api_token: str) -> str:
    normalized = str(api_token or "")
    if len(normalized) >= 4:
        return f"••••{normalized[-4:]}"
    return "configured"


def _document_id(current_user: AuthUser) -> str:
    return str(current_user.sub or "").strip()


def get_jira_connection_status(*, current_user: AuthUser) -> JiraConnectionStatusResponse:
    stored = get_decrypted_jira_connection(current_user=current_user, required=False)
    if stored is None:
        return JiraConnectionStatusResponse(connected=False, connection=None)
    return JiraConnectionStatusResponse(
        connected=True,
        connection=JiraConnectionSummary(**stored.model_dump(exclude={"api_token"})),
    )


def get_decrypted_jira_connection(*, current_user: AuthUser, required: bool = True) -> Optional[JiraStoredConnection]:
    collection = _get_collection(required=False)
    if collection is None:
        if required:
            raise LookupError("No stored JIRA connection was found for this user")
        return None

    snapshot = collection.document(_document_id(current_user)).get()
    if not getattr(snapshot, "exists", False):
        if required:
            raise LookupError("No stored JIRA connection was found for this user")
        return None

    payload = snapshot.to_dict() or {}
    encrypted_token = str(payload.get("encrypted_api_token") or "").strip()
    if not encrypted_token:
        if required:
            raise LookupError("No stored JIRA connection was found for this user")
        return None

    return JiraStoredConnection(
        base_url=payload.get("base_url"),
        email=payload.get("email"),
        api_token=_decrypt_api_token(encrypted_token),
        account_id=payload.get("account_id") or None,
        display_name=payload.get("display_name") or None,
        api_token_hint=payload.get("api_token_hint") or None,
        connected_at=payload.get("connected_at") or None,
        updated_at=payload.get("updated_at") or None,
        last_validated_at=payload.get("last_validated_at") or None,
    )


def upsert_jira_connection(*, current_user: AuthUser, payload: JiraConnectionInput) -> JiraConnectionStatusResponse:
    settings = get_jira_settings()
    adapter = JiraAdapter(
        base_url=str(payload.base_url),
        email=payload.email,
        api_token=payload.api_token,
        timeout_seconds=settings.api_timeout_seconds,
    )
    validated_account = adapter.validate_connection()
    existing = get_decrypted_jira_connection(current_user=current_user, required=False)
    now = _utcnow()

    connection_summary = JiraConnectionSummary(
        base_url=str(payload.base_url).rstrip("/"),
        email=payload.email,
        account_id=str(validated_account.get("accountId") or "") or None,
        display_name=str(validated_account.get("displayName") or validated_account.get("emailAddress") or "") or None,
        api_token_hint=_token_hint(payload.api_token),
        connected_at=(existing.connected_at if existing and existing.connected_at else now),
        updated_at=now,
        last_validated_at=now,
    )

    collection = _get_collection(required=True)
    collection.document(_document_id(current_user)).set(
        {
            "user_id": current_user.sub,
            "base_url": str(connection_summary.base_url).rstrip("/"),
            "email": connection_summary.email,
            "encrypted_api_token": _encrypt_api_token(payload.api_token),
            "api_token_hint": connection_summary.api_token_hint,
            "account_id": connection_summary.account_id,
            "display_name": connection_summary.display_name,
            "connected_at": connection_summary.connected_at,
            "updated_at": connection_summary.updated_at,
            "last_validated_at": connection_summary.last_validated_at,
            "organization_domain": current_user.organization_domain,
            "tenant_id": current_user.tenant_id,
        },
        merge=True,
    )

    return JiraConnectionStatusResponse(connected=True, connection=connection_summary)


def delete_jira_connection(*, current_user: AuthUser) -> None:
    collection = _get_collection(required=True)
    collection.document(_document_id(current_user)).delete()


def get_jira_adapter_for_user(*, current_user: AuthUser) -> JiraAdapter:
    connection = get_decrypted_jira_connection(current_user=current_user, required=True)
    settings = get_jira_settings()
    return JiraAdapter.from_connection(connection, timeout_seconds=settings.api_timeout_seconds)
