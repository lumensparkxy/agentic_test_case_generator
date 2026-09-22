"""Durable, analysis-scoped single flight for impact application.

Reservations do not change the project revision. Snapshot commits still compare
that revision and fence the lease in the same Firestore transaction.
"""

from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import uuid4

from ..contracts.impact import ImpactApplication
from . import workflow_project_service as projects

LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30


def now():
    return datetime.now(timezone.utc)


def operation_doc(project_doc, analysis_id):
    return project_doc.collection("impact_applications").document(analysis_id)


def _expired(record):
    expiry = record.get("lease_expires_at")
    if isinstance(expiry, str):
        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    return not expiry or expiry <= now()


def public_record(record):
    if not record:
        return None
    record = dict(record)
    if record.get("status") == "applying" and _expired(record):
        record.update(status="failed", error="Application was interrupted. The existing suite was preserved; retry is safe.")
    return ImpactApplication.model_validate(record)


def historical_record(analysis, tests):
    """Only immutable lineage proves a legacy apply, never a test title or count."""
    if not analysis or not tests or tests.operation != "impact.update.apply" or tests.source_snapshot_id != analysis.snapshot_id:
        return None
    result = tests.payload.get("impact_update_result") or {}
    applied = result.get("applied_recommendation_ids") or []
    known = {r.get("recommendation_id") for r in analysis.payload.get("recommendations", [])}
    if not applied or not set(applied) <= known:
        return None
    changed = result.get("changed_test_case_ids")
    if changed is None:
        # Compare immutable suite versions: inherited impact tags can describe an
        # older apply and must not put unchanged rows into the review filter.
        baseline = projects._snapshot_for(analysis.project_id, analysis.source_snapshot_id) if analysis.source_snapshot_id else None
        before = {r["id"]: r for r in baseline.payload.get("test_cases", [])} if baseline else {}
        if not baseline:
            return None
        changed = [r["id"] for r in tests.payload.get("test_cases", []) if r != before.get(r["id"])]
    return ImpactApplication(
        analysis_snapshot_id=analysis.snapshot_id,
        status="applied",
        accepted_recommendation_ids=applied,
        completed_at=tests.created_at,
        result_snapshot_id=tests.snapshot_id,
        result_project_revision=tests.project_revision,
        changed_test_case_ids=changed,
        **{key: result.get(key, 0) for key in ("preserved_count", "updated_count", "added_count", "deprecated_count")},
    )


def read_current(project_doc, snapshots):
    analysis = snapshots.get("impact_analysis")
    if not analysis:
        return None
    record = projects._document_to_dict(operation_doc(project_doc, analysis.snapshot_id).get())
    result = public_record(record) or historical_record(analysis, snapshots.get("test_cases"))
    tests = snapshots.get("test_cases")
    if not result and tests and analysis.source_snapshot_id != tests.snapshot_id:
        return ImpactApplication(
            analysis_snapshot_id=analysis.snapshot_id,
            status="verification_required",
            error="This analysis targets an earlier suite. Analyze impact again to verify remaining work.",
        )
    return result


class ApplicationLease:
    def __init__(self, project_id, analysis_id, actor, revision, selected):
        self.project_id, self.analysis_id, self.actor = project_id, analysis_id, actor
        self.revision, self.selected = revision, sorted(selected)
        self.token = uuid4().hex
        self.client = projects.get_required_firestore_client(unavailable_message="Project storage is unavailable")
        self.project_doc = self.client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)
        self.doc = operation_doc(self.project_doc, analysis_id)
        self.stop = Event()
        self.thread = None

    def reserve(self):
        @projects.transactional
        def claim(transaction):
            project = projects._document_to_dict(self.project_doc.get(transaction=transaction))
            projects._require_owner(project, self.actor)
            record = projects._document_to_dict(self.doc.get(transaction=transaction))
            if record and (record["status"] == "applied" or (record["status"] == "applying" and not _expired(record))):
                return False
            projects._check_revision(project, self.revision)
            if project.get("status") != "active" or project["stage_state"]["impact_analysis"]["current_snapshot_id"] != self.analysis_id:
                raise projects.ProjectConflictError(project["current_revision"])
            if record and record["accepted_recommendation_ids"] != self.selected:
                raise ValueError("Retry the original selection, or analyze impact again to change it.")
            transaction.set(
                self.doc,
                {
                    "analysis_snapshot_id": self.analysis_id,
                    "status": "applying",
                    "accepted_recommendation_ids": self.selected,
                    "lease_token": self.token,
                    "lease_expires_at": now() + timedelta(seconds=LEASE_SECONDS),
                    "started_at": now(),
                },
            )
            return True

        return claim(self.client.transaction())

    def renew(self):
        @projects.transactional
        def heartbeat(transaction):
            record = projects._document_to_dict(self.doc.get(transaction=transaction))
            self._check(record)
            transaction.update(self.doc, {"lease_expires_at": now() + timedelta(seconds=LEASE_SECONDS)})

        heartbeat(self.client.transaction())

    def _heartbeat(self):
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                self.renew()
            except Exception:
                # Never renew an expired/superseded lease. The commit fails closed.
                return

    def __enter__(self):
        self.thread = Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=1)

    def _check(self, record):
        if not record or record.get("lease_token") != self.token or record.get("status") != "applying" or _expired(record):
            raise RuntimeError("Application lease expired or was superseded. Check application status before retrying.")

    def commit(self, transaction, snapshot):
        record = projects._document_to_dict(self.doc.get(transaction=transaction))
        self._check(record)
        result = snapshot["payload"]["impact_update_result"]
        transaction.set(
            self.doc,
            {
                **record,
                **result,
                "status": "applied",
                "completed_at": snapshot["created_at"],
                "result_snapshot_id": snapshot["snapshot_id"],
                "result_project_revision": snapshot["project_revision"],
                "error": None,
            },
        )

    def fail(self):
        @projects.transactional
        def finish(transaction):
            record = projects._document_to_dict(self.doc.get(transaction=transaction))
            if record and record.get("lease_token") == self.token and record.get("status") == "applying":
                transaction.update(self.doc, {"status": "failed", "error": "Application failed before saving changes. The existing suite was preserved."})

        finish(self.client.transaction())
