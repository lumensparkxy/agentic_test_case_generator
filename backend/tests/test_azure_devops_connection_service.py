from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import AzureDevOpsSettings
from app.models import AuthUser, AzureDevOpsConnectionInput
from app.services.azure_devops_connection_service import (
    AZURE_DEVOPS_TOKEN_KEY_ID_FIELD,
    delete_azure_devops_connection,
    get_azure_devops_connection_status,
    get_decrypted_azure_devops_connection,
    reencrypt_azure_devops_connection_credentials,
    upsert_azure_devops_connection,
)


class FakeSnapshot:
    def __init__(self, payload, doc_id=None):
        self._payload = payload
        self.id = doc_id

    @property
    def exists(self):
        return self._payload is not None

    def to_dict(self):
        return dict(self._payload or {})


class FakeDocument:
    def __init__(self, store, doc_id):
        self.store = store
        self.doc_id = doc_id

    def set(self, payload, merge=False):
        existing = dict(self.store.get(self.doc_id, {})) if merge else {}
        existing.update(payload)
        self.store[self.doc_id] = existing

    def get(self):
        return FakeSnapshot(self.store.get(self.doc_id), doc_id=self.doc_id)

    def delete(self):
        self.store.pop(self.doc_id, None)


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def document(self, doc_id):
        return FakeDocument(self.store, doc_id)

    def stream(self):
        return [FakeSnapshot(payload, doc_id=doc_id) for doc_id, payload in self.store.items()]


class FakeFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return FakeCollection(self.collections.setdefault(name, {}))


class AzureDevOpsConnectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")
        self.settings = AzureDevOpsSettings(
            connection_secret_key="azure-secret-key",
            api_timeout_seconds=15,
            api_version="7.1",
            project_page_size=50,
            work_item_page_size=50,
        )

    def test_upsert_and_get_connection_round_trip_encrypts_pat_and_normalizes_project_url(self) -> None:
        client = FakeFirestoreClient()
        payload = AzureDevOpsConnectionInput(
            organization_url="https://dev.azure.com/acme/Payments",
            personal_access_token="azure-pat-1234",
            account_email="qa@acme.com",
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.azure_devops_connection_service.get_azure_devops_settings", return_value=self.settings):
                with patch("app.services.azure_devops_connection_service.AzureDevOpsAdapter.validate_connection", return_value={"organization": "acme"}):
                    response = upsert_azure_devops_connection(current_user=self.user, payload=payload)
                    stored = get_decrypted_azure_devops_connection(current_user=self.user)

        persisted = client.collections["azure_devops_user_connections"][self.user.sub]
        self.assertTrue(response.connected)
        self.assertEqual(str(response.connection.organization_url).rstrip("/"), "https://dev.azure.com/acme")
        self.assertEqual(response.connection.organization, "acme")
        self.assertEqual(response.connection.default_project, "Payments")
        self.assertEqual(response.connection.token_hint, "••••1234")
        self.assertNotEqual(persisted["encrypted_personal_access_token"], "azure-pat-1234")
        self.assertIn(AZURE_DEVOPS_TOKEN_KEY_ID_FIELD, persisted)
        self.assertEqual(stored.personal_access_token, "azure-pat-1234")

    def test_decrypts_previous_key_record_and_reencrypts_with_primary_key(self) -> None:
        client = FakeFirestoreClient()
        payload = AzureDevOpsConnectionInput(
            organization_url="https://dev.azure.com/acme/Payments",
            personal_access_token="azure-pat-1234",
            account_email="qa@acme.com",
        )
        old_settings = self.settings.model_copy(update={"connection_secret_key": "old-azure-secret"})
        new_settings = self.settings.model_copy(
            update={
                "connection_secret_key": "new-azure-secret",
                "previous_connection_secret_keys": ["old-azure-secret"],
            }
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.azure_devops_connection_service.get_azure_devops_settings", return_value=old_settings):
                with patch(
                    "app.services.azure_devops_connection_service.AzureDevOpsAdapter.validate_connection",
                    return_value={"organization": "acme"},
                ):
                    upsert_azure_devops_connection(current_user=self.user, payload=payload)

            persisted = client.collections["azure_devops_user_connections"][self.user.sub]
            old_ciphertext = persisted["encrypted_personal_access_token"]
            old_key_id = persisted[AZURE_DEVOPS_TOKEN_KEY_ID_FIELD]

            with patch("app.services.azure_devops_connection_service.get_azure_devops_settings", return_value=new_settings):
                status = get_azure_devops_connection_status(current_user=self.user)
                stored = get_decrypted_azure_devops_connection(current_user=self.user)
                dry_run = reencrypt_azure_devops_connection_credentials(dry_run=True)
                after_dry_run_ciphertext = persisted["encrypted_personal_access_token"]
                result = reencrypt_azure_devops_connection_credentials()
                rotated = get_decrypted_azure_devops_connection(current_user=self.user)

        updated = client.collections["azure_devops_user_connections"][self.user.sub]
        self.assertTrue(status.connected)
        self.assertEqual(status.connection.token_hint, "••••1234")
        self.assertEqual(stored.personal_access_token, "azure-pat-1234")
        self.assertEqual(dry_run["dry_run"], True)
        self.assertEqual(dry_run["reencrypted"], 1)
        self.assertEqual(after_dry_run_ciphertext, old_ciphertext)
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["reencrypted"], 1)
        self.assertNotEqual(updated["encrypted_personal_access_token"], old_ciphertext)
        self.assertNotEqual(updated[AZURE_DEVOPS_TOKEN_KEY_ID_FIELD], old_key_id)
        self.assertNotIn("azure-pat-1234", str(updated))
        self.assertEqual(rotated.personal_access_token, "azure-pat-1234")

    def test_invalid_ciphertext_reencrypt_failure_does_not_log_token_material(self) -> None:
        client = FakeFirestoreClient()
        client.collections["azure_devops_user_connections"] = {
            self.user.sub: {
                "encrypted_personal_access_token": "not-a-valid-fernet-token",
                "token_hint": "••••1234",
            }
        }

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.azure_devops_connection_service.get_azure_devops_settings", return_value=self.settings):
                with self.assertLogs(level="WARNING") as logs:
                    result = reencrypt_azure_devops_connection_credentials()

        self.assertEqual(result["failed"], 1)
        serialized_logs = "\n".join(logs.output)
        self.assertIn("could not be decrypted", serialized_logs)
        self.assertNotIn("azure-pat", serialized_logs)

    def test_connection_status_returns_disconnected_when_no_document_exists(self) -> None:
        client = FakeFirestoreClient()

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            response = get_azure_devops_connection_status(current_user=self.user)

        self.assertFalse(response.connected)
        self.assertIsNone(response.connection)

    def test_delete_connection_removes_stored_document(self) -> None:
        client = FakeFirestoreClient()
        payload = AzureDevOpsConnectionInput(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="azure-pat-1234",
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.azure_devops_connection_service.get_azure_devops_settings", return_value=self.settings):
                with patch("app.services.azure_devops_connection_service.AzureDevOpsAdapter.validate_connection", return_value={"organization": "acme"}):
                    upsert_azure_devops_connection(current_user=self.user, payload=payload)
                    delete_azure_devops_connection(current_user=self.user)

        self.assertEqual(client.collections["azure_devops_user_connections"], {})


if __name__ == "__main__":
    unittest.main()
