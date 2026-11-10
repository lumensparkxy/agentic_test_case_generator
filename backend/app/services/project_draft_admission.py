"""Share one provider fence across exact foreground draft operations."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import re
from threading import Lock

from fastapi import HTTPException
from google.api_core.exceptions import Aborted
from google.cloud.firestore_v1 import transactional

from ..config import DEFAULT_MODEL_NAME
from . import provider_journey_budget as journeys
from .approval_continuation import lease_blocks_claim, lease_matches, project_lease_ref, released_project_lease
from .firestore_repository import get_required_firestore_client
from .guidance_service import enabled
from .workflow_project_service import QA_PROJECTS_COLLECTION, ProjectNotFoundError, ProjectPermissionError

DEMO_PROJECT_ID = "demo-tcg-v2-continuation"
_REVIEW_OPERATION = "test_cases.review"
REPAIR_OPERATION = "testcases.repair"
SCENARIO_REFRESH_OPERATION = "use_cases.refresh"
SCOPED_IMPACT_OPERATION = "impact.update.apply"
REQUIREMENT_EXTRACTION_OPERATIONS = frozenset({"requirements.parse", "requirements.refine"})
REQUIREMENT_EXTRACTION_REQUEST_CAP = 16
REQUIREMENT_EXTRACTION_DEADLINE_SECONDS = 180
_OPERATION_SOURCE_STAGE = {
    SCENARIO_REFRESH_OPERATION: "use_cases",
    **dict.fromkeys(REQUIREMENT_EXTRACTION_OPERATIONS, "requirements"),
}
_SCENARIO_REFRESH_SCOPE_FIELDS = {"refreshed_requirement_ids", "retained_requirement_ids", "removed_requirement_ids"}
_REPAIR_BINDING_FIELDS = {"target_id", "case_id", "case_version_id", "case_version_number", "request_fingerprint"}
_SOURCE_STAGES = {"requirements", "context", "use_cases", "test_cases"}
_SYNC_SOURCE_STAGES = _SOURCE_STAGES | {"reports"}
_SYNC_STAGES = {"project.sync.use_cases": "use_cases", "project.sync.test_cases": "test_cases"}
_REQUEST_CAPS = {
    _REVIEW_OPERATION: 24,
    REPAIR_OPERATION: 8,
    SCENARIO_REFRESH_OPERATION: 7,
    SCOPED_IMPACT_OPERATION: 24,
    "project.sync.use_cases": 7,
    "project.sync.test_cases": 24,
    **dict.fromkeys(REQUIREMENT_EXTRACTION_OPERATIONS, REQUIREMENT_EXTRACTION_REQUEST_CAP),
}
_ATTEMPTS = "project_draft_attempts"
_KIND = "draft"
_LEASE_SECONDS = 60
_MAX_REQUEST_SECONDS = 25
_BUSY = "Another project draft attempt is active or awaiting recovery."


def _draft_admission_requested():
    return os.getenv("TCG_APPROVAL_SCENARIOS_ENABLED") == "true" and os.getenv("FIREBASE_PROJECT_ID") == DEMO_PROJECT_ID


def draft_admission_enabled():
    target = re.fullmatch(r"(?:127\.0\.0\.1|\[::1\]):([0-9]{1,5})", os.getenv("FIRESTORE_EMULATOR_HOST", ""))
    return bool(_draft_admission_requested() and target and 1 <= int(target.group(1)) <= 65535)


def demo_client_matches(client):
    # The Firestore client resolves its emulator transport at construction.
    return (
        draft_admission_enabled()
        and getattr(client, "project", None) == DEMO_PROJECT_ID
        and getattr(client, "_emulator_host", None) == os.getenv("FIRESTORE_EMULATOR_HOST")
    )


def demo_provider_allowed():
    return (
        draft_admission_enabled()
        and os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"", "false", "0"}
        and os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME).startswith("gemini-")
    )


def draft_admission_client(client=None, *, read_only=False):
    if not _draft_admission_requested():
        return None
    if not draft_admission_enabled():
        if read_only:
            return None
        raise HTTPException(503, "Project draft admission is unavailable.")
    if client is None:
        try:
            client = get_required_firestore_client(unavailable_message="Project draft admission is unavailable.")
        except RuntimeError as exc:
            if read_only:
                return None
            raise HTTPException(503, "Project draft admission is unavailable.") from exc
    if not demo_client_matches(client):
        if read_only:
            return None
        raise HTTPException(503, "Project draft admission is unavailable.")
    return client


def _conflict(code, message):
    return HTTPException(409, {"code": code, "message": message})


def require_standard_extraction_guidance():
    if _draft_admission_requested() and (enabled("memory", "requirements") or enabled("skills", "requirements")):
        raise _conflict("project_draft_budget_unavailable", "Requirement extraction in this demo requires the standard guidance settings.")


def draft_refresh_deferred():
    return _conflict("project_draft_refresh_deferred", "Paid draft refresh is unavailable until it uses shared project admission.")


def _utcnow():
    return datetime.now(timezone.utc)


def _project_doc(client, project_id):
    return client.collection(QA_PROJECTS_COLLECTION).document(project_id)


def _attempt_doc(project_doc, operation_id):
    return project_doc.collection(_ATTEMPTS).document(operation_id)


def _operation_id(project_id, actor_id, operation, request_id, change_id=None):
    # Parse and refine share one request endpoint and one import preview identity.
    namespace = "requirements.extraction" if operation in REQUIREMENT_EXTRACTION_OPERATIONS else operation
    identity = [project_id, actor_id, namespace, request_id]
    if change_id is not None:
        identity.append(change_id)
    fingerprint = sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    return f"draft_{fingerprint}"


def _is_operation_id(value):
    return isinstance(value, str) and re.fullmatch(r"draft_[0-9a-f]{64}", value) is not None


def _read(transaction, doc, *, field_paths=None):
    return doc.get(transaction=transaction, field_paths=field_paths).to_dict()


def _require_owner(project, project_id, actor_id):
    if not project or project.get("project_id") != project_id:
        raise ProjectNotFoundError(project_id)
    if project.get("owner_user_id") != actor_id:
        raise ProjectPermissionError(project_id)


def _matches_sources(project, attempt):
    if (
        not project
        or project.get("project_id") != attempt["project_id"]
        or project.get("owner_user_id") != attempt["actor_id"]
        or project.get("status") != "active"
        or project.get("current_revision") != attempt["base_revision"]
    ):
        return False
    stage_to_refresh = _SYNC_STAGES.get(attempt["operation"])
    if stage_to_refresh:
        sync = project.get("project_sync") or {}
        expected = sync.get("expected_snapshots") or {}
        stage_status = sync.get("stages") or {}
        if (
            sync.get("id") != attempt["change_id"]
            or sync.get("project_revision") != attempt["base_revision"]
            or stage_status.get(stage_to_refresh) != "pending"
            or (stage_to_refresh == "test_cases" and stage_status.get("use_cases") != "draft")
            or {stage: expected.get(stage) for stage in _SYNC_SOURCE_STAGES} != attempt["source_snapshot_ids"]
        ):
            return False
    states = project.get("stage_state") or {}
    scenario_refresh = attempt["operation"] == SCENARIO_REFRESH_OPERATION
    if scenario_refresh:
        predecessor = states.get("use_cases") or {}
        human = (predecessor.get("metadata") or {}).get("latest_human_review") or {}
        if (
            not predecessor.get("approved")
            or human.get("review_id") != attempt["scenario_refresh"]["previous_review_id"]
            or human.get("snapshot_id") != attempt["source_snapshot_ids"]["use_cases"]
            or human.get("reviewer_user_id") != attempt["actor_id"]
            or human.get("decision") != "approve"
            or not (states.get("test_cases") or {}).get("stale")
            or not predecessor.get("stale")
            or (states.get("requirements") or {}).get("operation") != "requirements.review"
            or (states.get("context") or {}).get("approved") is not True
        ):
            return False
    scoped_impact = attempt["operation"] == SCOPED_IMPACT_OPERATION
    if scoped_impact:
        plan = states.get("use_cases") or {}
        review = (plan.get("metadata") or {}).get("latest_human_review") or {}
        if (
            not plan.get("approved")
            or review.get("decision") != "approve"
            or review.get("snapshot_id") != attempt["source_snapshot_ids"]["use_cases"]
            or review.get("review_id") != attempt["scoped_impact"]["review_id"]
            or review.get("reviewer_user_id") != attempt["actor_id"]
            or not (states.get("test_cases") or {}).get("stale")
            or (states.get("requirements") or {}).get("operation") != "requirements.review"
            or (states.get("context") or {}).get("approved") is not True
        ):
            return False
    merge_inputs = {"test_cases", "reports"} if stage_to_refresh == "test_cases" else {"use_cases", "test_cases", "reports"}
    for stage, snapshot_id in attempt["source_snapshot_ids"].items():
        state = states.get(stage) or {}
        may_review_stale_suite = attempt["operation"] == _REVIEW_OPERATION and stage == "test_cases" and state.get("operation") == "quality.feedback.apply"
        may_merge_stale = (stage_to_refresh and stage in merge_inputs) or (scenario_refresh and stage in {"use_cases", "test_cases"})
        may_merge_stale = may_merge_stale or (scoped_impact and stage == "test_cases")
        if state.get("current_snapshot_id") != snapshot_id or (state.get("stale") and not (may_review_stale_suite or may_merge_stale)):
            return False
    return True


def _same_attempt(attempt, binding):
    return attempt and attempt.get("schema_version") == 1 and all(attempt.get(key) == value for key, value in binding.items())


def _same_holder(lease, attempt):
    return lease_matches(lease, kind=_KIND, operation_id=attempt["operation_id"], fence=attempt["fence"])


def _valid_until(expires_at, now):
    return isinstance(expires_at, datetime) and expires_at.tzinfo is not None and expires_at > now


def _recorded_sync_sources_match(recorded, sources):
    return (
        isinstance(recorded, dict)
        and recorded.keys() <= sources.keys()
        and {stage: source for stage, source in recorded.items() if source is not None}
        == {stage: source for stage, source in sources.items() if source is not None}
    )


def _snapshot_matches(snapshot, attempt):
    sources = attempt["source_snapshot_ids"]
    metadata = (snapshot or {}).get("metadata") or {}
    sync_stage = _SYNC_STAGES.get(attempt["operation"])
    if sync_stage:
        output_stage = sync_stage
        operation = "project.sync.draft"
        source_snapshot_id = sources["requirements" if sync_stage == "use_cases" else "use_cases"]
        sources_match = metadata.get("change_id") == attempt["change_id"] and _recorded_sync_sources_match(metadata.get("source_snapshot_ids"), sources)
    elif attempt["operation"] == SCENARIO_REFRESH_OPERATION:
        output_stage = "use_cases"
        operation = "use_cases.generate"
        source_snapshot_id = sources["requirements"]
        binding = attempt["scenario_refresh"]
        sources_match = (
            (snapshot or {}).get("approved") is False
            and metadata.get("source_requirements_snapshot_id") == sources["requirements"]
            and metadata.get("source_context_snapshot_id") == sources["context"]
            and metadata.get("use_case_refresh") == binding["scope"]
            and metadata.get("scenario_refresh_predecessor")
            == {
                "previous_requirements_snapshot_id": binding["previous_requirements_snapshot_id"],
                "previous_review_id": binding["previous_review_id"],
                "source_test_cases_snapshot_id": sources["test_cases"],
            }
            and metadata.get("generation_request_fingerprint") == binding["request_fingerprint"]
        )
    elif attempt["operation"] == SCOPED_IMPACT_OPERATION:
        output_stage = "test_cases"
        operation = SCOPED_IMPACT_OPERATION
        source_snapshot_id = sources["impact_analysis"]
        expected_sources = {stage: sources[stage] for stage in _SOURCE_STAGES}
        sources_match = (
            (snapshot or {}).get("approved") is False
            and metadata.get("scoped_impact") == attempt["scoped_impact"]
            and _recorded_sync_sources_match(metadata.get("source_snapshot_ids"), expected_sources)
            and _impact_versions_match(snapshot, attempt["scoped_impact"])
        )
    elif attempt["operation"] == REPAIR_OPERATION:
        output_stage = "test_cases"
        operation = REPAIR_OPERATION
        source_snapshot_id = sources["use_cases"]
        expected_sources = {stage: sources[stage] for stage in ("requirements", "context", "use_cases")}
        sources_match = (
            _recorded_sync_sources_match(metadata.get("source_snapshot_ids"), expected_sources)
            and metadata.get("selected_repair") == attempt["selected_repair"]
            and metadata.get("source_test_cases_snapshot_id") == sources["test_cases"]
            and metadata.get("generation_request_fingerprint") == attempt["selected_repair"]["request_fingerprint"]
            and _repair_version_matches(snapshot, attempt["selected_repair"])
        )
    else:
        output_stage = "test_cases"
        operation = attempt["operation"]
        source_snapshot_id = sources["test_cases"]
        expected_sources = {stage: sources[stage] for stage in ("requirements", "context", "use_cases") if sources[stage] is not None}
        sources_match = metadata.get("source_snapshot_ids") == expected_sources
    return bool(
        snapshot
        and snapshot.get("snapshot_id")
        and snapshot.get("project_id") == attempt["project_id"]
        and snapshot.get("actor_user_id") == attempt["actor_id"]
        and snapshot.get("request_id") == attempt["request_id"]
        and snapshot.get("stage") == output_stage
        and snapshot.get("operation") == operation
        and snapshot.get("project_revision") == attempt["base_revision"] + 1
        and snapshot.get("source_snapshot_id") == source_snapshot_id
        and sources_match
    )


def _repair_version_matches(snapshot, binding):
    rows = (snapshot.get("payload") or {}).get("test_cases") or []
    cases = [row for row in rows if row.get("id") == binding["case_id"]]
    return (
        len(cases) == 1
        and isinstance(cases[0].get("artifact_version_id"), str)
        and bool(cases[0]["artifact_version_id"])
        and cases[0]["artifact_version_id"] != binding["case_version_id"]
        and cases[0].get("artifact_version_number") == binding["case_version_number"] + 1
    )


def _impact_versions_match(snapshot, binding):
    payload = (snapshot or {}).get("payload") or {}
    result = payload.get("impact_update_result") or {}
    versions = binding["case_versions"]
    if (
        result.get("applied_recommendation_ids") != binding["accepted_recommendation_ids"]
        or result.get("changed_test_case_ids") != sorted(versions)
        or result.get("updated_count") != len(versions)
        or result.get("added_count") != 0
        or result.get("deprecated_count") != 0
    ):
        return False
    return all(_repair_version_matches(snapshot, {"case_id": case_id, **version}) for case_id, version in versions.items())


def _import_matches(preview, attempt):
    binding = attempt["requirement_extraction"]
    return bool(
        preview
        and preview.get("import_id") == binding["import_id"]
        and preview.get("project_id") == attempt["project_id"]
        and preview.get("base_project_revision") == attempt["base_revision"]
        and preview.get("operation") == attempt["operation"]
        and (preview.get("extraction") or {}).get("fingerprint") == binding["request_fingerprint"]
    )


def _read_result(transaction, project_doc, attempt, *, include_snapshot=False):
    if attempt["operation"] in REQUIREMENT_EXTRACTION_OPERATIONS:
        import_id = attempt.get("result_import_id")
        if not import_id or import_id != attempt["requirement_extraction"]["import_id"]:
            raise HTTPException(503, "The saved requirement preview could not be confirmed.")
        preview = _read(transaction, project_doc.collection("requirement_imports").document(import_id))
        if not _import_matches(preview, attempt):
            raise HTTPException(503, "The saved requirement preview could not be confirmed.")
        return import_id
    unavailable = {
        _REVIEW_OPERATION: "The saved assessment result could not be confirmed.",
        REPAIR_OPERATION: "The saved selected-case repair could not be confirmed.",
        SCENARIO_REFRESH_OPERATION: "The saved scenario refresh could not be confirmed.",
        SCOPED_IMPACT_OPERATION: "The saved impact application could not be confirmed.",
    }.get(attempt["operation"], "The saved Project Sync draft could not be confirmed.")
    fields = ("snapshot_id", "project_id", "actor_user_id", "request_id", "stage", "operation", "project_revision", "source_snapshot_id", "metadata")
    if attempt["operation"] == REPAIR_OPERATION:
        fields += ("payload",)
    if attempt["operation"] == SCENARIO_REFRESH_OPERATION:
        fields += ("approved",)
    if attempt["operation"] == SCOPED_IMPACT_OPERATION:
        fields += ("approved", "payload")
    snapshot_id = attempt.get("result_snapshot_id")
    if not snapshot_id:
        raise HTTPException(503, unavailable)
    snapshot = _read(
        transaction,
        project_doc.collection("snapshots").document(snapshot_id),
        field_paths=fields,
    )
    if not _snapshot_matches(snapshot, attempt) or snapshot["snapshot_id"] != snapshot_id:
        raise HTTPException(503, unavailable)
    return snapshot if include_snapshot else snapshot_id


def _projected_state(project, attempt, lease, now):
    if attempt["state"] == "interrupted":
        return "interrupted", "provider_outcome_unconfirmed"
    if attempt["state"] == "running":
        if _valid_until(lease.get("expires_at"), now) and _matches_sources(project, attempt):
            return "running", None
        if lease.get("dispatched") or attempt.get("provider_requests_reserved"):
            return "interrupted", "provider_outcome_unconfirmed"
    return None, None


class DraftAttemptClaim:
    """In-process capability for one exact persisted attempt and fence."""

    def __init__(self, client, binding, fence, result_snapshot_id=None, *, result_import_id=None):
        self._client = client
        self._binding = binding
        self.operation_id = binding["operation_id"]
        self.request_id = binding["request_id"]
        self.journey_id = binding.get(journeys.BUDGET_FIELD)
        self.fence = fence
        self.result_snapshot_id = result_snapshot_id
        self.result_import_id = result_import_id
        self._project = _project_doc(client, binding["project_id"])
        self._attempt = _attempt_doc(self._project, self.operation_id)
        self._lease = project_lease_ref(self._project)
        self._started = 0
        self._reservation_failed = False
        self._lock = Lock()

    def _validate(self, transaction):
        project = _read(transaction, self._project)
        attempt = _read(transaction, self._attempt)
        lease = _read(transaction, self._lease) or {}
        now = _utcnow()
        if (
            not _same_attempt(attempt, self._binding)
            or attempt.get("state") != "running"
            or attempt.get("fence") != self.fence
            or not _same_holder(lease, attempt)
            or not _valid_until(lease.get("expires_at"), now)
            or not _valid_until(attempt.get("deadline_at"), now)
            or not _matches_sources(project, attempt)
        ):
            raise _conflict("project_draft_claim_lost", "This draft attempt expired, was cancelled, or no longer matches the saved sources.")
        return attempt, lease, now

    def validate(self, transaction):
        """Read and verify authority before staging checkpoint writes."""
        self._validate(transaction)

    def reserve(self):
        """Reserve the full stage cap durably before the first actual provider request."""
        operation = self._binding["operation"]
        guidance_stage = _OPERATION_SOURCE_STAGE.get(operation, _SYNC_STAGES.get(operation, "test_cases"))
        with self._lock:
            if (
                self._reservation_failed
                or not demo_provider_allowed()
                or not demo_client_matches(self._client)
                or enabled("memory", guidance_stage)
                or enabled("skills", guidance_stage)
            ):
                raise _conflict("project_draft_budget_unavailable", "This draft attempt cannot make another provider request.")

            @transactional
            def reserve_request(transaction):
                attempt, lease, now = self._validate(transaction)
                cap = attempt["max_requests"]
                already_dispatched = self._started > 0
                if (
                    self._started >= cap
                    or lease.get("dispatched") is not already_dispatched
                    or attempt.get("provider_requests_reserved") != (cap if already_dispatched else 0)
                ):
                    raise _conflict("project_draft_budget_unavailable", "This draft attempt cannot make another provider request.")
                expires_at = min(now + timedelta(seconds=_LEASE_SECONDS), attempt["deadline_at"])
                if self._started == 0:
                    if self.journey_id is not None:
                        budget = self._journey_budget(transaction)
                        journeys.reserve(transaction, budget, operation, self.operation_id, cap, now)
                    transaction.update(self._attempt, {"provider_requests_reserved": cap, "dispatched_at": now})
                transaction.update(self._lease, {"expires_at": expires_at, "dispatched": True})
                return attempt["deadline_at"]

            try:
                deadline_at = reserve_request(self._client.transaction())
                remaining = (deadline_at - _utcnow()).total_seconds()
                if remaining <= 0:
                    raise _conflict("project_draft_budget_unavailable", "The draft attempt deadline elapsed.")
            except Exception:
                # Do not retry a reservation when its transaction outcome may be unknown.
                self._reservation_failed = True
                raise
            self._started += 1
            return min(remaining, _MAX_REQUEST_SECONDS)

    def _journey_budget(self, transaction):
        if self._binding["operation"] == journeys.INTAKE_STAGE:
            budget = journeys.read_budget(transaction, self._project, self._binding["actor_id"], self.journey_id)
            if budget.data.get("intake_attempt_id") != self.operation_id:
                raise journeys.unavailable()
            return budget
        return journeys.budget_for_artifact(
            transaction,
            self._project,
            self._binding["actor_id"],
            "test_cases",
            self._binding["source_snapshot_ids"]["test_cases"],
            expected_id=self.journey_id,
        )

    def prepare_publication(self, transaction, snapshot):
        """Read the exact claim, then return a callback that only stages writes."""
        attempt, lease, _ = self._validate(transaction)
        if not _snapshot_matches(snapshot, attempt):
            message = {
                _REVIEW_OPERATION: "The draft result does not match this saved suite and request.",
                REPAIR_OPERATION: "The draft result does not match this selected-case repair.",
                SCENARIO_REFRESH_OPERATION: "The draft result does not match this scoped scenario refresh.",
                SCOPED_IMPACT_OPERATION: "The result does not match this scoped impact application.",
            }.get(attempt["operation"], "The draft result does not match this Project Sync stage's saved sources and request.")
            raise _conflict("project_draft_result_mismatch", message)

        finish = self._prepare_finish(transaction, lease, {"result_snapshot_id": snapshot["snapshot_id"]})
        if self.journey_id is None:
            return finish
        budget = self._journey_budget(transaction)

        def finish_and_link():
            journeys.link_artifact(
                transaction, self._project, budget, "test_cases", snapshot["snapshot_id"], producer=(attempt["operation"], self.operation_id)
            )
            snapshot[journeys.BUDGET_FIELD] = budget.id
            finish()

        return finish_and_link

    def prepare_import_publication(self, transaction, preview):
        """Verify the same-revision preview before staging the audit and lease release."""
        attempt, lease, _ = self._validate(transaction)
        if attempt["operation"] not in REQUIREMENT_EXTRACTION_OPERATIONS or preview.get("status") != "pending" or not _import_matches(preview, attempt):
            raise _conflict("project_draft_result_mismatch", "The draft result does not match this requirement import.")
        finish = self._prepare_finish(transaction, lease, {"result_import_id": preview["import_id"]})
        if self.journey_id is None:
            return finish
        if (preview.get("extraction") or {}).get(journeys.BUDGET_FIELD) != self.journey_id:
            raise journeys.unavailable()
        budget = self._journey_budget(transaction)

        def finish_and_link():
            journeys.finish_preview(transaction, budget, self.operation_id, preview)
            finish()

        return finish_and_link

    def _prepare_finish(self, transaction, lease, result):
        def finish():
            now = _utcnow()
            if not _valid_until(lease.get("expires_at"), now):
                raise _conflict("project_draft_claim_lost", "This draft attempt expired before publication.")
            transaction.update(self._attempt, {"state": "completed", **result, "completed_at": now, "reason": None})
            transaction.set(self._lease, released_project_lease(self.fence))

        return finish

    def finish_on_commit(self, transaction, snapshot):
        """Commit the exact review receipt and release alongside its immutable result."""
        self.prepare_publication(transaction, snapshot)()

    def read_result(self):
        """Reconcile this exact attempt after an unconfirmed publication."""

        @transactional
        def read(transaction):
            _require_owner(_read(transaction, self._project), self._binding["project_id"], self._binding["actor_id"])
            attempt = _read(transaction, self._attempt)
            if _same_attempt(attempt, {**self._binding, "fence": self.fence}) and attempt.get("state") == "completed":
                return _read_result(transaction, self._project, attempt)
            return None

        return read(self._client.transaction())

    def stop(self):
        """Release known unstarted work and retain uncertain provider work."""

        @transactional
        def stop_attempt(transaction):
            attempt = _read(transaction, self._attempt)
            lease = _read(transaction, self._lease) or {}
            if (
                not _same_attempt(attempt, self._binding)
                or attempt.get("state") != "running"
                or attempt.get("fence") != self.fence
                or not _same_holder(lease, attempt)
            ):
                return False
            if lease.get("dispatched") or attempt.get("provider_requests_reserved"):
                transaction.update(self._attempt, {"state": "interrupted", "reason": "provider_outcome_unconfirmed"})
            else:
                transaction.update(self._attempt, {"state": "unstarted", "reason": None})
                transaction.set(self._lease, released_project_lease(self.fence))

            return True

        return stop_attempt(self._client.transaction())


def _sync_stage_doc(project_doc, change_id, stage):
    return project_doc.collection("project_changes").document(change_id).collection("draft_stages").document(stage)


def _sync_attempt_at(transaction, project_doc, operation_id, binding):
    if not _is_operation_id(operation_id):
        raise HTTPException(503, "The saved Project Sync stage could not be confirmed.")
    attempt = _read(transaction, _attempt_doc(project_doc, operation_id))
    identity = {key: binding[key] for key in ("project_id", "actor_id", "operation", "change_id")}
    if not _same_attempt(attempt, {**identity, "operation_id": operation_id}):
        raise HTTPException(503, "The saved Project Sync stage could not be confirmed.")
    return attempt


def _resolve_sync_attempt(transaction, project_doc, stage_doc, binding):
    """Find the stage's canonical attempt and remember aliases that actually acquired it."""
    requested_id = binding["operation_id"]
    pointer = _read(transaction, stage_doc)
    attempt = _read(transaction, _attempt_doc(project_doc, requested_id))
    if attempt is not None or pointer is None:
        return binding, attempt, None
    alias_doc = stage_doc.collection("request_aliases").document(requested_id)
    alias = _read(transaction, alias_doc)
    if alias is not None:
        attempt = _sync_attempt_at(transaction, project_doc, alias.get("operation_id"), binding)
        return {**binding, "operation_id": attempt["operation_id"], "request_id": attempt["request_id"]}, attempt, None
    attempt = _sync_attempt_at(transaction, project_doc, pointer.get("operation_id"), binding)
    if attempt.get("state") == "cancelled":
        return binding, None, None
    return {**binding, "operation_id": attempt["operation_id"], "request_id": attempt["request_id"]}, attempt, alias_doc


def verify_completed_sync_stage(transaction, project_doc, record, stage, *, actor_id, snapshot_id, result_revision):
    """Verify an immutable result against its canonical private and public stage receipts."""
    pointer = _read(transaction, _sync_stage_doc(project_doc, record["id"], stage)) or {}
    checkpoint = (record.get("stage_receipts") or {}).get(stage) or {}
    identity = {"project_id": project_doc.id, "actor_id": actor_id, "change_id": record["id"], "operation": f"project.sync.{stage}"}
    attempt = _sync_attempt_at(transaction, project_doc, pointer.get("operation_id"), identity)
    if (
        attempt.get("state") != "completed"
        or attempt["base_revision"] + 1 != result_revision
        or checkpoint.get("base_revision") != attempt["base_revision"]
        or checkpoint.get("result_revision") != result_revision
        or checkpoint.get("request_id") != attempt["request_id"]
        or checkpoint.get("settings_fingerprint") != attempt["settings_fingerprint"]
        or not _recorded_sync_sources_match(checkpoint.get("source_snapshot_ids"), attempt["source_snapshot_ids"])
        or checkpoint.get("snapshot_id") != attempt.get("result_snapshot_id")
        or checkpoint.get("snapshot_id") != snapshot_id
    ):
        raise HTTPException(503, "The saved Project Sync checkpoint could not be confirmed.")
    _read_result(transaction, project_doc, attempt)


def _verify_sync_predecessor(transaction, project_doc, project, binding):
    verify_completed_sync_stage(
        transaction,
        project_doc,
        project["project_sync"],
        "use_cases",
        actor_id=binding["actor_id"],
        snapshot_id=binding["source_snapshot_ids"]["use_cases"],
        result_revision=binding["base_revision"],
    )


def _valid_scenario_refresh(binding, previous_snapshot_id):
    if (
        not isinstance(binding, dict)
        or binding.keys() != {"scope", "previous_requirements_snapshot_id", "previous_review_id", "request_fingerprint"}
        or any(not isinstance(binding[key], str) or not binding[key] for key in ("previous_requirements_snapshot_id", "previous_review_id"))
        or not isinstance(binding["request_fingerprint"], str)
        or re.fullmatch(r"[0-9a-f]{64}", binding["request_fingerprint"]) is None
    ):
        return False
    scope = binding["scope"]
    if (
        not isinstance(scope, dict)
        or scope.keys() != _SCENARIO_REFRESH_SCOPE_FIELDS | {"previous_snapshot_id"}
        or scope["previous_snapshot_id"] != previous_snapshot_id
    ):
        return False
    selected = []
    for field in _SCENARIO_REFRESH_SCOPE_FIELDS:
        values = scope[field]
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            return False
        selected.extend(values)
    return bool(scope["refreshed_requirement_ids"] or scope["removed_requirement_ids"]) and len(selected) == len(set(selected))


def _valid_scoped_impact(binding):
    if (
        not isinstance(binding, dict)
        or binding.keys() != {"review_id", "request_fingerprint", "accepted_recommendation_ids", "case_versions"}
        or not isinstance(binding["review_id"], str)
        or not binding["review_id"]
        or not isinstance(binding["request_fingerprint"], str)
        or re.fullmatch(r"[0-9a-f]{64}", binding["request_fingerprint"]) is None
    ):
        return False
    selected = binding["accepted_recommendation_ids"]
    versions = binding["case_versions"]
    return (
        isinstance(selected, list)
        and all(isinstance(item, str) and item for item in selected)
        and selected == sorted(set(selected))
        and 1 <= len(selected) <= 6
        and isinstance(versions, dict)
        and len(versions) == len(selected)
        and all(
            isinstance(case_id, str)
            and case_id
            and isinstance(version, dict)
            and version.keys() == {"case_version_id", "case_version_number"}
            and isinstance(version["case_version_id"], str)
            and version["case_version_id"]
            and type(version["case_version_number"]) is int
            and version["case_version_number"] >= 1
            for case_id, version in versions.items()
        )
    )


def claim_draft_attempt(
    client,
    *,
    project_id,
    actor_id,
    request_id,
    operation,
    source_snapshot_ids,
    base_revision,
    max_requests,
    deadline_seconds,
    change_id=None,
    settings_fingerprint=None,
    selected_repair=None,
    scenario_refresh=None,
    scoped_impact=None,
    requirement_extraction=None,
):
    if not demo_client_matches(client):
        raise HTTPException(503, "Project draft admission is unavailable.")
    sync_stage = _SYNC_STAGES.get(operation)
    source_stages = _SYNC_SOURCE_STAGES if sync_stage else _SOURCE_STAGES
    optional_sources = {"context"}
    intake = operation in REQUIREMENT_EXTRACTION_OPERATIONS
    if intake:
        source_stages = optional_sources = {"requirements"}
    if operation == SCOPED_IMPACT_OPERATION:
        source_stages = _SOURCE_STAGES | {"impact_analysis"}
    if operation in {SCENARIO_REFRESH_OPERATION, SCOPED_IMPACT_OPERATION}:
        optional_sources = set()
    if sync_stage:
        optional_sources = {"test_cases", "reports"}
        if sync_stage == "use_cases":
            optional_sources.add("use_cases")
    if (
        operation not in _REQUEST_CAPS
        or not project_id
        or not actor_id
        or not isinstance(request_id, str)
        or not request_id.strip()
        or len(request_id) > 160
        or type(base_revision) is not int
        or base_revision < 1
        or type(max_requests) is not int
        or not 1 <= max_requests <= _REQUEST_CAPS[operation]
        or type(deadline_seconds) is not int
        or not 1 <= deadline_seconds <= 300
        or source_snapshot_ids.keys() != source_stages
        or any(
            not isinstance(source, str) or not source for stage, source in source_snapshot_ids.items() if stage not in optional_sources or source is not None
        )
    ):
        raise ValueError("Draft admission requires exact saved sources and bounded settings")
    if sync_stage:
        if (
            not isinstance(change_id, str)
            or re.fullmatch(r"change_[0-9a-f]{24}", change_id) is None
            or not isinstance(settings_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", settings_fingerprint) is None
        ):
            raise ValueError("Project Sync admission requires an exact change ID and settings fingerprint")
    elif operation == SCENARIO_REFRESH_OPERATION:
        if (
            change_id is not None
            or not isinstance(settings_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", settings_fingerprint) is None
            or not _valid_scenario_refresh(scenario_refresh, source_snapshot_ids["use_cases"])
        ):
            raise ValueError("Scenario refresh admission requires exact prior scope and settings")
    elif operation == SCOPED_IMPACT_OPERATION:
        if (
            change_id is not None
            or not isinstance(settings_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", settings_fingerprint) is None
            or not _valid_scoped_impact(scoped_impact)
        ):
            raise ValueError("Scoped impact admission requires an exact analysis, selection, case versions and settings")
    elif operation == REPAIR_OPERATION:
        if (
            change_id is not None
            or not isinstance(settings_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", settings_fingerprint) is None
            or not isinstance(selected_repair, dict)
            or selected_repair.keys() != _REPAIR_BINDING_FIELDS
            or any(not isinstance(selected_repair[key], str) or not selected_repair[key] for key in ("target_id", "case_id", "case_version_id"))
            or type(selected_repair["case_version_number"]) is not int
            or selected_repair["case_version_number"] < 1
            or not isinstance(selected_repair["request_fingerprint"], str)
            or re.fullmatch(r"[0-9a-f]{64}", selected_repair["request_fingerprint"]) is None
        ):
            raise ValueError("Repair admission requires an exact target and settings fingerprint")
    elif intake:
        if (
            change_id is not None
            or not isinstance(settings_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", settings_fingerprint) is None
            or not isinstance(requirement_extraction, dict)
            or requirement_extraction.keys() != {"import_id", "request_fingerprint"}
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in requirement_extraction.values())
        ):
            raise ValueError("Requirement extraction admission requires an exact preview and settings fingerprint")
    elif change_id is not None or settings_fingerprint is not None:
        raise ValueError("Saved-suite assessment does not accept Project Sync settings")
    if operation != REPAIR_OPERATION and selected_repair is not None:
        raise ValueError("Only selected repair accepts a repair target")
    if operation != SCENARIO_REFRESH_OPERATION and scenario_refresh is not None:
        raise ValueError("Only a scoped scenario refresh accepts previous plan scope")
    if operation != SCOPED_IMPACT_OPERATION and scoped_impact is not None:
        raise ValueError("Only scoped impact accepts an impact selection")
    if not intake and requirement_extraction is not None:
        raise ValueError("Only requirement extraction accepts an import preview binding")
    binding = {
        "operation_id": _operation_id(project_id, actor_id, operation, request_id, change_id),
        "operation": operation,
        "project_id": project_id,
        "actor_id": actor_id,
        "request_id": request_id,
        "base_revision": base_revision,
        "source_snapshot_ids": dict(source_snapshot_ids),
    }
    if sync_stage:
        binding.update(change_id=change_id, settings_fingerprint=settings_fingerprint, max_requests=max_requests, deadline_seconds=deadline_seconds)
    if operation == REPAIR_OPERATION:
        binding.update(
            selected_repair=dict(selected_repair), settings_fingerprint=settings_fingerprint, max_requests=max_requests, deadline_seconds=deadline_seconds
        )
    if operation == SCENARIO_REFRESH_OPERATION:
        binding.update(
            scenario_refresh=scenario_refresh, settings_fingerprint=settings_fingerprint, max_requests=max_requests, deadline_seconds=deadline_seconds
        )
    if operation == SCOPED_IMPACT_OPERATION:
        binding.update(scoped_impact=scoped_impact, settings_fingerprint=settings_fingerprint, max_requests=max_requests, deadline_seconds=deadline_seconds)
    if intake:
        binding.update(
            requirement_extraction=dict(requirement_extraction),
            settings_fingerprint=settings_fingerprint,
            max_requests=max_requests,
            deadline_seconds=deadline_seconds,
        )
    project_doc = _project_doc(client, project_id)
    stage_doc = _sync_stage_doc(project_doc, change_id, sync_stage) if sync_stage else None
    lease_doc = project_lease_ref(project_doc)

    @transactional
    def claim(transaction):
        project = _read(transaction, project_doc)
        _require_owner(project, project_id, actor_id)
        canonical, alias_doc = binding, None
        if stage_doc is not None:
            canonical, attempt, alias_doc = _resolve_sync_attempt(transaction, project_doc, stage_doc, binding)
        else:
            attempt = _read(transaction, _attempt_doc(project_doc, binding["operation_id"]))
        saved_journey_id = (attempt or {}).get(journeys.BUDGET_FIELD)
        if saved_journey_id is not None:
            if operation not in {journeys.INTAKE_STAGE, journeys.ASSESSMENT_STAGE, journeys.REPAIR_STAGE}:
                raise journeys.unavailable()
            canonical = {**canonical, journeys.BUDGET_FIELD: saved_journey_id}
        if attempt is not None:
            if not _same_attempt(attempt, canonical):
                target = {
                    REPAIR_OPERATION: "case repair",
                    _REVIEW_OPERATION: "draft assessment",
                    SCENARIO_REFRESH_OPERATION: "scenario refresh",
                    SCOPED_IMPACT_OPERATION: "scoped impact application",
                    **dict.fromkeys(REQUIREMENT_EXTRACTION_OPERATIONS, "requirement import"),
                }.get(operation, "Project Sync draft")
                raise _conflict("project_draft_request_mismatch", f"This request ID belongs to a different {target}.")
            if attempt.get("state") == "completed":
                result = _read_result(transaction, project_doc, attempt)
                if intake:
                    return DraftAttemptClaim(client, canonical, attempt["fence"], result_import_id=result)
                return DraftAttemptClaim(client, canonical, attempt["fence"], result)
            if intake and attempt.get("state") == "running":
                current_lease = _read(transaction, lease_doc) or {}
                if _same_holder(current_lease, attempt) and _projected_state(project, attempt, current_lease, _utcnow())[0] == "running":
                    raise _conflict("project_draft_busy", "This requirement extraction request is already running. Check its current status.")
            if attempt.get("state") in ("interrupted", "cancelled") or attempt.get("provider_requests_reserved"):
                raise _conflict(
                    "project_draft_recovery_required", "This request was interrupted or cancelled. Check its saved status before starting new work."
                )
        lease = _read(transaction, lease_doc) or {}
        now = _utcnow()
        if not _matches_sources(project, canonical):
            message = "Project sources changed. Reload the current project draft."
            if intake:
                message = "Project requirements changed. Reload the current project before extracting requirements."
            elif not sync_stage:
                message = "Project sources changed. Reload the saved suite before reviewing it."
            raise _conflict("project_draft_sources_changed", message)
        if lease_blocks_claim(lease, now):
            raise _conflict("project_draft_busy", _BUSY)
        if sync_stage == "test_cases":
            _verify_sync_predecessor(transaction, project_doc, project, canonical)
        create_budget = None
        if operation == journeys.INTAKE_STAGE and (attempt is None or saved_journey_id is not None):
            journey_id = journeys.intake_journey_id(canonical["operation_id"])
            if saved_journey_id is not None and saved_journey_id != journey_id:
                raise journeys.unavailable()
            create_budget = journeys.prepare_intake_budget(
                transaction, project_doc, actor_id, canonical["operation_id"], requirement_extraction["import_id"], base_revision
            )
            canonical = {**canonical, journeys.BUDGET_FIELD: journey_id}
        if operation in {journeys.ASSESSMENT_STAGE, journeys.REPAIR_STAGE}:
            budget = journeys.budget_for_artifact(
                transaction, project_doc, actor_id, "test_cases", source_snapshot_ids["test_cases"], expected_id=saved_journey_id
            )
            if budget is not None:
                if not journeys.available(budget, operation, max_requests):
                    raise journeys.exhausted()
                canonical = {**canonical, journeys.BUDGET_FIELD: budget.id}
        fence = lease.get("fence", 0) + 1
        deadline_at = now + timedelta(seconds=deadline_seconds)
        operation_id = canonical["operation_id"]
        transaction.set(
            lease_doc,
            {
                "kind": _KIND,
                "run_id": operation_id,
                "fence": fence,
                "expires_at": min(now + timedelta(seconds=_LEASE_SECONDS), deadline_at),
                "dispatched": False,
            },
        )
        transaction.set(
            _attempt_doc(project_doc, operation_id),
            {
                **canonical,
                "schema_version": 1,
                "state": "running",
                "fence": fence,
                "max_requests": max_requests,
                "deadline_at": deadline_at,
                "provider_requests_reserved": 0,
                "started_at": now,
            },
        )
        if create_budget is not None:
            create_budget()
        if stage_doc is not None:
            transaction.set(stage_doc, {"operation_id": operation_id})
        if alias_doc is not None:
            transaction.create(alias_doc, {"operation_id": operation_id})
        return DraftAttemptClaim(client, canonical, fence)

    try:
        return claim(client.transaction())
    except Aborted as exc:
        raise _conflict("project_draft_busy", _BUSY) from exc
    except ValueError as exc:
        # Firestore wraps exhausted explicit contention, while transport failure remains unknown.
        if isinstance(exc.__cause__, Aborted):
            raise _conflict("project_draft_busy", _BUSY) from exc
        raise


def read_draft_attempt_result(client, *, project_id, actor_id, request_id, operation, source_snapshot_id, base_revision):
    if operation != _REVIEW_OPERATION:
        raise ValueError("Unsupported draft admission operation")
    operation_id = _operation_id(project_id, actor_id, operation, request_id)
    project_doc = _project_doc(client, project_id)

    @transactional
    def read(transaction):
        _require_owner(_read(transaction, project_doc), project_id, actor_id)
        attempt = _read(transaction, _attempt_doc(project_doc, operation_id))
        identity = {"project_id": project_id, "actor_id": actor_id, "operation_id": operation_id, "request_id": request_id, "operation": operation}
        if attempt is None:
            return None
        if (
            not _same_attempt(attempt, identity)
            or attempt.get("base_revision") != base_revision
            or (attempt.get("source_snapshot_ids") or {}).get("test_cases") != source_snapshot_id
        ):
            raise _conflict("project_draft_request_mismatch", "This request ID belongs to a different draft assessment.")
        if attempt.get("state") == "completed":
            return _read_result(transaction, project_doc, attempt)
        return None

    return read(client.transaction())


def read_selected_repair_result(client, *, project_id, actor_id, request_id, request_fingerprint=None):
    return _read_foreground_result(client, project_id, actor_id, request_id, REPAIR_OPERATION, "selected_repair", request_fingerprint)


def read_scenario_refresh_result(client, *, project_id, actor_id, request_id, request_fingerprint=None):
    return _read_foreground_result(client, project_id, actor_id, request_id, SCENARIO_REFRESH_OPERATION, "scenario_refresh", request_fingerprint)


def read_scoped_impact_result(client, *, project_id, actor_id, request_id, request_fingerprint=None):
    return _read_foreground_result(client, project_id, actor_id, request_id, SCOPED_IMPACT_OPERATION, "scoped_impact", request_fingerprint)


def _read_foreground_result(client, project_id, actor_id, request_id, operation, intent_field, request_fingerprint):
    """Read the exact immutable result, independently of current project revision or demo policy."""
    operation_id = _operation_id(project_id, actor_id, operation, request_id)
    project_doc = _project_doc(client, project_id)

    @transactional
    def read(transaction):
        _require_owner(_read(transaction, project_doc), project_id, actor_id)
        attempt = _read(transaction, _attempt_doc(project_doc, operation_id))
        identity = {"project_id": project_id, "actor_id": actor_id, "operation_id": operation_id, "request_id": request_id, "operation": operation}
        if not _same_attempt(attempt, identity):
            return None
        if request_fingerprint is not None and attempt[intent_field]["request_fingerprint"] != request_fingerprint:
            target = {REPAIR_OPERATION: "repair target", SCENARIO_REFRESH_OPERATION: "scenario refresh", SCOPED_IMPACT_OPERATION: "impact selection"}[operation]
            raise _conflict("project_draft_request_mismatch", f"This request ID belongs to a different {target}.")
        if attempt.get("state") == "completed":
            return _read_result(transaction, project_doc, attempt, include_snapshot=True)
        return None

    return read(client.transaction())


def _read_active_attempt(client, project_id, actor_id, *, operations):
    project_doc = _project_doc(client, project_id)

    @transactional
    def read(transaction):
        project = _read(transaction, project_doc)
        _require_owner(project, project_id, actor_id)
        lease = _read(transaction, project_lease_ref(project_doc)) or {}
        now = _utcnow()
        blocked = lease_blocks_claim(lease, now)
        operation_id = lease.get("run_id")
        if lease.get("kind") != _KIND or not _is_operation_id(operation_id):
            return blocked, None
        attempt = _read(transaction, _attempt_doc(project_doc, operation_id))
        identity = {"project_id": project_id, "actor_id": actor_id, "operation_id": operation_id}
        if not _same_attempt(attempt, identity) or not _same_holder(lease, attempt):
            return blocked, None
        sync_stage = _SYNC_STAGES.get(attempt.get("operation"))
        if attempt.get("operation") not in operations:
            return blocked, None
        state, reason = _projected_state(project, attempt, lease, now)
        if not state:
            return blocked, None
        public = {
            "operation_id": operation_id,
            "fence": attempt["fence"],
            "operation": attempt["operation"],
            "request_id": attempt["request_id"],
            "state": state,
            "reason": reason,
        }
        if sync_stage:
            public.update(change_id=attempt["change_id"], stage=sync_stage)
        else:
            stage = _OPERATION_SOURCE_STAGE.get(attempt["operation"], "test_cases")
            public["source_snapshot_id"] = attempt["source_snapshot_ids"][stage]
        if attempt["operation"] == SCOPED_IMPACT_OPERATION:
            public.update(
                analysis_snapshot_id=attempt["source_snapshot_ids"]["impact_analysis"],
                accepted_recommendation_ids=attempt["scoped_impact"]["accepted_recommendation_ids"],
            )
        if attempt["operation"] == REPAIR_OPERATION:
            public["case_id"] = attempt["selected_repair"]["case_id"]
        return blocked, public

    return read(client.transaction())


def read_draft_attempt(client, project_id, actor_id):
    _, attempt = _read_active_attempt(client, project_id, actor_id, operations={_REVIEW_OPERATION})
    return attempt


def read_project_sync_admission(client, project_id, actor_id):
    blocked, attempt = _read_active_attempt(client, project_id, actor_id, operations=_SYNC_STAGES)
    return {"blocked": blocked, "attempt": attempt}


def read_selected_repair_admission(client, project_id, actor_id):
    blocked, attempt = _read_active_attempt(client, project_id, actor_id, operations={REPAIR_OPERATION})
    return {"blocked": blocked, "attempt": attempt}


def read_scenario_refresh_admission(client, project_id, actor_id):
    blocked, attempt = _read_active_attempt(client, project_id, actor_id, operations={SCENARIO_REFRESH_OPERATION})
    return {"blocked": blocked, "attempt": attempt}


def read_scoped_impact_admission(client, project_id, actor_id):
    blocked, attempt = _read_active_attempt(client, project_id, actor_id, operations={SCOPED_IMPACT_OPERATION})
    return {"blocked": blocked, "attempt": attempt}


def read_requirement_extraction_admission(client, project_id, actor_id):
    blocked, attempt = _read_active_attempt(client, project_id, actor_id, operations=REQUIREMENT_EXTRACTION_OPERATIONS)
    return {"blocked": blocked, "attempt": attempt}


def release_draft_attempt(
    client,
    project_id,
    actor_id,
    operation_id,
    fence,
    *,
    project_sync=False,
    selected_repair=False,
    scenario_refresh=False,
    scoped_impact=False,
    requirement_extraction=False,
):
    if sum((project_sync, selected_repair, scenario_refresh, scoped_impact, requirement_extraction)) > 1:
        raise ValueError("Choose one draft admission operation family")
    if not _is_operation_id(operation_id) or type(fence) is not int or fence < 1:
        raise _conflict("project_draft_release_mismatch", "This draft attempt identity is unavailable. Reload its current status.")
    project_doc = _project_doc(client, project_id)
    lease_doc = project_lease_ref(project_doc)
    attempt_doc = _attempt_doc(project_doc, operation_id)

    @transactional
    def release(transaction):
        project = _read(transaction, project_doc)
        _require_owner(project, project_id, actor_id)
        attempt = _read(transaction, attempt_doc)
        identity = {
            "project_id": project_id,
            "actor_id": actor_id,
            "operation_id": operation_id,
            "fence": fence,
        }
        allowed_operations = _SYNC_STAGES if project_sync else {_REVIEW_OPERATION}
        if selected_repair:
            allowed_operations = {REPAIR_OPERATION}
        if scenario_refresh:
            allowed_operations = {SCENARIO_REFRESH_OPERATION}
        if scoped_impact:
            allowed_operations = {SCOPED_IMPACT_OPERATION}
        if requirement_extraction:
            allowed_operations = REQUIREMENT_EXTRACTION_OPERATIONS
        if not _same_attempt(attempt, identity) or attempt.get("operation") not in allowed_operations:
            raise _conflict("project_draft_release_mismatch", "This draft attempt changed. Reload its current status.")
        if attempt.get("state") == "cancelled":
            return True
        lease = _read(transaction, lease_doc) or {}
        if attempt.get("state") not in ("running", "interrupted") or not _same_holder(lease, attempt):
            raise _conflict("project_draft_release_mismatch", "This draft attempt changed. Reload its current status.")
        now = _utcnow()
        state, _ = _projected_state(project, attempt, lease, now)
        if requirement_extraction and state != "interrupted":
            raise _conflict("project_draft_release_mismatch", "This requirement extraction is still running. Check its current status.")
        reason = "provider_outcome_acknowledged" if state == "interrupted" else "cancelled_by_owner"
        transaction.update(attempt_doc, {"state": "cancelled", "reason": reason, "completed_at": now})
        transaction.set(lease_doc, released_project_lease(fence))
        return True

    return release(client.transaction())
