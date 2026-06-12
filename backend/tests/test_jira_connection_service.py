from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import JiraSettings
from app.models import AuthUser, JiraConnectionInput
from app.services.jira_connection_service import (
    JIRA_TOKEN_KEY_ID_FIELD,
    delete_jira_connection,
    get_decrypted_jira_connection,
    get_jira_connection_status,
    reencrypt_jira_connection_credentials,
    upsert_jira_connection,
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


class JiraConnectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")
        self.settings = JiraSettings(
            connection_secret_key="jira-secret-key",
            api_timeout_seconds=15,
            project_page_size=50,
            issue_page_size=50,
        )

    def test_upsert_and_get_connection_round_trip_encrypts_api_token(self) -> None:
        client = FakeFirestoreClient()
        payload = JiraConnectionInput(
            base_url="https://acme.atlassian.net",
            email="qa@acme.com",
            api_token="jira-token-1234",
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.jira_connection_service.get_jira_settings", return_value=self.settings):
                with patch(
                    "app.services.jira_connection_service.JiraAdapter.validate_connection", return_value={"accountId": "acct-1", "displayName": "QA User"}
                ):
                    response = upsert_jira_connection(current_user=self.user, payload=payload)
                    stored = get_decrypted_jira_connection(current_user=self.user)

        persisted = client.collections["jira_user_connections"][self.user.sub]
        self.assertTrue(response.connected)
        self.assertEqual(response.connection.display_name, "QA User")
        self.assertEqual(response.connection.api_token_hint, "••••1234")
        self.assertNotEqual(persisted["encrypted_api_token"], "jira-token-1234")
        self.assertIn(JIRA_TOKEN_KEY_ID_FIELD, persisted)
        self.assertEqual(stored.api_token, "jira-token-1234")
        self.assertEqual(stored.account_id, "acct-1")

    def test_decrypts_previous_key_record_and_reencrypts_with_primary_key(self) -> None:
        client = FakeFirestoreClient()
        payload = JiraConnectionInput(
            base_url="https://acme.atlassian.net",
            email="qa@acme.com",
            api_token="jira-token-1234",
        )
        old_settings = self.settings.model_copy(update={"connection_secret_key": "old-jira-secret"})
        new_settings = self.settings.model_copy(
            update={
                "connection_secret_key": "new-jira-secret",
                "previous_connection_secret_keys": ["old-jira-secret"],
            }
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.jira_connection_service.get_jira_settings", return_value=old_settings):
                with patch(
                    "app.services.jira_connection_service.JiraAdapter.validate_connection",
                    return_value={"accountId": "acct-1", "displayName": "QA User"},
                ):
                    upsert_jira_connection(current_user=self.user, payload=payload)

            persisted = client.collections["jira_user_connections"][self.user.sub]
            old_ciphertext = persisted["encrypted_api_token"]
            old_key_id = persisted[JIRA_TOKEN_KEY_ID_FIELD]

            with patch("app.services.jira_connection_service.get_jira_settings", return_value=new_settings):
                status = get_jira_connection_status(current_user=self.user)
                stored = get_decrypted_jira_connection(current_user=self.user)
                result = reencrypt_jira_connection_credentials()
                rotated = get_decrypted_jira_connection(current_user=self.user)

        updated = client.collections["jira_user_connections"][self.user.sub]
        self.assertTrue(status.connected)
        self.assertEqual(status.connection.api_token_hint, "••••1234")
        self.assertEqual(stored.api_token, "jira-token-1234")
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["reencrypted"], 1)
        self.assertNotEqual(updated["encrypted_api_token"], old_ciphertext)
        self.assertNotEqual(updated[JIRA_TOKEN_KEY_ID_FIELD], old_key_id)
        self.assertNotIn("jira-token-1234", str(updated))
        self.assertEqual(rotated.api_token, "jira-token-1234")

    def test_upsert_during_rotation_preserves_connected_at_and_writes_primary_key(self) -> None:
        client = FakeFirestoreClient()
        old_payload = JiraConnectionInput(
            base_url="https://acme.atlassian.net",
            email="qa@acme.com",
            api_token="jira-token-1234",
        )
        new_payload = old_payload.model_copy(update={"api_token": "jira-token-5678"})
        old_settings = self.settings.model_copy(update={"connection_secret_key": "old-jira-secret"})
        new_settings = self.settings.model_copy(
            update={
                "connection_secret_key": "new-jira-secret",
                "previous_connection_secret_keys": ["old-jira-secret"],
            }
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.jira_connection_service.get_jira_settings", return_value=old_settings):
                with patch(
                    "app.services.jira_connection_service.JiraAdapter.validate_connection",
                    return_value={"accountId": "acct-1", "displayName": "QA User"},
                ):
                    original = upsert_jira_connection(current_user=self.user, payload=old_payload)
            old_key_id = client.collections["jira_user_connections"][self.user.sub][JIRA_TOKEN_KEY_ID_FIELD]

            with patch("app.services.jira_connection_service.get_jira_settings", return_value=new_settings):
                with patch(
                    "app.services.jira_connection_service.JiraAdapter.validate_connection",
                    return_value={"accountId": "acct-1", "displayName": "QA User"},
                ):
                    updated = upsert_jira_connection(current_user=self.user, payload=new_payload)
                stored = get_decrypted_jira_connection(current_user=self.user)

        persisted = client.collections["jira_user_connections"][self.user.sub]
        self.assertEqual(updated.connection.connected_at, original.connection.connected_at)
        self.assertEqual(updated.connection.api_token_hint, "••••5678")
        self.assertNotEqual(persisted[JIRA_TOKEN_KEY_ID_FIELD], old_key_id)
        self.assertEqual(stored.api_token, "jira-token-5678")

    def test_missing_previous_key_fails_without_token_leakage(self) -> None:
        client = FakeFirestoreClient()
        payload = JiraConnectionInput(
            base_url="https://acme.atlassian.net",
            email="qa@acme.com",
            api_token="jira-token-1234",
        )
        old_settings = self.settings.model_copy(update={"connection_secret_key": "old-jira-secret"})
        new_settings = self.settings.model_copy(update={"connection_secret_key": "new-jira-secret"})

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.jira_connection_service.get_jira_settings", return_value=old_settings):
                with patch(
                    "app.services.jira_connection_service.JiraAdapter.validate_connection",
                    return_value={"accountId": "acct-1", "displayName": "QA User"},
                ):
                    upsert_jira_connection(current_user=self.user, payload=payload)

            with patch("app.services.jira_connection_service.get_jira_settings", return_value=new_settings):
                with self.assertRaises(RuntimeError) as error:
                    get_decrypted_jira_connection(current_user=self.user)

        self.assertIn("could not be decrypted", str(error.exception))
        self.assertNotIn("jira-token-1234", str(error.exception))

    def test_connection_status_returns_disconnected_when_no_document_exists(self) -> None:
        client = FakeFirestoreClient()

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            response = get_jira_connection_status(current_user=self.user)

        self.assertFalse(response.connected)
        self.assertIsNone(response.connection)

    def test_delete_connection_removes_stored_document(self) -> None:
        client = FakeFirestoreClient()
        payload = JiraConnectionInput(
            base_url="https://acme.atlassian.net",
            email="qa@acme.com",
            api_token="jira-token-1234",
        )

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            with patch("app.services.jira_connection_service.get_jira_settings", return_value=self.settings):
                with patch(
                    "app.services.jira_connection_service.JiraAdapter.validate_connection", return_value={"accountId": "acct-1", "displayName": "QA User"}
                ):
                    upsert_jira_connection(current_user=self.user, payload=payload)
                    delete_jira_connection(current_user=self.user)

        self.assertEqual(client.collections["jira_user_connections"], {})


if __name__ == "__main__":
    unittest.main()
