"""Durable staged imports and atomic reviewed baseline commits (#294)."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import uuid4
from fastapi import HTTPException
from google.cloud.firestore_v1 import transactional
from ..agents.requirement_matching_agent import suggest_matches
from ..contracts.requirement_imports import RequirementImportPreview, ImportApplyInput, ImportHistoryEntry
from ..contracts.requirements import Requirement
from .guidance_service import public_manifest
from .firestore_repository import get_required_firestore_client
from .requirement_reconciliation import build_preview, canonical_requirements, digest, reconcile
from .workflow_project_service import get_project, DOWNSTREAM_STAGES


def _owned(data, actor):
    if not data or data.get("owner_user_id") != actor.sub:
        raise HTTPException(404, "Project not found")
    if data.get("status") == "archived":
        raise HTTPException(409, "Restore this project before changing requirements.")
    return data


def _revision(data, expected):
    if expected is None or data.get("current_revision", 0) != expected:
        raise HTTPException(
            409,
            {
                "code": "stale_import",
                "message": "Project changed. Compare the import again before applying.",
                "latest_revision": data.get("current_revision", 0),
            },
        )


def _bounded(payload):
    if len(json.dumps(payload, default=str).encode()) > 850_000:
        raise HTTPException(413, "Requirement comparison exceeds the storage limit. Import a smaller batch.")
    return payload


def _snapshot_write(transaction, doc, project, payload, actor, request_id, snapshot_id, operation, counts):
    now = datetime.now(timezone.utc)
    revision = project.get("current_revision", 0) + 1
    state = deepcopy(project.get("stage_state") or {})
    previous = state.get("requirements") or {}
    approved = bool(payload["requirements"]) and all(r.get("review_status") == "Approved" for r in payload["requirements"])
    title = f"{counts.get('added', 0)} added · {counts.get('updated', 0)} updated · {counts.get('retired', 0)} retired · {len(payload['requirements'])} active requirements"
    snapshot = {
        "snapshot_id": snapshot_id,
        "project_id": doc.id,
        "stage": "requirements",
        "version": previous.get("version", 0) + 1,
        "project_revision": revision,
        "operation": operation,
        "approved": approved,
        "source_snapshot_id": previous.get("current_snapshot_id"),
        "request_id": request_id,
        "actor_user_id": actor.sub,
        "title": title,
        "metadata": {"changes": counts, "guidance": payload.get("guidance")},
        "payload": payload,
        "created_at": now,
    }
    state["requirements"] = {
        "current_snapshot_id": snapshot_id,
        "version": snapshot["version"],
        "approved": approved,
        "stale": False,
        "updated_at": now,
        "operation": operation,
        "metadata": {"changes": counts},
    }
    for stage in DOWNSTREAM_STAGES["requirements"]:
        if stage in state:
            state[stage].update(stale=True, stale_reason=f"requirements changed in project revision {revision}")
    _bounded(snapshot)
    transaction.create(doc.collection("snapshots").document(snapshot_id), snapshot)
    transaction.update(doc, {"current_revision": revision, "stage_state": state, "latest_stage": "requirements", "updated_at": now})
    transaction.create(
        doc.collection("timeline").document(snapshot_id),
        {
            "event_id": snapshot_id,
            "project_id": doc.id,
            "event_type": "stage.snapshot_created",
            "stage": "requirements",
            "summary": title,
            "project_revision": revision,
            "snapshot_id": snapshot_id,
            "actor_user_id": actor.sub,
            "occurred_at": now,
            "metadata": {"operation": operation, "changes": counts},
        },
    )


class RequirementImportRepository:
    def __init__(self):
        self.client = get_required_firestore_client(unavailable_message="Requirement import storage is unavailable")

    def project_doc(self, project_id):
        return self.client.collection("qa_projects").document(project_id)

    def baseline(self, project_id, actor, expected=None):
        doc = self.project_doc(project_id)
        project = _owned(doc.get().to_dict(), actor)
        if expected is not None:
            _revision(project, expected)
        sid = (project.get("stage_state", {}).get("requirements") or {}).get("current_snapshot_id")
        snapshot = doc.collection("snapshots").document(sid).get().to_dict() if sid else None
        if sid and not snapshot:
            raise HTTPException(409, "Current requirements snapshot is unavailable.")
        return project, snapshot

    def read(self, project_id, import_id, actor):
        self.baseline(project_id, actor)
        data = self.project_doc(project_id).collection("requirement_imports").document(import_id).get().to_dict()
        if not data:
            raise HTTPException(404, "Import preview not found")
        return data

    def save(self, preview, actor, retired, workflow_payload):
        doc = self.project_doc(preview.project_id)
        data = _bounded(
            {**preview.model_dump(mode="json"), "retired_requirements": [r.model_dump(mode="json") for r in retired], "workflow_payload": workflow_payload}
        )

        @transactional
        def save(transaction):
            project = _owned(doc.get(transaction=transaction).to_dict(), actor)
            _revision(project, preview.base_project_revision)
            transaction.create(doc.collection("requirement_imports").document(preview.import_id), data)

        save(self.client.transaction())
        return preview

    def list_pending(self, project_id, actor):
        self.baseline(project_id, actor)
        rows = [
            RequirementImportPreview.model_validate(d.to_dict())
            for d in self.project_doc(project_id).collection("requirement_imports").stream()
            if d.to_dict().get("status") == "pending"
        ]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    def cancel(self, project_id, import_id, actor):
        doc = self.project_doc(project_id)
        ref = doc.collection("requirement_imports").document(import_id)

        @transactional
        def cancel(transaction):
            _owned(doc.get(transaction=transaction).to_dict(), actor)
            data = ref.get(transaction=transaction).to_dict()
            if not data:
                raise HTTPException(404, "Import preview not found")
            if data.get("status") == "applied":
                raise HTTPException(409, "This import has already been applied.")
            transaction.update(ref, {"status": "cancelled"})

        cancel(self.client.transaction())

    def apply(self, project_id, import_id, actor, decision, request_id):
        doc = self.project_doc(project_id)
        ref = doc.collection("requirement_imports").document(import_id)
        receipt = doc.collection("requirement_import_receipts").document(digest(decision.idempotency_key))
        fingerprint = digest([import_id, decision.model_dump(mode="json")])

        @transactional
        def apply(transaction):
            project = _owned(doc.get(transaction=transaction).to_dict(), actor)
            data = ref.get(transaction=transaction).to_dict()
            saved = receipt.get(transaction=transaction).to_dict()
            if saved:
                if saved.get("fingerprint") != fingerprint:
                    raise HTTPException(409, "This retry key was used for different import decisions.")
                return
            if not data:
                raise HTTPException(404, "Import preview not found")
            preview = RequirementImportPreview.model_validate(data)
            if preview.status != "pending":
                raise HTTPException(409, "This import is no longer pending.")
            _revision(project, preview.base_project_revision)
            if decision.base_project_revision != preview.base_project_revision:
                raise HTTPException(409, "Apply must use the reviewed preview's project revision.")
            active, retired, counts = reconcile(preview, decision, [Requirement.model_validate(r) for r in data.get("retired_requirements", [])])
            payload = {
                **data.get("workflow_payload", {}),
                "requirements": [r.model_dump(mode="json") for r in active],
                "retired_requirements": [r.model_dump(mode="json") for r in retired],
                "import_id": import_id,
                "source_name": "Project requirements",
                "source_names": sorted({s.label for r in active for s in r.sources}),
                "approved": bool(active) and all(r.review_status == "Approved" for r in active),
                "import_changes": counts,
                "schema_version": 2,
            }
            # Extraction scores describe the batch, not the entire combined baseline.
            payload["review"] = {"approved": payload["approved"], "summary": "Review the project requirements before generation."}
            payload["coverage_metrics"] = {"total_requirements": len(active), "document_count": len(payload["source_names"])}
            payload["workflow_diagnostics"] = {}
            sid = digest([import_id, decision.idempotency_key])
            _snapshot_write(transaction, doc, project, payload, actor, request_id, sid, "requirements.import.applied", counts)
            transaction.update(ref, {"status": "applied", "applied_snapshot_id": sid, "applied_counts": counts})
            transaction.create(receipt, {"fingerprint": fingerprint, "snapshot_id": sid})

        apply(self.client.transaction())
        return get_project(project_id, actor=actor)

    def review(self, project_id, actor, payload, request_id):
        doc = self.project_doc(project_id)
        receipt = doc.collection("requirement_import_receipts").document(digest(payload.idempotency_key))
        fingerprint = digest(["review", payload.model_dump(mode="json")])

        @transactional
        def review(transaction):
            project = _owned(doc.get(transaction=transaction).to_dict(), actor)
            saved = receipt.get(transaction=transaction).to_dict()
            if saved:
                if saved.get("fingerprint") != fingerprint:
                    raise HTTPException(409, "Retry key was used for different review decisions.")
                return
            _revision(project, payload.base_project_revision)
            sid = (project.get("stage_state", {}).get("requirements") or {}).get("current_snapshot_id")
            snapshot = doc.collection("snapshots").document(sid).get(transaction=transaction).to_dict() if sid else None
            if not snapshot:
                raise HTTPException(409, "Import requirements before reviewing them.")
            active, retired = canonical_requirements(project_id, snapshot)
            by_uid = {r.requirement_uid: r for r in active}
            seen = set()
            for change in payload.reviews:
                if change.requirement_uid not in by_uid or change.requirement_uid in seen:
                    raise HTTPException(422, "Review each active requirement at most once.")
                seen.add(change.requirement_uid)
                req = by_uid[change.requirement_uid]
                req.review_status = change.review_status
                req.quality_flags = change.quality_flags
            updated = {
                **snapshot["payload"],
                "requirements": [r.model_dump(mode="json") for r in active],
                "retired_requirements": [r.model_dump(mode="json") for r in retired],
                "approved": bool(active) and all(r.review_status == "Approved" for r in active),
            }
            updated["review"] = {**updated.get("review", {}), "approved": updated["approved"]}
            sid = digest(["review", payload.idempotency_key])
            _snapshot_write(transaction, doc, project, updated, actor, request_id, sid, "requirements.review", {"reviewed": len(seen)})
            transaction.create(receipt, {"fingerprint": fingerprint, "snapshot_id": sid})

        review(self.client.transaction())
        return get_project(project_id, actor=actor)

    def history(self, project_id, uid, actor):
        self.baseline(project_id, actor)
        rows = []
        for doc in self.project_doc(project_id).collection("snapshots").stream():
            snapshot = doc.to_dict()
            if snapshot.get("stage") != "requirements":
                continue
            active, retired = canonical_requirements(project_id, snapshot)
            for req in active + retired:
                if req.requirement_uid == uid:
                    rows.append(
                        ImportHistoryEntry(
                            snapshot_id=snapshot["snapshot_id"],
                            project_revision=snapshot["project_revision"],
                            created_at=snapshot["created_at"],
                            requirement=req,
                        )
                    )
        return sorted(rows, key=lambda row: row.project_revision, reverse=True)


def repository():
    return RequirementImportRepository()


def require_review_mode(project_id, import_mode):
    if project_id and import_mode != "review":
        raise HTTPException(409, {"code": "import_review_required", "message": "This client must be updated. Imports require Compare changes and Apply."})


def require_review_client(project_id, base_project_revision, import_mode, actor):
    require_review_mode(project_id, import_mode)
    if not project_id:
        return
    if base_project_revision is None:
        raise HTTPException(422, "A project revision is required to prepare an import.")
    repository().baseline(project_id, actor, base_project_revision)


def stage_import(project_id, actor, revision, response, operation):
    repo = repository()
    project, snapshot = repo.baseline(project_id, actor, revision)
    current, retired = canonical_requirements(project_id, snapshot)
    incoming = response.requirements
    if not incoming:
        raise HTTPException(422, "No requirements extracted. The current project has not changed.")
    _bounded({"current": [r.model_dump(mode="json") for r in current], "incoming": [r.model_dump(mode="json") for r in incoming]})
    matches, failed = suggest_matches(current, incoming)
    preview = build_preview(project_id, project["current_revision"], current, incoming, response.source_name, operation, str(uuid4()), matches, failed)
    preview.guidance = response.guidance or public_manifest()
    workflow = response.model_dump(mode="json", exclude={"requirements", "raw_text"})
    workflow["guidance"] = preview.guidance
    return repo.save(preview, actor, retired, workflow)


def recompare_import(project_id, import_id, actor):
    repo = repository()
    data = repo.read(project_id, import_id, actor)
    old = RequirementImportPreview.model_validate(data)
    if old.status != "pending":
        raise HTTPException(409, "Only pending imports can be compared again.")
    if old.recovery_snapshot_ids:
        raise HTTPException(409, "Project changed. Prepare a new recovery comparison from the historical snapshots.")
    project, snapshot = repo.baseline(project_id, actor)
    current, retired = canonical_requirements(project_id, snapshot)
    incoming = [c.requirement for c in old.candidates]
    _bounded({"current": [r.model_dump(mode="json") for r in current], "incoming": [r.model_dump(mode="json") for r in incoming]})
    matches, failed = suggest_matches(current, incoming)
    preview = build_preview(project_id, project["current_revision"], current, incoming, old.source_name, old.operation, str(uuid4()), matches, failed)
    preview.guidance = old.guidance
    result = repo.save(preview, actor, retired, data.get("workflow_payload", {}))
    repo.cancel(project_id, import_id, actor)
    return result


def prepare_recovery(project_id, actor, payload):
    repo = repository()
    repo.baseline(project_id, actor, payload.base_project_revision)
    doc = repo.project_doc(project_id)
    snapshots = [doc.collection("snapshots").document(sid).get().to_dict() for sid in (payload.baseline_snapshot_id, payload.incoming_snapshot_id)]
    if any(not s or s.get("stage") != "requirements" for s in snapshots):
        raise HTTPException(422, "Select two existing requirements snapshots from this project.")
    # Restrict recovery to the observed replacement, so a later baseline cannot be silently dropped.
    _, current_snapshot = repo.baseline(project_id, actor, payload.base_project_revision)
    if not current_snapshot or current_snapshot["snapshot_id"] != payload.incoming_snapshot_id:
        raise HTTPException(409, "Recovery incoming snapshot must be the current requirements baseline.")
    current, retired = canonical_requirements(project_id, snapshots[0])
    incoming, _ = canonical_requirements(project_id, snapshots[1])
    _bounded({"current": [r.model_dump(mode="json") for r in current], "incoming": [r.model_dump(mode="json") for r in incoming]})
    matches, failed = suggest_matches(current, incoming)
    preview = build_preview(
        project_id,
        payload.base_project_revision,
        current,
        incoming,
        snapshots[1]["payload"].get("source_name") or "Recovered import",
        "requirements.recovery",
        str(uuid4()),
        matches,
        failed,
    )
    preview.recovery_snapshot_ids = [payload.baseline_snapshot_id, payload.incoming_snapshot_id]
    preview.warnings.append("Recovery restores the selected saved baseline and merges the incoming snapshot. Review the complete result before applying.")
    return repo.save(preview, actor, retired, {})


def validate_generation_baseline(project_id, actor, revision, requirements):
    if any(r.lifecycle_status == "retired" for r in requirements):
        raise HTTPException(409, "Retired requirements cannot be used for new generation.")
    if not project_id:
        return
    project, snapshot = repository().baseline(project_id, actor)
    # Legacy callers keep their previous contract until their baseline is migrated.
    if not snapshot or snapshot.get("payload", {}).get("schema_version") != 2:
        return
    _revision(project, revision)
    active, _ = canonical_requirements(project_id, snapshot)
    by_id = {r.id: r for r in active}
    seen = set()
    for req in requirements:
        saved = by_id.get(req.id)
        if (
            not saved
            or req.id in seen
            or saved.review_status != "Approved"
            or saved.text != req.text
            or (req.requirement_uid and req.requirement_uid != saved.requirement_uid)
        ):
            raise HTTPException(409, "Generation requires the current, approved active requirements. Reload the project.")
        seen.add(req.id)
