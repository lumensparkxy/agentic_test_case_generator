"""Share the project draft provider fence with exact saved-suite assessment."""

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


def _operation_id(project_id, actor_id, operation, request_id):
    fingerprint = sha256(json.dumps([project_id, actor_id, operation, request_id], separators=(",", ":")).encode()).hexdigest()
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
    states = project.get("stage_state") or {}
    for stage, snapshot_id in attempt["source_snapshot_ids"].items():
        state = states.get(stage) or {}
        may_review_stale_suite = stage == "test_cases" and state.get("operation") == "quality.feedback.apply"
        if state.get("current_snapshot_id") != snapshot_id or (state.get("stale") and not may_review_stale_suite):
            return False
    return True


def _same_attempt(attempt, binding):
    return attempt and attempt.get("schema_version") == 1 and all(attempt.get(key) == value for key, value in binding.items())


def _same_holder(lease, attempt):
    return lease_matches(lease, kind=_KIND, operation_id=attempt["operation_id"], fence=attempt["fence"])


def _valid_until(expires_at, now):
    return isinstance(expires_at, datetime) and expires_at.tzinfo is not None and expires_at > now


def _snapshot_matches(snapshot, attempt):
    sources = attempt["source_snapshot_ids"]
    metadata = (snapshot or {}).get("metadata") or {}
    upstream = {stage: sources[stage] for stage in ("requirements", "context", "use_cases") if sources[stage] is not None}
    return bool(
        snapshot
        and snapshot.get("snapshot_id")
        and snapshot.get("project_id") == attempt["project_id"]
        and snapshot.get("actor_user_id") == attempt["actor_id"]
        and snapshot.get("request_id") == attempt["request_id"]
        and snapshot.get("stage") == "test_cases"
        and snapshot.get("operation") == attempt["operation"]
        and snapshot.get("project_revision") == attempt["base_revision"] + 1
        and snapshot.get("source_snapshot_id") == sources["test_cases"]
        and metadata.get("source_snapshot_ids") == upstream
    )


def _read_result(transaction, project_doc, attempt):
    snapshot_id = attempt.get("result_snapshot_id")
    if not snapshot_id:
        raise HTTPException(503, "The saved assessment result could not be confirmed.")
    snapshot = _read(
        transaction,
        project_doc.collection("snapshots").document(snapshot_id),
        field_paths=("snapshot_id", "project_id", "actor_user_id", "request_id", "stage", "operation", "project_revision", "source_snapshot_id", "metadata"),
    )
    if not _snapshot_matches(snapshot, attempt) or snapshot["snapshot_id"] != snapshot_id:
        raise HTTPException(503, "The saved assessment result could not be confirmed.")
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
        with self._lock:
            if (
                self._reservation_failed
                or not demo_provider_allowed()
                or not demo_client_matches(self._client)
                or enabled("memory", "test_cases")
                or enabled("skills", "test_cases")
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

    def finish_on_commit(self, transaction, snapshot):
        """Commit the exact review receipt and release alongside its immutable result."""
        attempt, _, now = self._validate(transaction)
        if not _snapshot_matches(snapshot, attempt):
            raise _conflict("project_draft_result_mismatch", "The draft result does not match this saved suite and request.")
        transaction.update(
            self._attempt,
            {"state": "completed", "result_snapshot_id": snapshot["snapshot_id"], "completed_at": now, "reason": None},
        )
        transaction.set(self._lease, released_project_lease(self.fence))

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


def claim_draft_attempt(client, *, project_id, actor_id, request_id, operation, source_snapshot_ids, base_revision, max_requests, deadline_seconds):
    if not demo_client_matches(client):
        raise HTTPException(503, "Project draft admission is unavailable.")
    if (
        operation != _REVIEW_OPERATION
        or not project_id
        or not actor_id
        or not request_id.strip()
        or len(request_id) > 160
        or type(base_revision) is not int
        or base_revision < 1
        or type(max_requests) is not int
        or not 1 <= max_requests <= 24
        or type(deadline_seconds) is not int
        or not 1 <= deadline_seconds <= 300
        or source_snapshot_ids.keys() != _SOURCE_STAGES
        or any(not isinstance(source, str) or not source for stage, source in source_snapshot_ids.items() if stage != "context" or source is not None)
    ):
        raise ValueError("Draft admission requires an exact saved suite and bounded review settings")
    operation_id = _operation_id(project_id, actor_id, operation, request_id)
    binding = {
        "operation_id": operation_id,
        "operation": operation,
        "project_id": project_id,
        "actor_id": actor_id,
        "request_id": request_id,
        "base_revision": base_revision,
        "source_snapshot_ids": dict(source_snapshot_ids),
    }
    project_doc = _project_doc(client, project_id)
    attempt_doc = _attempt_doc(project_doc, operation_id)
    lease_doc = project_lease_ref(project_doc)

    @transactional
    def claim(transaction):
        project = _read(transaction, project_doc)
        _require_owner(project, project_id, actor_id)
        attempt = _read(transaction, attempt_doc)
        if attempt is not None:
            if not _same_attempt(attempt, binding):
                raise _conflict("project_draft_request_mismatch", "This request ID belongs to a different draft assessment.")
            if attempt.get("state") == "completed":
                return DraftAttemptClaim(client, binding, attempt["fence"], _read_result(transaction, project_doc, attempt))
            if attempt.get("state") in ("interrupted", "cancelled") or attempt.get("provider_requests_reserved"):
                raise _conflict(
                    "project_draft_recovery_required", "This request was interrupted or cancelled. Check its saved status before starting new work."
                )
        lease = _read(transaction, lease_doc) or {}
        now = _utcnow()
        if not _matches_sources(project, binding):
            raise _conflict("project_draft_sources_changed", "Project sources changed. Reload the saved suite before reviewing it.")
        if lease_blocks_claim(lease, now):
            raise _conflict("project_draft_busy", _BUSY)
        fence = lease.get("fence", 0) + 1
        deadline_at = now + timedelta(seconds=deadline_seconds)
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
            attempt_doc,
            {
                **binding,
                "schema_version": 1,
                "state": "running",
                "fence": fence,
                "max_requests": max_requests,
                "deadline_at": deadline_at,
                "provider_requests_reserved": 0,
                "started_at": now,
            },
        )
        return DraftAttemptClaim(client, binding, fence)

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


def read_draft_attempt(client, project_id, actor_id):
    project_doc = _project_doc(client, project_id)

    @transactional
    def read(transaction):
        project = _read(transaction, project_doc)
        _require_owner(project, project_id, actor_id)
        lease = _read(transaction, project_lease_ref(project_doc)) or {}
        operation_id = lease.get("run_id")
        if lease.get("kind") != _KIND or not _is_operation_id(operation_id):
            return None
        attempt = _read(transaction, _attempt_doc(project_doc, operation_id))
        identity = {"project_id": project_id, "actor_id": actor_id, "operation_id": operation_id, "operation": _REVIEW_OPERATION}
        if not _same_attempt(attempt, identity) or not _same_holder(lease, attempt):
            return None
        state, reason = _projected_state(project, attempt, lease, _utcnow())
        if state is None:
            return None
        return {
            "operation_id": operation_id,
            "fence": attempt["fence"],
            "operation": attempt["operation"],
            "request_id": attempt["request_id"],
            "state": state,
            "reason": reason,
            "source_snapshot_id": attempt["source_snapshot_ids"]["test_cases"],
        }

    return read(client.transaction())


def release_draft_attempt(client, project_id, actor_id, operation_id, fence):
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
            "operation": _REVIEW_OPERATION,
            "fence": fence,
        }
        if not _same_attempt(attempt, identity):
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
