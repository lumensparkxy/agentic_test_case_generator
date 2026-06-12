from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..adapters.jira import JiraAdapter
from ..config import get_jira_settings
from ..models import (
    AuthUser,
    JiraConnectionInput,
    JiraConnectionStatusResponse,
    JiraConnectionSummary,
    JiraStoredConnection,
)
from .credential_crypto import CredentialCipher, DecryptedCredential, EncryptedCredential
from .firestore_repository import get_optional_firestore_collection, get_required_firestore_collection

JIRA_CONNECTIONS_COLLECTION = "jira_user_connections"
JIRA_TOKEN_KEY_ID_FIELD = "api_token_key_id"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_collection(*, required: bool) -> Optional[object]:
    if required:
        return get_required_firestore_collection(
            JIRA_CONNECTIONS_COLLECTION,
            unavailable_message="Firestore client unavailable for JIRA connection persistence",
        )
    return get_optional_firestore_collection(
        JIRA_CONNECTIONS_COLLECTION,
        unavailable_message="Firestore unavailable for JIRA connection reads",
    )


def _build_cipher() -> CredentialCipher:
    settings = get_jira_settings()
    return CredentialCipher(
        provider="jira",
        primary_secret=settings.connection_secret_key,
        previous_secrets=settings.previous_connection_secret_keys,
        missing_secret_message="JIRA connection encryption is not configured. Set JIRA_CONNECTION_SECRET_KEY or JWT_SECRET_KEY.",
        decrypt_failure_message="Stored JIRA API token could not be decrypted with the configured secret",
    )


def _encrypt_api_token(api_token: str) -> EncryptedCredential:
    return _build_cipher().encrypt(api_token)


def _decrypt_api_token(ciphertext: str, *, key_id: str | None = None) -> DecryptedCredential:
    return _build_cipher().decrypt(ciphertext, key_id=key_id)


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

    decrypted_token = _decrypt_api_token(encrypted_token, key_id=payload.get(JIRA_TOKEN_KEY_ID_FIELD) or payload.get("credential_key_id") or None)

    return JiraStoredConnection(
        base_url=payload.get("base_url"),
        email=payload.get("email"),
        api_token=decrypted_token.plaintext,
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
    encrypted_token = _encrypt_api_token(payload.api_token)

    collection = _get_collection(required=True)
    collection.document(_document_id(current_user)).set(
        {
            "user_id": current_user.sub,
            "base_url": str(connection_summary.base_url).rstrip("/"),
            "email": connection_summary.email,
            "encrypted_api_token": encrypted_token.ciphertext,
            JIRA_TOKEN_KEY_ID_FIELD: encrypted_token.key_id,
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


def reencrypt_jira_connection_credentials(*, dry_run: bool = False) -> dict[str, int | str | bool]:
    collection = _get_collection(required=True)
    cipher = _build_cipher()
    result: dict[str, int | str | bool] = {
        "provider": "jira",
        "dry_run": dry_run,
        "checked": 0,
        "reencrypted": 0,
        "metadata_updated": 0,
        "skipped": 0,
        "failed": 0,
    }

    for snapshot in collection.stream():
        result["checked"] = int(result["checked"]) + 1
        payload = snapshot.to_dict() or {}
        encrypted_token = str(payload.get("encrypted_api_token") or "").strip()
        if not encrypted_token:
            result["skipped"] = int(result["skipped"]) + 1
            continue

        stored_key_id = str(payload.get(JIRA_TOKEN_KEY_ID_FIELD) or payload.get("credential_key_id") or "").strip() or None
        try:
            decrypted = cipher.decrypt(encrypted_token, key_id=stored_key_id)
        except RuntimeError:
            result["failed"] = int(result["failed"]) + 1
            continue

        updates: dict[str, object] = {}
        if decrypted.used_primary:
            if stored_key_id != cipher.primary_key_id:
                updates[JIRA_TOKEN_KEY_ID_FIELD] = cipher.primary_key_id
                updates["credential_reencrypted_at"] = _utcnow()
                result["metadata_updated"] = int(result["metadata_updated"]) + 1
            else:
                result["skipped"] = int(result["skipped"]) + 1
        else:
            encrypted = cipher.encrypt(decrypted.plaintext)
            updates.update(
                {
                    "encrypted_api_token": encrypted.ciphertext,
                    JIRA_TOKEN_KEY_ID_FIELD: encrypted.key_id,
                    "credential_reencrypted_at": _utcnow(),
                }
            )
            result["reencrypted"] = int(result["reencrypted"]) + 1

        if updates and not dry_run:
            collection.document(snapshot.id).set(updates, merge=True)

    if int(result["failed"]):
        logging.warning("JIRA credential re-encryption skipped %s record(s) that could not be decrypted.", result["failed"])

    return result


def delete_jira_connection(*, current_user: AuthUser) -> None:
    collection = _get_collection(required=True)
    collection.document(_document_id(current_user)).delete()


def get_jira_adapter_for_user(*, current_user: AuthUser) -> JiraAdapter:
    connection = get_decrypted_jira_connection(current_user=current_user, required=True)
    settings = get_jira_settings()
    return JiraAdapter.from_connection(connection, timeout_seconds=settings.api_timeout_seconds)
