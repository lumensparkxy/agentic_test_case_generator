"""Rebind completed material Sync drafts to a facts-identical approved successor."""

from dataclasses import dataclass
import json
import re

from fastapi import HTTPException
from google.api_core.exceptions import GoogleAPICallError
from pydantic import ValidationError

from ..contracts.project_sync import ProjectInputManifest, ProjectSyncRebindReceipt, ProjectSyncRebindStatus
from . import workflow_project_service as projects
from .approval_continuation import lease_blocks_claim, project_lease_ref
from .firestore_repository import get_required_firestore_client
from .project_draft_admission import draft_admission_client
from .project_inputs import manifest_from_payload
from .project_sync_review import SYNC_REBIND_OPERATION, completed_sync_pair, validate_sync_suite_scope, verify_original_sync_pair
from .quality_feedback import _snapshot
from .requirement_reconciliation import canonical_requirements
from .requirement_scope import approved_generation_requirements
from .test_case_quality import case_source_hash

_MAX_SNAPSHOT_BYTES = 850_000
_RECEIPTS = "sync_rebind_receipts"


@dataclass(frozen=True)
class _RebindSource:
    record: dict
    context: projects.QaProjectStageSnapshot
    baseline: projects.QaProjectStageSnapshot
    old_plan: projects.QaProjectStageSnapshot
    old_suite: projects.QaProjectStageSnapshot
    manifest: ProjectInputManifest
    facts_hash: str


def _conflict(
    code="sync_rebind_unavailable", message="Rebind needs the exact approved Requirements successor, unchanged accepted Context and both completed drafts."
):
    return HTTPException(409, {"code": code, "message": message})


def _material_record(record):
    return (
        isinstance(record, dict)
        and record.get("draft_admission_version") == 1
        and isinstance(record.get("id"), str)
        and re.fullmatch(r"change_[0-9a-f]{24}", record["id"]) is not None
        and isinstance(record.get("stages"), dict)
        and record["stages"].get("requirements") == "draft"
    )


def material_requirements_review_required(payload):
    """Keep named first-run generation out of an enrolled material correction."""
    record = payload.get("project_sync")
    if not _material_record(record) or record.get("review_rebind") or payload.get("status") != "active":
        return False
    state = (payload.get("stage_state") or {}).get("requirements") or {}
    return (
        state.get("operation") == "quality.feedback.apply"
        and not state.get("stale")
        and (state.get("metadata") or {}).get("change_id") == record["id"]
        and manifest_from_payload(payload).model_dump(mode="json") == record.get("input_manifest")
    )


def material_scope_pending_rebind(payload, requirement_snapshot):
    record = payload.get("project_sync")
    if not _material_record(record) or record.get("review_rebind") or payload.get("status") != "active" or not requirement_snapshot:
        return False
    try:
        original = ProjectInputManifest.model_validate(record.get("input_manifest"))
    except ValidationError:
        return False
    return (
        requirement_snapshot.get("operation") == "requirements.review"
        and requirement_snapshot.get("source_snapshot_id") == original.requirements_snapshot_id
        and manifest_from_payload(payload) == original.model_copy(update={"requirements_snapshot_id": requirement_snapshot.get("snapshot_id")})
    )


def _receipt_id(actor_id, request_id):
    return case_source_hash([actor_id, request_id])


def _public(receipt):
    return ProjectSyncRebindReceipt.model_validate(receipt)


def public_sync_record(record):
    if not record or "review_rebind" not in record:
        return record
    view = {key: value for key, value in record.items() if key != "review_rebind"}
    try:
        view["review_rebind"] = _public(record["review_rebind"]).model_dump(mode="json")
    except ValidationError:
        pass
    return view


def _read_snapshot(tx, doc, snapshot_id, stage):
    if not isinstance(snapshot_id, str) or not snapshot_id or "/" in snapshot_id:
        raise _conflict()
    value = doc.collection("snapshots").document(snapshot_id).get(transaction=tx).to_dict()
    if not value or value.get("snapshot_id") != snapshot_id or value.get("project_id") != doc.id or value.get("stage") != stage:
        raise _conflict()
    return projects.QaProjectStageSnapshot.model_validate(value)


def _change_doc(doc, change_id):
    return doc.collection("project_changes").document(change_id)


def _receipt_doc(doc, receipt_id):
    return doc.collection(_RECEIPTS).document(receipt_id)


def _facts_hash(project_id, snapshot):
    active, retired = canonical_requirements(project_id, snapshot.model_dump(mode="json"))
    return case_source_hash([[row.model_dump(mode="json", exclude={"review_status", "quality_flags"}) for row in rows] for rows in (active, retired)])


def _approved_successor(project, record, baseline, material, manifest, original_manifest):
    if (
        baseline.operation != "requirements.review"
        or baseline.actor_user_id != project.owner_user_id
        or not baseline.approved
        or baseline.source_snapshot_id != material.snapshot_id
        or baseline.project_revision <= record["project_revision"]
        or material.operation != "quality.feedback.apply"
        or material.actor_user_id != project.owner_user_id
        or material.metadata.get("change_id") != record["id"]
        or material.snapshot_id != original_manifest.requirements_snapshot_id
        or manifest != original_manifest.model_copy(update={"requirements_snapshot_id": baseline.snapshot_id})
        or not approved_generation_requirements(project)
    ):
        raise _conflict()
    facts_hash = _facts_hash(project.project_id, material)
    if facts_hash != _facts_hash(project.project_id, baseline):
        raise _conflict("sync_rebind_facts_changed", "The approved source changed more than review decisions. Refresh the affected drafts.")
    return facts_hash


def _candidate(tx, client, doc, raw, project):
    record = raw.get("project_sync")
    if not _material_record(record) or record.get("review_rebind") or raw.get("status") != "active":
        raise _conflict()
    if _change_doc(doc, record["id"]).get(transaction=tx).to_dict() != record:
        raise _conflict()
    states = raw.get("stage_state") or {}
    for stage in ("requirements", "context", "use_cases", "test_cases"):
        state = states.get(stage) or {}
        if not state.get("current_snapshot_id") or state.get("stale", False) != (stage in {"use_cases", "test_cases"}):
            raise _conflict()
    baseline = _read_snapshot(tx, doc, states["requirements"]["current_snapshot_id"], "requirements")
    context = _read_snapshot(tx, doc, states["context"]["current_snapshot_id"], "context")
    original, plan_receipt, suite_receipt = completed_sync_pair(project, record, context)
    if states["use_cases"]["current_snapshot_id"] != plan_receipt["snapshot_id"] or states["test_cases"]["current_snapshot_id"] != suite_receipt["snapshot_id"]:
        raise _conflict()
    material = _read_snapshot(tx, doc, original.requirements_snapshot_id, "requirements")
    manifest = manifest_from_payload(raw)
    if material.project_revision != context.project_revision:
        raise _conflict()
    facts_hash = _approved_successor(project, record, baseline, material, manifest, original)
    old_plan = _read_snapshot(tx, doc, plan_receipt["snapshot_id"], "use_cases")
    old_suite = _read_snapshot(tx, doc, suite_receipt["snapshot_id"], "test_cases")
    affected = validate_sync_suite_scope(project, old_suite, old_plan, record)
    verify_original_sync_pair(tx, client, project, doc, record, context, old_plan, old_suite, affected)
    return _RebindSource(record, context, baseline, old_plan, old_suite, manifest, facts_hash)


def _snapshot_matches(snapshot, receipt, stage):
    plan = stage == "use_cases"
    upstream = receipt["requirements_snapshot_id"] if plan else receipt["use_cases_snapshot_id"]
    metadata = snapshot.metadata
    return (
        snapshot.operation == SYNC_REBIND_OPERATION
        and snapshot.approved is False
        and snapshot.actor_user_id == receipt["actor_user_id"]
        and snapshot.snapshot_id == receipt[f"{stage}_snapshot_id"]
        and snapshot.request_id == receipt["request_id"]
        and snapshot.project_revision == receipt["project_revision"]
        and snapshot.source_snapshot_id == upstream
        and metadata.get("change_id") == receipt["change_id"]
        and metadata.get("rebind_receipt_id") == _receipt_id(receipt["actor_user_id"], receipt["request_id"])
        and metadata.get("previous_snapshot_id") == receipt[f"previous_{stage}_snapshot_id"]
        and metadata.get("provisional") is True
        and metadata.get("project_inputs") == receipt["input_manifest"]
        and metadata.get("source_snapshot_ids")
        == {
            "requirements": receipt["requirements_snapshot_id"],
            "context": receipt["context_snapshot_id"],
            "use_cases": receipt["use_cases_snapshot_id"],
        }
        and case_source_hash(snapshot.payload) == receipt[f"{stage}_payload_hash"]
    )


def validate_rebound_plan(project, record, baseline, plan, manifest, original_manifest, plan_receipt, draft_receipt):
    receipt = record.get("review_rebind") or {}
    if (
        not _material_record(record)
        or receipt.get("schema_version") != 1
        or receipt.get("project_id") != project.project_id
        or receipt.get("actor_user_id") != project.owner_user_id
        or receipt.get("change_id") != record["id"]
        or receipt.get("requirements_snapshot_id") != baseline.snapshot_id
        or baseline.source_snapshot_id != original_manifest.requirements_snapshot_id
        or baseline.operation != "requirements.review"
        or baseline.actor_user_id != project.owner_user_id
        or baseline.approved is not True
        or receipt.get("context_snapshot_id") != original_manifest.context_snapshot_id
        or manifest != original_manifest.model_copy(update={"requirements_snapshot_id": baseline.snapshot_id})
        or ProjectInputManifest.model_validate(receipt.get("input_manifest")) != manifest
        or receipt.get("previous_use_cases_snapshot_id") != plan_receipt["snapshot_id"]
        or receipt.get("previous_test_cases_snapshot_id") != draft_receipt["snapshot_id"]
        or receipt.get("base_project_revision", -1) < baseline.project_revision
        or receipt.get("project_revision") != receipt.get("base_project_revision") + 1
        or receipt["project_revision"] > project.current_revision
        or receipt.get("requirement_facts_hash") != _facts_hash(project.project_id, baseline)
        or not _snapshot_matches(plan, receipt, "use_cases")
    ):
        raise _conflict()
    return receipt


def validate_rebound_suite(project, receipt, suite):
    if suite.project_id != project.project_id or suite.stage != "test_cases" or not _snapshot_matches(suite, receipt, "test_cases"):
        raise _conflict()


def _verify_receipt(tx, doc, receipt, actor_id, request_id):
    if (
        receipt.get("schema_version") != 1
        or receipt.get("project_id") != doc.id
        or receipt.get("actor_user_id") != actor_id
        or receipt.get("request_id") != request_id
        or receipt.get("project_revision") != receipt.get("base_project_revision", -1) + 1
    ):
        raise _conflict()
    plan = _read_snapshot(tx, doc, receipt["use_cases_snapshot_id"], "use_cases")
    suite = _read_snapshot(tx, doc, receipt["test_cases_snapshot_id"], "test_cases")
    if not _snapshot_matches(plan, receipt, "use_cases") or not _snapshot_matches(suite, receipt, "test_cases"):
        raise _conflict()
    return plan, suite


def verify_rebound_history(tx, doc, project, record, receipt, plan, suite):
    saved = _receipt_doc(doc, _receipt_id(project.owner_user_id, receipt["request_id"])).get(transaction=tx).to_dict()
    if saved != receipt:
        raise _conflict()
    current_plan, current_suite = _verify_receipt(tx, doc, receipt, project.owner_user_id, receipt["request_id"])
    if current_plan != plan or current_suite != suite:
        raise _conflict()
    old_plan = _read_snapshot(tx, doc, receipt["previous_use_cases_snapshot_id"], "use_cases")
    old_suite = _read_snapshot(tx, doc, receipt["previous_test_cases_snapshot_id"], "test_cases")
    original = ProjectInputManifest.model_validate(record["input_manifest"])
    old_requirements = _read_snapshot(tx, doc, original.requirements_snapshot_id, "requirements")
    if (
        old_plan.payload != plan.payload
        or old_suite.payload != suite.payload
        or old_requirements.operation != "quality.feedback.apply"
        or old_requirements.actor_user_id != project.owner_user_id
        or old_requirements.metadata.get("change_id") != record["id"]
        or old_requirements.project_revision != project.current_snapshots["context"].project_revision
        or _facts_hash(project.project_id, old_requirements) != receipt["requirement_facts_hash"]
    ):
        raise _conflict()
    return old_plan, old_suite


def read_sync_rebind_receipt(project_id, actor, request_id, *, payload=None, client=None):
    client = client or get_required_firestore_client(unavailable_message="Project storage is unavailable")
    doc = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)

    @projects.transactional
    def read(tx):
        raw = doc.get(transaction=tx).to_dict()
        if not raw:
            raise projects.ProjectNotFoundError(project_id)
        projects._require_owner(raw, actor)
        receipt = _receipt_doc(doc, _receipt_id(actor.sub, request_id)).get(transaction=tx).to_dict()
        if receipt is None:
            return None
        if payload is not None and (receipt["change_id"], receipt["base_project_revision"]) != (payload.change_id, payload.base_project_revision):
            raise _conflict("sync_rebind_request_mismatch", "This request ID belongs to a different rebind.")
        _verify_receipt(tx, doc, receipt, actor.sub, request_id)
        return _public(receipt)

    return read(client.transaction())


def _write_rebind(tx, doc, project, raw, source, actor, request_id, receipt_id):
    revision = project.current_revision + 1
    now = projects._utcnow()
    ids = {
        stage: projects._stable_document_id("snapshot", project.project_id, stage, SYNC_REBIND_OPERATION, receipt_id) for stage in ("use_cases", "test_cases")
    }
    receipt = {
        "schema_version": 1,
        "request_id": request_id,
        "actor_user_id": actor.sub,
        "project_id": project.project_id,
        "change_id": source.record["id"],
        "base_project_revision": project.current_revision,
        "project_revision": revision,
        "requirements_snapshot_id": source.baseline.snapshot_id,
        "context_snapshot_id": source.context.snapshot_id,
        "previous_use_cases_snapshot_id": source.old_plan.snapshot_id,
        "previous_test_cases_snapshot_id": source.old_suite.snapshot_id,
        "use_cases_snapshot_id": ids["use_cases"],
        "test_cases_snapshot_id": ids["test_cases"],
        "requirement_facts_hash": source.facts_hash,
        "input_manifest": source.manifest.model_dump(mode="json"),
        "use_cases_payload_hash": case_source_hash(source.old_plan.payload),
        "test_cases_payload_hash": case_source_hash(source.old_suite.payload),
    }
    states = projects._stage_state_from_payload(raw)
    outputs = []
    for old, upstream in ((source.old_plan, source.baseline.snapshot_id), (source.old_suite, ids["use_cases"])):
        stage = old.stage
        snapshot, state = _snapshot(
            project,
            stage,
            old.payload,
            {"id": receipt_id, "revision": 1},
            actor,
            revision,
            {
                "change_id": receipt["change_id"],
                "rebind_receipt_id": receipt_id,
                "provisional": True,
                "previous_snapshot_id": old.snapshot_id,
                "source_snapshot_ids": {"requirements": source.baseline.snapshot_id, "context": source.context.snapshot_id, "use_cases": ids["use_cases"]},
                "project_inputs": receipt["input_manifest"],
            },
        )
        snapshot.update(
            snapshot_id=ids[stage],
            operation=SYNC_REBIND_OPERATION,
            request_id=request_id,
            source_snapshot_id=upstream,
            title="Project Sync draft bound to approved Requirements",
        )
        if len(json.dumps(snapshot, default=str).encode()) > _MAX_SNAPSHOT_BYTES:
            raise HTTPException(413, "This Project Sync draft exceeds the safe rebind size.")
        state.update(current_snapshot_id=ids[stage], operation=SYNC_REBIND_OPERATION, source_snapshot_id=upstream)
        states[stage] = state
        outputs.append(snapshot)
    for stage in projects.DOWNSTREAM_STAGES["use_cases"]:
        if stage in states and stage != "test_cases":
            states[stage] = {**states[stage], "stale": True, "approved": False, "stale_reason": "Project Sync drafts rebound; review required"}
    updated = {**source.record, "review_rebind": receipt}
    for snapshot in outputs:
        tx.create(doc.collection("snapshots").document(snapshot["snapshot_id"]), snapshot)
        tx.create(
            doc.collection("timeline").document(snapshot["snapshot_id"]),
            {
                "event_id": snapshot["snapshot_id"],
                "project_id": project.project_id,
                "event_type": "stage.snapshot_created",
                "stage": snapshot["stage"],
                "summary": snapshot["title"],
                "project_revision": revision,
                "snapshot_id": snapshot["snapshot_id"],
                "actor_user_id": actor.sub,
                "metadata": {"change_id": receipt["change_id"], "operation": SYNC_REBIND_OPERATION},
                "occurred_at": now,
            },
        )
    tx.create(_receipt_doc(doc, receipt_id), receipt)
    tx.update(_change_doc(doc, receipt["change_id"]), {"review_rebind": receipt})
    tx.update(
        doc,
        {"current_revision": revision, "updated_at": now, "latest_stage": "test_cases", "stage_state": states, "project_sync": updated},
    )
    return _public(receipt)


def rebind_sync_drafts(project_id, actor, payload, request_id):
    if not request_id or not request_id.strip() or len(request_id) > 160:
        raise HTTPException(422, "A bounded X-Request-ID is required to rebind Project Sync drafts.")
    existing = read_sync_rebind_receipt(project_id, actor, request_id, payload=payload)
    if existing is not None:
        return existing
    client = draft_admission_client()
    if client is None:
        raise _conflict("sync_rebind_disabled", "Project Sync rebind is unavailable in this environment.")
    project = projects.get_project(project_id, actor=actor)
    doc = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)
    receipt_id = _receipt_id(actor.sub, request_id)

    @projects.transactional
    def apply(tx):
        raw = doc.get(transaction=tx).to_dict()
        if not raw:
            raise projects.ProjectNotFoundError(project_id)
        projects._require_owner(raw, actor)
        existing = _receipt_doc(doc, receipt_id).get(transaction=tx).to_dict()
        if existing is not None:
            if (existing["change_id"], existing["base_project_revision"]) != (payload.change_id, payload.base_project_revision):
                raise _conflict("sync_rebind_request_mismatch", "This request ID belongs to a different rebind.")
            _verify_receipt(tx, doc, existing, actor.sub, request_id)
            return _public(existing)
        projects._check_revision(raw, payload.base_project_revision)
        projects._check_revision(raw, project.current_revision)
        if (raw.get("project_sync") or {}).get("id") != payload.change_id:
            raise _conflict()
        lease = project_lease_ref(doc).get(transaction=tx).to_dict()
        if lease_blocks_claim(lease or {}, projects._utcnow()):
            raise _conflict("project_draft_busy", "Another project draft attempt is active or awaiting recovery.")
        source = _candidate(tx, client, doc, raw, project)
        return _write_rebind(tx, doc, project, raw, source, actor, request_id, receipt_id)

    try:
        return apply(client.transaction())
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        existing = read_sync_rebind_receipt(project_id, actor, request_id, payload=payload, client=client)
        if existing is not None:
            return existing
        raise _conflict() from exc
    except Exception:
        existing = read_sync_rebind_receipt(project_id, actor, request_id, payload=payload, client=client)
        if existing is not None:
            return existing
        raise


def _current_rebind(project, receipt):
    plan = project.current_snapshots.get("use_cases")
    suite = project.current_snapshots.get("test_cases")
    if not plan or not suite or plan.snapshot_id != receipt.get("use_cases_snapshot_id"):
        return False
    if any(project.stage_state[stage].stale for stage in ("use_cases", "test_cases")):
        return False
    binding = suite.metadata.get("sync_review_binding") or {}
    return suite.snapshot_id == receipt.get("test_cases_snapshot_id") or (
        suite.operation == "test_cases.review"
        and suite.source_snapshot_id == receipt.get("test_cases_snapshot_id")
        and binding.get("rebind_receipt_hash") == case_source_hash(receipt)
    )


def sync_rebind_status(client, project_id, actor):
    project = projects.get_project(project_id, actor=actor)
    record = project.project_sync
    if not _material_record(record):
        return None
    change_id = record["id"]
    if receipt := project._project_sync_rebind:
        if record.get("status") == "ready_for_review" and _current_rebind(project, receipt):
            try:
                verified = read_sync_rebind_receipt(project_id, actor, receipt["request_id"], client=client)
                if verified == _public(receipt):
                    return ProjectSyncRebindStatus(change_id=change_id, state="bound")
            except HTTPException, GoogleAPICallError, KeyError, TypeError, ValueError, AttributeError, ValidationError:
                pass
        return ProjectSyncRebindStatus(change_id=change_id, state="unavailable")
    if project.material_requirements_review_required:
        return ProjectSyncRebindStatus(change_id=change_id, state="needs_requirement_approval")
    doc = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)

    @projects.transactional
    def eligible(tx):
        raw = doc.get(transaction=tx).to_dict()
        if not raw:
            raise projects.ProjectNotFoundError(project_id)
        projects._require_owner(raw, actor)
        projects._check_revision(raw, project.current_revision)
        _candidate(tx, client, doc, raw, project)

    try:
        eligible(client.transaction())
    except HTTPException, GoogleAPICallError, projects.ProjectConflictError, KeyError, TypeError, ValueError, AttributeError, ValidationError:
        return ProjectSyncRebindStatus(change_id=change_id, state="unavailable")
    return ProjectSyncRebindStatus(change_id=change_id, state="available")
