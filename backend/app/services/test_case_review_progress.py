"""Private, revision-bound progress for an interrupted saved-suite review."""

from datetime import datetime, timedelta
from hashlib import sha256
import json
import logging

from google.api_core.exceptions import GoogleAPICallError

from . import workflow_project_service as projects

RECOVERY_WINDOW = timedelta(hours=24)
MAX_PROGRESS_BYTES = 768 * 1024
PROGRESS_SCHEMA_VERSION = 1


def _bounded(items):
    return sum(len(key) + len(value.encode("utf-8")) for key, value in items.items()) <= MAX_PROGRESS_BYTES


class SavedReviewProgress:
    def __init__(self, project_id, snapshot_id, revision, actor):
        self.project_id, self.snapshot_id, self.revision, self.actor = project_id, snapshot_id, revision, actor
        self.doc = None

    def _check_project(self, transaction):
        project = projects._document_to_dict(self.project_doc.get(transaction=transaction))
        if project is None:
            raise projects.ProjectNotFoundError(self.project_id)
        projects._require_owner(project, self.actor)
        projects._check_revision(project, self.revision)
        suite = (project.get("stage_state") or {}).get("test_cases") or {}
        if project.get("status") != "active" or suite.get("current_snapshot_id") != self.snapshot_id:
            raise projects.ProjectConflictError(project["current_revision"])

    def _read(self, doc, transaction):
        record = projects._document_to_dict(doc.get(transaction=transaction)) or {}
        expires_at = record.get("expires_at")
        if record.get("binding") != self.binding or not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at <= projects._utcnow():
            return None, {}
        items = record.get("items")
        usable = {key: value for key, value in items.items() if key in self.group_keys and isinstance(value, str)} if isinstance(items, dict) else {}
        return expires_at, usable if _bounded(usable) else {}

    def load(self, review_key, group_keys):
        identity = [PROGRESS_SCHEMA_VERSION, self.actor.sub, self.project_id, self.snapshot_id, self.revision, review_key]
        self.binding = sha256(json.dumps(identity).encode()).hexdigest()
        self.group_keys = frozenset(group_keys)
        try:
            self.client = projects.get_required_firestore_client(unavailable_message="Project storage is unavailable")
        except RuntimeError:
            return {}
        self.project_doc = self.client.collection(projects.QA_PROJECTS_COLLECTION).document(self.project_id)
        doc = self.project_doc.collection("test_case_review_progress").document("current")

        @projects.transactional
        def load(transaction):
            self._check_project(transaction)
            expires_at, items = self._read(doc, transaction)
            if expires_at is None:
                transaction.set(doc, {"binding": self.binding, "expires_at": projects._utcnow() + RECOVERY_WINDOW, "items": {}})
            return items

        try:
            items = load(self.client.transaction())
        except GoogleAPICallError as exc:
            logging.warning("Saved suite review progress could not be loaded (%s)", type(exc).__name__)
            return {}
        self.doc = doc
        return items

    def save(self, group_key, evidence):
        if self.doc is None:
            return

        @projects.transactional
        def save(transaction):
            self._check_project(transaction)
            expires_at, items = self._read(self.doc, transaction)
            items[group_key] = evidence
            if expires_at is not None and _bounded(items):
                transaction.update(self.doc, {"items": items})

        try:
            save(self.client.transaction())
        except GoogleAPICallError as exc:
            logging.warning("Saved suite review progress could not be stored (%s)", type(exc).__name__)

    def clear_on_commit(self, transaction, _snapshot):
        if self.doc is not None:
            transaction.delete(self.doc)
