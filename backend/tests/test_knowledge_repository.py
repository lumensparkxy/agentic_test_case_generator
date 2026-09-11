from copy import deepcopy
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from app.services import knowledge_repository as repository


class Document:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def document(self, key):
        return Document(self.client, self.path + "/" + key)

    collection = document

    def get(self, transaction=None):
        if transaction and transaction.writes:
            raise AssertionError("Firestore requires reads before writes")
        return SimpleNamespace(to_dict=lambda: deepcopy(self.client.data.get(self.path)))


class Transaction:
    def __init__(self, client):
        self.client, self.writes = client, []

    def set(self, doc, value):
        self.writes.append(doc.path)
        self.client.data[doc.path] = deepcopy(value)


class Client:
    def __init__(self):
        self.data = {"qa_projects/p": {"owner_user_id": "owner"}}

    def collection(self, name):
        return Document(self, name)

    def transaction(self):
        return Transaction(self)


class KnowledgeRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.client_patch = patch.object(repository, "get_required_firestore_client", return_value=self.client)
        self.transaction_patch = patch.object(repository, "transactional", side_effect=lambda f: f)
        self.client_patch.start()
        self.transaction_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.transaction_patch.stop)
        self.repo = repository.FirestoreKnowledgeRepository()

    def test_transaction_checks_owner_before_writes(self):
        with self.assertRaises(HTTPException) as caught:
            self.repo.mutate("another", "request", "fingerprint", lambda state: state, project_id="p")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(list(self.client.data), ["qa_projects/p"])

    def test_receipts_contain_references_and_conflict_when_input_changes(self):
        def create(state):
            state["entries"]["m"] = {"text": "approved private wording"}
            return "m"

        first = self.repo.mutate("owner", "request", "fingerprint", create, project_id="p")
        second = self.repo.mutate("owner", "request", "fingerprint", lambda state: self.fail("Retry must not reapply"), project_id="p")
        self.assertEqual(first, second)
        receipts = [v for k, v in self.client.data.items() if "/requests/" in k]
        self.assertEqual(receipts, [{"fingerprint": "fingerprint", "entry_id": "m"}])
        with self.assertRaises(HTTPException) as caught:
            self.repo.mutate("owner", "request", "changed", create, project_id="p")
        self.assertEqual(caught.exception.status_code, 409)

    def test_size_limit_fails_before_persisting_any_entry_or_receipt(self):
        def oversized(state):
            state["entries"]["m"] = "x" * 750001
            return "m"

        with self.assertRaises(HTTPException) as caught:
            self.repo.mutate("owner", "request", "fingerprint", oversized, project_id="p")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(list(self.client.data), ["qa_projects/p"])
