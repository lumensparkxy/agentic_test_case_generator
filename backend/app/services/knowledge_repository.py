"""Firestore boundary: a bounded owner document serializes knowledge changes.

Receipts contain identifiers only, so deleting an entry cannot leave its text
in an idempotency response cache. The owner document is capped below Firestore's
size limit; future adapters may split records without changing the service.
"""

from copy import deepcopy
from hashlib import sha256
import json
from google.cloud.firestore_v1 import transactional
from fastapi import HTTPException
from .firestore_repository import get_required_firestore_client


def empty_state():
    return {"revision": 0, "entries": {}, "selections": {}}


class FirestoreKnowledgeRepository:
    def __init__(self):
        self.client = get_required_firestore_client(unavailable_message="Knowledge storage is unavailable")

    def document(self, owner):
        return self.client.collection("agent_knowledge").document(sha256(owner.encode()).hexdigest())

    def read(self, owner):
        return self.document(owner).get().to_dict() or empty_state()

    def mutate(self, owner, request_id, fingerprint, apply, project_id=None):
        doc = self.document(owner)
        receipt = doc.collection("requests").document(sha256(request_id.encode()).hexdigest())

        @transactional
        def run(transaction):
            # Check project ownership within the same transaction as the write.
            if project_id:
                project = self.client.collection("qa_projects").document(project_id).get(transaction=transaction).to_dict()
                if not project or project.get("owner_user_id") != owner:
                    raise HTTPException(404, "Project not found")
            saved = receipt.get(transaction=transaction).to_dict()
            state = doc.get(transaction=transaction).to_dict() or empty_state()
            if saved:
                if saved["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Request ID already used for different knowledge changes")
                return state, saved["entry_id"]
            updated = deepcopy(state)
            entry_id = apply(updated)
            updated["revision"] += 1
            if len(json.dumps(updated).encode()) > 750_000:
                raise HTTPException(422, "Knowledge storage limit reached; delete unused entries before adding more")
            transaction.set(doc, updated)
            transaction.set(receipt, {"fingerprint": fingerprint, "entry_id": entry_id})
            return updated, entry_id

        return run(self.client.transaction())
