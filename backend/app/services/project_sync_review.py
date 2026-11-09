"""Verify the exact Project Sync draft chain before saved-suite review."""

from dataclasses import dataclass
import re

from fastapi import HTTPException
from google.api_core.exceptions import GoogleAPICallError
from pydantic import ValidationError

from ..contracts.project_sync import ProjectInputManifest
from ..contracts.requirements import RequirementAnalysis, RequirementCoveragePlan
from ..contracts.test_cases import TestCase
from . import workflow_project_service as projects
from .approval_scenario_worker import approval_test_cases_enabled
from .orchestrator_service import _matching_use_case_human_review, _use_cases_human_approved
from .project_draft_admission import draft_admission_client, verify_completed_sync_stage
from .project_inputs import manifest_from_payload
from .requirement_scope import approved_generation_requirements
from .scenario_review_state import current_scenario_reviews
from .test_case_quality import case_source_hash
from .versioning_service import TEST_CASE_SETS_COLLECTION

SYNC_DRAFT_OPERATION = "project.sync.draft"
SYNC_REBIND_OPERATION = "project.sync.rebind"
SYNC_REVIEW_OPERATIONS = {SYNC_DRAFT_OPERATION, SYNC_REBIND_OPERATION}
_SOURCE_STAGES = ("requirements", "context", "use_cases", "test_cases", "reports")


@dataclass(frozen=True)
class _Lineage:
    record: dict
    manifest: ProjectInputManifest
    plan: projects.QaProjectStageSnapshot
    draft_id: str
    plan_receipt: dict
    draft_receipt: dict
    review: dict
    rebind: dict | None = None

    def binding(self, rows):
        return {
            "schema_version": 1,
            "change_id": self.record["id"],
            "use_cases_snapshot_id": self.plan.snapshot_id,
            "test_cases_snapshot_id": self.draft_id,
            "use_cases_receipt_hash": case_source_hash(self.plan_receipt),
            "test_cases_receipt_hash": case_source_hash(self.draft_receipt),
            "scenario_review_id": self.review["review_id"],
            "project_input_manifest_hash": case_source_hash(self.manifest.model_dump(mode="json")),
            "draft_saved_rows_hash": case_source_hash(rows),
            **({"rebind_receipt_hash": case_source_hash(self.rebind)} if self.rebind is not None else {}),
        }


def _unavailable():
    return HTTPException(
        409,
        {
            "code": "sync_review_lineage_unavailable",
            "message": "Review needs the exact current correction, its saved drafts and the approved scenario plan.",
        },
    )


def _current(project, stage):
    snapshot = project.current_snapshots.get(stage)
    state = project.stage_state.get(stage)
    if (
        snapshot
        and state
        and not state.stale
        and snapshot.snapshot_id == state.current_snapshot_id
        and snapshot.stage == stage
        and snapshot.project_id == project.project_id
    ):
        return snapshot
    return None


def _source_ids(value):
    if not isinstance(value, dict) or not value.keys() <= set(_SOURCE_STAGES):
        raise _unavailable()
    return {stage: value.get(stage) for stage in _SOURCE_STAGES}


def _id(value):
    return isinstance(value, str) and bool(value.strip()) and "/" not in value


def _receipt(record, stage):
    receipt = record["stage_receipts"][stage]
    if (
        not _id(receipt["snapshot_id"])
        or not isinstance(receipt["request_id"], str)
        or not receipt["request_id"].strip()
        or len(receipt["request_id"]) > 160
        or type(receipt["base_revision"]) is not int
        or type(receipt["result_revision"]) is not int
        or receipt["result_revision"] != receipt["base_revision"] + 1
        or not isinstance(receipt["settings_fingerprint"], str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["settings_fingerprint"]) is None
    ):
        raise _unavailable()
    return receipt


def _snapshot_matches(snapshot, receipt, record, manifest, actor_id, *, upstream, previous):
    metadata = snapshot.metadata
    return (
        snapshot.operation == SYNC_DRAFT_OPERATION
        and snapshot.actor_user_id == actor_id
        and snapshot.approved is False
        and snapshot.snapshot_id == receipt["snapshot_id"]
        and snapshot.request_id == receipt["request_id"]
        and snapshot.project_revision == receipt["result_revision"]
        and snapshot.source_snapshot_id == upstream
        and metadata.get("change_id") == record["id"]
        and metadata.get("provisional") is True
        and metadata.get("previous_snapshot_id") == previous
        and metadata.get("settings_fingerprint") == receipt["settings_fingerprint"]
        and _source_ids(metadata.get("source_snapshot_ids")) == _source_ids(receipt["source_snapshot_ids"])
        and ProjectInputManifest.model_validate(metadata.get("project_inputs")) == manifest
        and ProjectInputManifest.model_validate((metadata.get("guidance") or {}).get("project_inputs")) == manifest
    )


def _current_manifest(project):
    return manifest_from_payload(
        {
            "project_id": project.project_id,
            "input_revision": project.input_revision,
            "stage_state": {stage: state.model_dump() for stage, state in project.stage_state.items()},
            "project_decisions": project.project_decisions,
        }
    )


def completed_sync_pair(project, record, context):
    """Validate the immutable two-stage publication chain, before any later approval."""
    if (
        not record
        or record.get("draft_admission_version") != 1
        or record.get("status") != "ready_for_review"
        or not isinstance(record.get("id"), str)
        or re.fullmatch(r"change_[0-9a-f]{24}", record["id"]) is None
        or not context
        or context.operation != "quality.feedback.apply"
        or context.metadata.get("change_id") != record["id"]
        or context.actor_user_id != project.owner_user_id
        or record["stages"].get("context") != "draft"
        or any(record["stages"].get(stage) != "draft" for stage in ("use_cases", "test_cases"))
    ):
        raise _unavailable()
    manifest = ProjectInputManifest.model_validate(record["input_manifest"])
    decisions = [decision.model_dump(mode="json") for decision in manifest.decisions]
    if (
        manifest.project_id != project.project_id
        or manifest.context_snapshot_id != context.snapshot_id
        or not _id(manifest.requirements_snapshot_id)
        or record.get("decision") not in decisions
        or record["decision"].get("id") != record["id"]
        or record["decision"].get("accepted_by") != project.owner_user_id
        or record["decision"].get("finding_id") != record.get("finding_id")
        or context.payload.get("project_decisions") != decisions
    ):
        raise _unavailable()
    plan_receipt = _receipt(record, "use_cases")
    draft_receipt = _receipt(record, "test_cases")
    plan_sources = _source_ids(plan_receipt["source_snapshot_ids"])
    draft_sources = _source_ids(draft_receipt["source_snapshot_ids"])
    expected = _source_ids({stage: value for stage, value in record["expected_snapshots"].items() if stage in _SOURCE_STAGES})
    if (
        plan_sources["requirements"] != manifest.requirements_snapshot_id
        or plan_sources["context"] != context.snapshot_id
        or not _id(plan_sources["test_cases"])
        or draft_sources != {**plan_sources, "use_cases": plan_receipt["snapshot_id"]}
        or expected != {**draft_sources, "test_cases": draft_receipt["snapshot_id"]}
        or plan_receipt["base_revision"] != context.project_revision
        or draft_receipt["base_revision"] != plan_receipt["result_revision"]
        or record["project_revision"] != draft_receipt["result_revision"]
    ):
        raise _unavailable()
    return manifest, plan_receipt, draft_receipt


def _lineage(project):
    record = project.project_sync
    if project._project_sync_rebind and record:
        record = {**record, "review_rebind": project._project_sync_rebind}
    baseline, context, plan = (_current(project, stage) for stage in ("requirements", "context", "use_cases"))
    if not baseline or not plan:
        raise _unavailable()
    original_manifest, plan_receipt, draft_receipt = completed_sync_pair(project, record, context)
    manifest = _current_manifest(project)
    rebind = None
    if plan.operation == SYNC_REBIND_OPERATION:
        from .project_sync_rebind import validate_rebound_plan

        rebind = validate_rebound_plan(project, record, baseline, plan, manifest, original_manifest, plan_receipt, draft_receipt)
        draft_id, draft_revision = rebind["test_cases_snapshot_id"], rebind["project_revision"]
    else:
        if (
            record["stages"].get("requirements") != "preserved"
            or original_manifest != manifest
            or baseline.project_revision >= context.project_revision
            or not _snapshot_matches(
                plan,
                plan_receipt,
                record,
                manifest,
                project.owner_user_id,
                upstream=baseline.snapshot_id,
                previous=_source_ids(plan_receipt["source_snapshot_ids"])["use_cases"],
            )
        ):
            raise _unavailable()
        draft_id, draft_revision = draft_receipt["snapshot_id"], draft_receipt["result_revision"]
    state = project.stage_state["use_cases"]
    review = _matching_use_case_human_review(state)
    reviews = current_scenario_reviews(plan.payload, state.metadata, plan.snapshot_id)
    approval_revision = review.get("resulting_project_revision")
    if (
        not _use_cases_human_approved(state)
        or not _id(review.get("review_id"))
        or review.get("reviewer_user_id") != project.owner_user_id
        or type(approval_revision) is not int
        or not draft_revision < approval_revision <= project.current_revision
        or not reviews
        or any(item["status"] != "approved" for item in reviews.values())
    ):
        raise _unavailable()
    return _Lineage(record, manifest, plan, draft_id, plan_receipt, draft_receipt, review, rebind)


def _validate_draft(project, suite, lineage):
    current = _current(project, "test_cases")
    state = project.stage_state.get("test_cases")
    if not current or not state or state.approved or current.snapshot_id != suite.snapshot_id or suite.snapshot_id != lineage.draft_id:
        raise _unavailable()
    if lineage.rebind is not None:
        from .project_sync_rebind import validate_rebound_suite

        validate_rebound_suite(project, lineage.rebind, suite)
    else:
        _validate_original_snapshot(project, suite, lineage.record, lineage.manifest, lineage.draft_receipt, lineage.plan.snapshot_id)
    return validate_sync_suite_scope(project, suite, lineage.plan, lineage.record)


def _validate_original_snapshot(project, suite, record, manifest, receipt, plan_id):
    if (
        suite.stage != "test_cases"
        or suite.project_id != project.project_id
        or not _id(suite.source_event_id)
        or not _snapshot_matches(
            suite, receipt, record, manifest, project.owner_user_id, upstream=plan_id, previous=_source_ids(receipt["source_snapshot_ids"])["test_cases"]
        )
    ):
        raise _unavailable()


def validate_sync_suite_scope(project, suite, plan, record):
    from .suite_quality import _case_rows, _case_scope_gaps, _versioned_cases

    if (
        (suite.payload.get("substantive_assessment") or {}).get("status") != "unknown"
        or not isinstance(suite.payload.get("test_cases"), list)
        or suite.payload.get("generation_tasks") != []
    ):
        raise _unavailable()
    requirements = approved_generation_requirements(project)
    groups = [RequirementCoveragePlan.model_validate(row, extra="forbid") for row in plan.payload["coverage_plan"]]
    analysis = [RequirementAnalysis.model_validate(row, extra="forbid") for row in plan.payload["requirement_analysis"]]
    texts = {row.id: row.text for row in requirements}
    affected = record.get("affected_requirement_ids")
    if (
        not texts
        or not isinstance(affected, list)
        or not affected
        or len(set(affected)) != len(affected)
        or not set(affected) <= texts.keys()
        or len(groups) != len(texts)
        or {group.requirement_id: group.requirement_text for group in groups} != texts
        or len(analysis) != len(texts)
        or {row.requirement_id: row.requirement_text for row in analysis} != texts
        or suite.payload["coverage_plan"] != plan.payload["coverage_plan"]
        or suite.payload["requirement_analysis"] != plan.payload["requirement_analysis"]
    ):
        raise _unavailable()
    scenarios = {scenario.id: scenario for group in groups for scenario in group.scenarios}
    rows = _case_rows(suite.payload["test_cases"])
    if (
        len(scenarios) != sum(len(group.scenarios) for group in groups)
        or any(not group.scenarios or any(scenario.requirement_id != group.requirement_id for scenario in group.scenarios) for group in groups)
        or not _versioned_cases(rows)
        or _case_scope_gaps([row for row in rows if row["status"] != "Deprecated"], scenarios)
    ):
        raise _unavailable()
    return set(affected)


def _verify_case_versions(transaction, client, project_doc, draft_receipt, suite, affected):
    source_id = draft_receipt["source_snapshot_ids"]["test_cases"]
    old = project_doc.collection("snapshots").document(source_id).get(transaction=transaction).to_dict()
    if (
        not old
        or old.get("snapshot_id") != source_id
        or old.get("project_id") != project_doc.id
        or old.get("stage") != "test_cases"
        or not isinstance((old.get("payload") or {}).get("test_cases"), list)
    ):
        raise _unavailable()
    rows = suite.payload["test_cases"]
    previous = old["payload"]["test_cases"]
    if not all(isinstance(row, dict) for row in previous):
        raise _unavailable()
    kept = [row for row in previous if not (set(row.get("linked_requirement_ids", [])) & affected)]
    if rows[: len(kept)] != kept or len(kept) >= len(rows):
        raise _unavailable()
    for row in rows[len(kept) :]:
        case = TestCase.model_validate(row, strict=True, extra="forbid")
        if case.status != "Draft" or not case.linked_requirement_ids or not set(case.linked_requirement_ids) <= affected:
            raise _unavailable()
        ref = (
            client.collection(TEST_CASE_SETS_COLLECTION)
            .document(case.artifact_set_id)
            .collection("items")
            .document(case.artifact_item_id)
            .collection("versions")
            .document(case.artifact_version_id)
        )
        saved = ref.get(transaction=transaction).to_dict()
        if (
            not saved
            or saved.get("operation") != "project.sync"
            or saved.get("request_id") != suite.request_id
            or saved.get("actor_user_id") != suite.actor_user_id
            or saved.get("source_event_id") != suite.source_event_id
            or saved.get("set_id") != case.artifact_set_id
            or saved.get("item_id") != case.artifact_item_id
            or saved.get("version_id") != case.artifact_version_id
            or saved.get("version_number") != case.artifact_version_number
            or saved.get("payload") != case.model_dump(exclude={"artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number"})
        ):
            raise _unavailable()


def verify_original_sync_pair(transaction, client, project, project_doc, record, context, plan, suite, affected):
    manifest, plan_receipt, draft_receipt = completed_sync_pair(project, record, context)
    if not _snapshot_matches(
        plan,
        plan_receipt,
        record,
        manifest,
        project.owner_user_id,
        upstream=manifest.requirements_snapshot_id,
        previous=_source_ids(plan_receipt["source_snapshot_ids"])["use_cases"],
    ):
        raise _unavailable()
    _validate_original_snapshot(project, suite, record, manifest, draft_receipt, plan.snapshot_id)
    for snapshot, receipt in ((plan, plan_receipt), (suite, draft_receipt)):
        verify_completed_sync_stage(
            transaction,
            project_doc,
            record,
            snapshot.stage,
            actor_id=project.owner_user_id,
            snapshot_id=receipt["snapshot_id"],
            result_revision=receipt["result_revision"],
        )
        persisted = project_doc.collection("snapshots").document(snapshot.snapshot_id).get(transaction=transaction, field_paths=("payload",)).to_dict()
        if not persisted or persisted.get("payload") != snapshot.payload:
            raise _unavailable()
    _verify_case_versions(transaction, client, project_doc, draft_receipt, suite, affected)


def verify_sync_review(client, project, suite, actor, *, transaction=None):
    """Verify public records, private completion receipts and raw version identities."""
    try:
        if project.status != "active":
            raise _unavailable()
        lineage = _lineage(project)
        affected = _validate_draft(project, suite, lineage)
        project_doc = client.collection(projects.QA_PROJECTS_COLLECTION).document(project.project_id)

        def read(tx):
            current = project_doc.get(transaction=tx).to_dict()
            if current is None:
                raise projects.ProjectNotFoundError(project.project_id)
            projects._require_owner(current, actor)
            projects._check_revision(current, project.current_revision)
            record = project_doc.collection("project_changes").document(lineage.record["id"]).get(transaction=tx).to_dict()
            if current.get("project_sync") != lineage.record or record != lineage.record or manifest_from_payload(current) != lineage.manifest:
                raise _unavailable()
            context = project.current_snapshots["context"]
            if lineage.rebind is not None:
                from .project_sync_rebind import verify_rebound_history

                old_plan, old_suite = verify_rebound_history(tx, project_doc, project, lineage.record, lineage.rebind, lineage.plan, suite)
            else:
                old_plan, old_suite = lineage.plan, suite
            verify_original_sync_pair(tx, client, project, project_doc, record, context, old_plan, old_suite, affected)
            review = project_doc.collection("use_case_reviews").document(lineage.review["review_id"]).get(transaction=tx).to_dict()
            if (
                not review
                or review.get("review_id") != lineage.review["review_id"]
                or review.get("project_id") != project.project_id
                or review.get("stage") != "use_cases"
                or review.get("snapshot_id") != lineage.plan.snapshot_id
                or review.get("reviewer_user_id") != actor.sub
                or review.get("decision") != "approve"
                or review.get("resulting_project_revision") != lineage.review["resulting_project_revision"]
                or review.get("base_project_revision") != review["resulting_project_revision"] - 1
                or review.get("require_prepared_journey")
                or review.get("continuation_options_fingerprint")
            ):
                raise _unavailable()
            return lineage.binding(suite.payload["test_cases"]), lineage.manifest

        return read(transaction) if transaction is not None else projects.transactional(read)(client.transaction())
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise _unavailable() from exc


def sync_review_available(project, suite, actor):
    if not approval_test_cases_enabled():
        return False
    client = draft_admission_client(read_only=True)
    if client is None:
        return False
    try:
        verify_sync_review(client, project, suite, actor)
    except HTTPException, GoogleAPICallError, projects.ProjectConflictError, projects.ProjectNotFoundError:
        return False
    return True


def sync_review_binding_for_proof(project, payload, metadata):
    """Recheck the successor's bound lineage without re-enabling any draft actions."""
    try:
        lineage = _lineage(project)
    except HTTPException, KeyError, TypeError, ValueError, AttributeError, ValidationError:
        return None
    binding = lineage.binding(payload["test_cases"])
    guidance = metadata.get("guidance") or {}
    manifest_id = guidance.get("manifest_id")
    assessment = payload.get("substantive_assessment") or {}
    if (
        metadata.get("sync_review_binding") != binding
        or metadata.get("source_test_cases_snapshot_id") != lineage.draft_id
        or not _id(manifest_id)
        or assessment.get("guidance_manifest_id") != manifest_id
        or ProjectInputManifest.model_validate(guidance.get("project_inputs")) != lineage.manifest
    ):
        return None
    return binding
