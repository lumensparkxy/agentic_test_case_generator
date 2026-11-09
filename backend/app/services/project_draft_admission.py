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
from .approval_continuation import lease_blocks_claim, lease_matches, project_lease_ref, released_project_lease
from .firestore_repository import get_required_firestore_client
from .guidance_service import enabled
from .workflow_project_service import QA_PROJECTS_COLLECTION, ProjectNotFoundError, ProjectPermissionError

DEMO_PROJECT_ID = "demo-tcg-v2-continuation"
_REVIEW_OPERATION = "test_cases.review"
_SOURCE_STAGES = {"requirements", "context", "use_cases", "test_cases"}
_SYNC_SOURCE_STAGES = _SOURCE_STAGES | {"reports"}
_SYNC_STAGES = {"project.sync.use_cases": "use_cases", "project.sync.test_cases": "test_cases"}
_REQUEST_CAPS = {_REVIEW_OPERATION: 24, "project.sync.use_cases": 7, "project.sync.test_cases": 24}
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


def draft_refresh_deferred():
    return _conflict("project_draft_refresh_deferred", "Paid draft refresh is unavailable until it uses shared project admission.")


def _utcnow():
    return datetime.now(timezone.utc)


def _project_doc(client, project_id):
    return client.collection(QA_PROJECTS_COLLECTION).document(project_id)


def _attempt_doc(project_doc, operation_id):
    return project_doc.collection(_ATTEMPTS).document(operation_id)


def _operation_id(project_id, actor_id, operation, request_id, change_id=None):
    identity = [project_id, actor_id, operation, request_id]
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
    merge_inputs = {"test_cases", "reports"} if stage_to_refresh == "test_cases" else {"use_cases", "test_cases", "reports"}
    for stage, snapshot_id in attempt["source_snapshot_ids"].items():
        state = states.get(stage) or {}
        may_review_stale_suite = stage == "test_cases" and state.get("operation") == "quality.feedback.apply"
        may_merge_stale = stage_to_refresh and stage in merge_inputs
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


def _read_result(transaction, project_doc, attempt):
    unavailable = (
        "The saved assessment result could not be confirmed."
        if attempt["operation"] == _REVIEW_OPERATION
        else "The saved Project Sync draft could not be confirmed."
    )
    snapshot_id = attempt.get("result_snapshot_id")
    if not snapshot_id:
        raise HTTPException(503, unavailable)
    snapshot = _read(
        transaction,
        project_doc.collection("snapshots").document(snapshot_id),
        field_paths=("snapshot_id", "project_id", "actor_user_id", "request_id", "stage", "operation", "project_revision", "source_snapshot_id", "metadata"),
    )
    if not _snapshot_matches(snapshot, attempt) or snapshot["snapshot_id"] != snapshot_id:
        raise HTTPException(503, unavailable)
    return snapshot_id


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

    def __init__(self, client, binding, fence, result_snapshot_id=None):
        self._client = client
        self._binding = binding
        self.operation_id = binding["operation_id"]
        self.request_id = binding["request_id"]
        self.fence = fence
        self.result_snapshot_id = result_snapshot_id
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
        guidance_stage = _SYNC_STAGES.get(self._binding["operation"], "test_cases")
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

    def prepare_publication(self, transaction, snapshot):
        """Read the exact claim, then return a callback that only stages writes."""
        attempt, lease, _ = self._validate(transaction)
        if not _snapshot_matches(snapshot, attempt):
            message = "The draft result does not match this saved suite and request."
            if attempt["operation"] != _REVIEW_OPERATION:
                message = "The draft result does not match this Project Sync stage's saved sources and request."
            raise _conflict("project_draft_result_mismatch", message)

        def finish():
            now = _utcnow()
            if not _valid_until(lease.get("expires_at"), now):
                raise _conflict("project_draft_claim_lost", "This draft attempt expired before publication.")
            transaction.update(
                self._attempt,
                {"state": "completed", "result_snapshot_id": snapshot["snapshot_id"], "completed_at": now, "reason": None},
            )
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
                return
            if lease.get("dispatched") or attempt.get("provider_requests_reserved"):
                transaction.update(self._attempt, {"state": "interrupted", "reason": "provider_outcome_unconfirmed"})
            else:
                transaction.update(self._attempt, {"state": "unstarted", "reason": None})
                transaction.set(self._lease, released_project_lease(self.fence))

        stop_attempt(self._client.transaction())


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


def _verify_sync_predecessor(transaction, project_doc, project, binding):
    stage_doc = _sync_stage_doc(project_doc, binding["change_id"], "use_cases")
    pointer = _read(transaction, stage_doc) or {}
    checkpoint = (project["project_sync"].get("stage_receipts") or {}).get("use_cases") or {}
    attempt = _sync_attempt_at(transaction, project_doc, pointer.get("operation_id"), {**binding, "operation": "project.sync.use_cases"})
    if (
        attempt.get("state") != "completed"
        or attempt["base_revision"] + 1 != binding["base_revision"]
        or checkpoint.get("base_revision") != attempt["base_revision"]
        or checkpoint.get("result_revision") != binding["base_revision"]
        or checkpoint.get("request_id") != attempt["request_id"]
        or checkpoint.get("settings_fingerprint") != attempt["settings_fingerprint"]
        or not _recorded_sync_sources_match(checkpoint.get("source_snapshot_ids"), attempt["source_snapshot_ids"])
        or checkpoint.get("snapshot_id") != attempt.get("result_snapshot_id")
        or checkpoint.get("snapshot_id") != binding["source_snapshot_ids"]["use_cases"]
    ):
        raise HTTPException(503, "The saved Project Sync scenario checkpoint could not be confirmed.")
    _read_result(transaction, project_doc, attempt)


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
):
    if not demo_client_matches(client):
        raise HTTPException(503, "Project draft admission is unavailable.")
    sync_stage = _SYNC_STAGES.get(operation)
    source_stages = _SYNC_SOURCE_STAGES if sync_stage else _SOURCE_STAGES
    optional_sources = {"context"}
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
    elif change_id is not None or settings_fingerprint is not None:
        raise ValueError("Saved-suite assessment does not accept Project Sync settings")
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
        if attempt is not None:
            if not _same_attempt(attempt, canonical):
                target = "Project Sync draft" if sync_stage else "draft assessment"
                raise _conflict("project_draft_request_mismatch", f"This request ID belongs to a different {target}.")
            if attempt.get("state") == "completed":
                return DraftAttemptClaim(client, canonical, attempt["fence"], _read_result(transaction, project_doc, attempt))
            if attempt.get("state") in ("interrupted", "cancelled") or attempt.get("provider_requests_reserved"):
                raise _conflict(
                    "project_draft_recovery_required", "This request was interrupted or cancelled. Check its saved status before starting new work."
                )
        lease = _read(transaction, lease_doc) or {}
        now = _utcnow()
        if not _matches_sources(project, canonical):
            message = "Project sources changed. Reload the current project draft."
            if not sync_stage:
                message = "Project sources changed. Reload the saved suite before reviewing it."
            raise _conflict("project_draft_sources_changed", message)
        if lease_blocks_claim(lease, now):
            raise _conflict("project_draft_busy", _BUSY)
        if sync_stage == "test_cases":
            _verify_sync_predecessor(transaction, project_doc, project, canonical)
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


def _read_active_attempt(client, project_id, actor_id, *, project_sync):
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
        if (project_sync and not sync_stage) or (not project_sync and attempt.get("operation") != _REVIEW_OPERATION):
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
            public["source_snapshot_id"] = attempt["source_snapshot_ids"]["test_cases"]
        return blocked, public

    return read(client.transaction())


def read_draft_attempt(client, project_id, actor_id):
    _, attempt = _read_active_attempt(client, project_id, actor_id, project_sync=False)
    return attempt


def read_project_sync_admission(client, project_id, actor_id):
    blocked, attempt = _read_active_attempt(client, project_id, actor_id, project_sync=True)
    return {"blocked": blocked, "attempt": attempt}


def release_draft_attempt(client, project_id, actor_id, operation_id, fence, *, project_sync=False):
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
        if not _same_attempt(attempt, identity) or attempt.get("operation") not in allowed_operations:
            raise _conflict("project_draft_release_mismatch", "This draft attempt changed. Reload its current status.")
        if attempt.get("state") == "cancelled":
            return True
        lease = _read(transaction, lease_doc) or {}
        if attempt.get("state") not in ("running", "interrupted") or not _same_holder(lease, attempt):
            raise _conflict("project_draft_release_mismatch", "This draft attempt changed. Reload its current status.")
        now = _utcnow()
        state, _ = _projected_state(project, attempt, lease, now)
        reason = "provider_outcome_acknowledged" if state == "interrupted" else "cancelled_by_owner"
        transaction.update(attempt_doc, {"state": "cancelled", "reason": reason, "completed_at": now})
        transaction.set(lease_doc, released_project_lease(fence))
        return True

    return release(client.transaction())
