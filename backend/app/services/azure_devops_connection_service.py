from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from ..adapters.azure_devops import AzureDevOpsAdapter, normalize_azure_devops_url
from ..config import get_azure_devops_settings
from ..models import (
    AuthUser,
    AzureDevOpsConnectionInput,
    AzureDevOpsConnectionStatusResponse,
    AzureDevOpsConnectionSummary,
    AzureDevOpsStoredConnection,
)
from .firebase_admin import get_firestore_client

AZURE_DEVOPS_CONNECTIONS_COLLECTION = "azure_devops_user_connections"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_collection(*, required: bool) -> Optional[object]:
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime state
        if required:
            raise RuntimeError("Firestore client unavailable for Azure DevOps connection persistence") from exc
        logging.warning("Firestore unavailable for Azure DevOps connection reads: %s", exc)
        return None
    return client.collection(AZURE_DEVOPS_CONNECTIONS_COLLECTION)


def _build_fernet() -> Fernet:
    settings = get_azure_devops_settings()
    raw_secret = str(settings.connection_secret_key or "").strip()
    if not raw_secret:
        raise RuntimeError(
            "Azure DevOps connection encryption is not configured. Set AZURE_DEVOPS_CONNECTION_SECRET_KEY or JWT_SECRET_KEY."
        )
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(raw_secret.encode("utf-8")).digest())
    return Fernet(derived_key)


def _encrypt_pat(personal_access_token: str) -> str:
    return _build_fernet().encrypt(str(personal_access_token or "").encode("utf-8")).decode("utf-8")


def _decrypt_pat(ciphertext: str) -> str:
    try:
        return _build_fernet().decrypt(str(ciphertext or "").encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored Azure DevOps PAT could not be decrypted with the configured secret") from exc


def _token_hint(personal_access_token: str) -> str:
    normalized = str(personal_access_token or "")
    if len(normalized) >= 4:
        return f"••••{normalized[-4:]}"
    return "configured"


def _document_id(current_user: AuthUser) -> str:
    return str(current_user.sub or "").strip()


def get_azure_devops_connection_status(*, current_user: AuthUser) -> AzureDevOpsConnectionStatusResponse:
    stored = get_decrypted_azure_devops_connection(current_user=current_user, required=False)
    if stored is None:
        return AzureDevOpsConnectionStatusResponse(connected=False, connection=None)
    return AzureDevOpsConnectionStatusResponse(
        connected=True,
        connection=AzureDevOpsConnectionSummary(**stored.model_dump(exclude={"personal_access_token"})),
    )


def get_decrypted_azure_devops_connection(
    *,
    current_user: AuthUser,
    required: bool = True,
) -> Optional[AzureDevOpsStoredConnection]:
    collection = _get_collection(required=False)
    if collection is None:
        if required:
            raise LookupError("No stored Azure DevOps connection was found for this user")
        return None

    snapshot = collection.document(_document_id(current_user)).get()
    if not getattr(snapshot, "exists", False):
        if required:
            raise LookupError("No stored Azure DevOps connection was found for this user")
        return None

    payload = snapshot.to_dict() or {}
    encrypted_token = str(payload.get("encrypted_personal_access_token") or "").strip()
    if not encrypted_token:
        if required:
            raise LookupError("No stored Azure DevOps connection was found for this user")
        return None

    return AzureDevOpsStoredConnection(
        organization_url=payload.get("organization_url"),
        organization=payload.get("organization"),
        default_project=payload.get("default_project") or None,
        personal_access_token=_decrypt_pat(encrypted_token),
        auth_type=payload.get("auth_type") or "pat",
        display_name=payload.get("display_name") or None,
        account_email=payload.get("account_email") or None,
        token_hint=payload.get("token_hint") or None,
        connected_at=payload.get("connected_at") or None,
        updated_at=payload.get("updated_at") or None,
        last_validated_at=payload.get("last_validated_at") or None,
    )


def upsert_azure_devops_connection(
    *,
    current_user: AuthUser,
    payload: AzureDevOpsConnectionInput,
) -> AzureDevOpsConnectionStatusResponse:
    settings = get_azure_devops_settings()
    location = normalize_azure_devops_url(str(payload.organization_url))
    adapter = AzureDevOpsAdapter(
        organization_url=location.organization_url,
        personal_access_token=payload.personal_access_token,
        default_project=location.default_project,
        timeout_seconds=settings.api_timeout_seconds,
        api_version=settings.api_version,
    )
    validated = adapter.validate_connection()
    existing = get_decrypted_azure_devops_connection(current_user=current_user, required=False)
    now = _utcnow()

    display_name = (
        str(payload.display_name or "").strip()
        or str(validated.get("displayName") or validated.get("organization") or location.organization or "").strip()
        or None
    )
    account_email = str(payload.account_email or current_user.email or "").strip() or None
    connection_summary = AzureDevOpsConnectionSummary(
        organization_url=location.organization_url,
        organization=location.organization,
        default_project=location.default_project,
        auth_type="pat",
        display_name=display_name,
        account_email=account_email,
        token_hint=_token_hint(payload.personal_access_token),
        connected_at=(existing.connected_at if existing and existing.connected_at else now),
        updated_at=now,
        last_validated_at=now,
    )

    collection = _get_collection(required=True)
    collection.document(_document_id(current_user)).set(
        {
            "user_id": current_user.sub,
            "organization_url": str(connection_summary.organization_url).rstrip("/"),
            "organization": connection_summary.organization,
            "default_project": connection_summary.default_project,
            "auth_type": "pat",
            "display_name": connection_summary.display_name,
            "account_email": connection_summary.account_email,
            "encrypted_personal_access_token": _encrypt_pat(payload.personal_access_token),
            "token_hint": connection_summary.token_hint,
            "connected_at": connection_summary.connected_at,
            "updated_at": connection_summary.updated_at,
            "last_validated_at": connection_summary.last_validated_at,
            "organization_domain": current_user.organization_domain,
            "tenant_id": current_user.tenant_id,
        },
        merge=True,
    )

    return AzureDevOpsConnectionStatusResponse(connected=True, connection=connection_summary)


def delete_azure_devops_connection(*, current_user: AuthUser) -> None:
    collection = _get_collection(required=True)
    collection.document(_document_id(current_user)).delete()


def get_azure_devops_adapter_for_user(*, current_user: AuthUser) -> AzureDevOpsAdapter:
    connection = get_decrypted_azure_devops_connection(current_user=current_user, required=True)
    settings = get_azure_devops_settings()
    return AzureDevOpsAdapter.from_connection(
        connection,
        timeout_seconds=settings.api_timeout_seconds,
        api_version=settings.api_version,
    )