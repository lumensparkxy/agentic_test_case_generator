from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    key_id: str


@dataclass(frozen=True)
class DecryptedCredential:
    plaintext: str
    key_id: str
    used_primary: bool


@dataclass(frozen=True)
class _CredentialKey:
    key_id: str
    fernet: Fernet
    primary: bool


def _derive_fernet(secret: str) -> Fernet:
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(derived_key)


def _derive_key_id(*, provider: str, secret: str) -> str:
    digest = hashlib.sha256(f"{provider}:{secret}".encode("utf-8")).hexdigest()
    return digest[:16]


def _dedupe_secrets(primary_secret: str, previous_secrets: Iterable[str]) -> list[str]:
    seen = {primary_secret}
    deduped: list[str] = []
    for secret in previous_secrets:
        normalized = str(secret or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


class CredentialCipher:
    def __init__(
        self,
        *,
        provider: str,
        primary_secret: str,
        previous_secrets: Iterable[str] = (),
        missing_secret_message: str,
        decrypt_failure_message: str,
    ) -> None:
        normalized_primary = str(primary_secret or "").strip()
        if not normalized_primary:
            raise RuntimeError(missing_secret_message)

        self._decrypt_failure_message = decrypt_failure_message
        self.primary_key = _CredentialKey(
            key_id=_derive_key_id(provider=provider, secret=normalized_primary),
            fernet=_derive_fernet(normalized_primary),
            primary=True,
        )
        self.previous_keys = [
            _CredentialKey(
                key_id=_derive_key_id(provider=provider, secret=secret),
                fernet=_derive_fernet(secret),
                primary=False,
            )
            for secret in _dedupe_secrets(normalized_primary, previous_secrets)
        ]

    @property
    def primary_key_id(self) -> str:
        return self.primary_key.key_id

    def encrypt(self, plaintext: str) -> EncryptedCredential:
        ciphertext = self.primary_key.fernet.encrypt(str(plaintext or "").encode("utf-8")).decode("utf-8")
        return EncryptedCredential(ciphertext=ciphertext, key_id=self.primary_key.key_id)

    def decrypt(self, ciphertext: str, *, key_id: str | None = None) -> DecryptedCredential:
        token = str(ciphertext or "").encode("utf-8")
        for key in self._ordered_keys(key_id=key_id):
            try:
                plaintext = key.fernet.decrypt(token).decode("utf-8")
                return DecryptedCredential(plaintext=plaintext, key_id=key.key_id, used_primary=key.primary)
            except InvalidToken:
                continue
        raise RuntimeError(self._decrypt_failure_message)

    def _ordered_keys(self, *, key_id: str | None) -> list[_CredentialKey]:
        keys = [self.primary_key, *self.previous_keys]
        if not key_id:
            return keys
        matching = [key for key in keys if key.key_id == key_id]
        remaining = [key for key in keys if key.key_id != key_id]
        return [*matching, *remaining]
