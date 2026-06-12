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
    delete_azure_devops_connection,
    get_azure_devops_connection_status,
    get_decrypted_azure_devops_connection,
    upsert_azure_devops_connection,
)


class FakeSnapshot:
    def __init__(self, payload):
        self._payload = payload

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
        return FakeSnapshot(self.store.get(self.doc_id))

    def delete(self):
        self.store.pop(self.doc_id, None)


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def document(self, doc_id):
        return FakeDocument(self.store, doc_id)


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
        self.assertEqual(stored.personal_access_token, "azure-pat-1234")

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
