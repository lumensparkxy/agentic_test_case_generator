"""Durable approval intent and a fenced, single-flight project generation claim."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal

from google.api_core.exceptions import Aborted
from google.cloud.firestore_v1 import transactional
from pydantic import BaseModel, Field

from ..contracts.test_cases import TestCaseTemplate
from .workflow_project_service import QA_PROJECTS_COLLECTION, ProjectNotFoundError, ProjectPermissionError


ContinuationStage = Literal["prepare_scenarios", "generate_tests"]
OUTBOX_COLLECTION = "approval_continuation_outbox"
_STAGES = {"prepare_scenarios": "use_cases", "generate_tests": "test_cases"}
_OPERATIONS = {"use_cases": "use_cases.generate", "test_cases": "testcases.generate"}
_LEGACY_STATUS = {
    "queued": "blocked",
    "running": "running",
    "awaiting_user": "blocked",
    "completed": "completed",
    "failed": "failed",
    "interrupted": "blocked",
    "cancelled": "cancelled",
    "superseded": "failed",
}


class ContinuationOptions(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    settings_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_attempts: int = Field(default=1, ge=1, le=3)
    max_provider_requests: int = Field(default=1, ge=1, le=128)
    deadline_seconds: int = Field(default=900, ge=1, le=1800)
    template: TestCaseTemplate | None = Field(default=None, exclude_if=lambda value: value is None)


class ContinuationConflict(RuntimeError):
    """The worker no longer has authority to invoke a provider or publish."""


@dataclass(frozen=True)
class ContinuationClaim:
    project_id: str
    run_id: str
    fence: int
    stage_request_id: str


@dataclass(frozen=True)
class _Documents:
    project: object
    run: object
    outbox: object
    lease: object


def _utcnow():
    return datetime.now(timezone.utc)


def _read(transaction, document):
    return document.get(transaction=transaction).to_dict()


def _digest(values):
    return sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def continuation_run_id(project_id, actor_id, stage, approval_id):
    return f"continuation_{_digest([project_id, actor_id, stage, approval_id])}"


def project_lease_ref(project_doc):
    return project_doc.collection("approval_continuation_state").document("lease")


def lease_blocks_claim(lease, now):
    if not lease.get("run_id"):
        return lease.get("dispatched") is True
    expires_at = lease.get("expires_at")
    return lease.get("dispatched") is not False or not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at > now


def lease_matches(lease, *, kind, operation_id, fence=None):
    return lease.get("kind", "continuation") == kind and lease.get("run_id") == operation_id and (fence is None or lease.get("fence") == fence)


def released_project_lease(fence):
    return {"run_id": None, "fence": fence, "expires_at": None, "dispatched": False}


def _references(client, project_doc, run_id):
    return _Documents(
        project=project_doc,
        run=project_doc.collection("orchestrator_runs").document(run_id),
        outbox=client.collection(OUTBOX_COLLECTION).document(run_id),
        lease=project_lease_ref(project_doc),
    )


def _documents(client, project_id, run_id):
    return _references(client, client.collection(QA_PROJECTS_COLLECTION).document(project_id), run_id)


def enqueue_approval(
    transaction,
    project_doc,
    *,
    client,
    stage: ContinuationStage,
    actor_id: str,
    approval_id: str,
    request_id: str,
    resulting_revision: int,
    source_snapshot_ids: dict[str, str | None],
    requirement_ids: list[str],
    options: ContinuationOptions,
) -> str:
    """Stage writes only. The caller verifies the approval and replays its receipt."""
    artifact_stage = _STAGES.get(stage)
    if artifact_stage is None:
        raise ValueError("Unsupported approval continuation stage")
    expected_sources = {"requirements", "context", "use_cases"} if stage == "generate_tests" else {"requirements", "context"}
    if (
        source_snapshot_ids.keys() != expected_sources
        or not source_snapshot_ids.get("requirements")
        or (stage == "generate_tests" and not source_snapshot_ids.get("use_cases"))
        or (stage == "prepare_scenarios" and source_snapshot_ids["requirements"] != approval_id)
        or not requirement_ids
        or len(requirement_ids) != len(set(requirement_ids))
        or any(not item for item in requirement_ids)
        or not actor_id
        or not approval_id
        or not request_id
        or resulting_revision < 1
    ):
        raise ValueError("Continuation requires exact approval, sources, scope and revision")

    run_id = continuation_run_id(project_doc.id, actor_id, stage, approval_id)
    refs = _references(client, project_doc, run_id)
    now = _utcnow()
    transaction.create(
        refs.run,
        {
            "run_id": run_id,
            "project_id": project_doc.id,
            "action": "generate",
            "status": "blocked",
            "current_stage": artifact_stage,
            "current_action": "generate",
            "project_revision": resulting_revision,
            "request_id": request_id,
            "actor_user_id": actor_id,
            "idempotency_key": run_id,
            "started_at": now,
            "updated_at": now,
            # This field is intentionally outside the legacy public metadata projection.
            "continuation": {
                "schema_version": 1,
                "state": "queued",
                "stage": stage,
                "approval_id": approval_id,
                "approval_project_revision": resulting_revision,
                "source_snapshot_ids": source_snapshot_ids,
                "requirement_ids": requirement_ids,
                "options": options.model_dump(mode="json"),
                "deadline_at": now + timedelta(seconds=options.deadline_seconds),
                "attempts": 0,
                "provider_requests_reserved": 0,
            },
        },
    )
    transaction.create(refs.outbox, {"project_id": project_doc.id, "run_id": run_id, "available_at": now})
    return run_id


def _continuation(run):
    intent = (run or {}).get("continuation")
    return intent if intent and intent.get("schema_version") == 1 else None


def _matches_sources(project, run, intent):
    if (
        not project
        or project.get("project_id") != run["project_id"]
        or project.get("owner_user_id") != run["actor_user_id"]
        or project.get("status") != "active"
        or project.get("current_revision") != run["project_revision"]
    ):
        return False
    states = project.get("stage_state") or {}
    for stage, snapshot_id in intent["source_snapshot_ids"].items():
        state = states.get(stage) or {}
        if state.get("current_snapshot_id") != snapshot_id or (snapshot_id and state.get("stale")):
            return False
    if intent["stage"] == "generate_tests":
        state = states["use_cases"]
        review = (state.get("metadata") or {}).get("latest_human_review") or {}
        return (
            state.get("approved") is True
            and review.get("decision") == "approve"
            and review.get("snapshot_id") == state["current_snapshot_id"]
            and review.get("review_id") == intent["approval_id"]
        )
    return True


def _update_run(transaction, refs, intent, state, now, **fields):
    transaction.update(refs.run, {"status": _LEGACY_STATUS[state], "updated_at": now, "continuation": {**intent, "state": state}, **fields})


def _close(transaction, refs, run, intent, lease, state, now, reason=None, **fields):
    _update_run(transaction, refs, {**intent, "reason": reason}, state, now, completed_at=None if state == "interrupted" else now, **fields)
    transaction.delete(refs.outbox)
    if state != "interrupted" and lease_matches(lease, kind="continuation", operation_id=run["run_id"]):
        # Preserve the epoch even after a terminal outcome.
        transaction.set(refs.lease, released_project_lease(lease["fence"]))


def _expires_at(now, intent, lease_seconds):
    if not 1 <= lease_seconds <= 300:
        raise ValueError("Continuation lease duration must be between 1 and 300 seconds")
    return min(now + timedelta(seconds=lease_seconds), intent["deadline_at"])


def claim_stage(client, *, project_id, run_id, lease_seconds=60) -> ContinuationClaim | None:
    """Claim one due stage or close a known superseded/expired operation."""
    refs = _documents(client, project_id, run_id)

    @transactional
    def claim(transaction):
        project = _read(transaction, refs.project)
        run = _read(transaction, refs.run)
        intent = _continuation(run)
        if not intent or intent["state"] not in ("queued", "running"):
            return None
        outbox = _read(transaction, refs.outbox)
        lease = _read(transaction, refs.lease) or {}
        now = _utcnow()
        if not _matches_sources(project, run, intent):
            if intent.get("dispatch"):
                _close(transaction, refs, run, intent, lease, "interrupted", now, "sources_changed_provider_outcome_unconfirmed")
            else:
                _close(transaction, refs, run, intent, lease, "superseded", now, "project_or_sources_changed")
            return None
        if not outbox or outbox.get("project_id") != project_id or outbox.get("run_id") != run_id or outbox["available_at"] > now:
            return None
        if intent.get("dispatch"):
            _close(transaction, refs, run, intent, lease, "interrupted", now, "provider_outcome_unconfirmed")
            return None
        if intent["deadline_at"] <= now:
            _close(transaction, refs, run, intent, lease, "failed", now, "deadline_exceeded")
            return None
        if lease_blocks_claim(lease, now):
            return None
        fence = lease.get("fence", 0) + 1
        expires_at = _expires_at(now, intent, lease_seconds)
        transaction.set(refs.lease, {"run_id": run_id, "fence": fence, "expires_at": expires_at, "dispatched": False})
        transaction.update(refs.outbox, {"available_at": expires_at})
        _update_run(transaction, refs, intent, "running", now)
        return ContinuationClaim(project_id, run_id, fence, run_id)

    try:
        return claim(client.transaction())
    except Aborted:
        return None
    except ValueError as exc:
        # The Firestore SDK wraps exhausted transaction contention in ValueError.
        # Leave the outbox due for a later poll; a transport failure remains unknown.
        if isinstance(exc.__cause__, Aborted):
            return None
        raise


def _load_claim(transaction, refs, claim):
    project = _read(transaction, refs.project)
    run = _read(transaction, refs.run)
    intent = _continuation(run)
    lease = _read(transaction, refs.lease) or {}
    now = _utcnow()
    if (
        not intent
        or intent["state"] != "running"
        or claim.stage_request_id != run["run_id"]
        or not lease_matches(lease, kind="continuation", operation_id=claim.run_id, fence=claim.fence)
        or lease["expires_at"] <= now
        or intent["deadline_at"] <= now
        or not _matches_sources(project, run, intent)
    ):
        raise ContinuationConflict("Continuation claim expired, was cancelled or no longer matches the approved sources")
    return run, intent, lease, now


def renew_claim(client, claim, *, lease_seconds=60):
    refs = _documents(client, claim.project_id, claim.run_id)

    @transactional
    def renew(transaction):
        _, intent, _, now = _load_claim(transaction, refs, claim)
        expires_at = _expires_at(now, intent, lease_seconds)
        transaction.update(refs.lease, {"expires_at": expires_at})
        transaction.update(refs.outbox, {"available_at": expires_at})

    renew(client.transaction())


def mark_dispatched(client, claim, *, provider_requests=1):
    """Reserve a bounded possible paid attempt before invocation. Replays fail closed."""
    if type(provider_requests) is not int or provider_requests < 1:
        raise ValueError("Provider request reservation must be a positive integer")
    refs = _documents(client, claim.project_id, claim.run_id)

    @transactional
    def dispatch(transaction):
        _, intent, _, now = _load_claim(transaction, refs, claim)
        options = intent["options"]
        reserved = intent["provider_requests_reserved"] + provider_requests
        attempt = intent["attempts"] + 1
        if intent.get("dispatch") or attempt > options["max_attempts"] or reserved > options["max_provider_requests"]:
            raise ContinuationConflict("No unspent continuation dispatch is available for this claim")
        intent = {**intent, "attempts": attempt, "provider_requests_reserved": reserved, "dispatch": {"fence": claim.fence, "at": now}}
        transaction.update(refs.lease, {"dispatched": True})
        _update_run(transaction, refs, intent, "running", now)

    dispatch(client.transaction())


def stop_stage(client, claim, *, reason):
    """Close an admitted worker failure without releasing uncertain provider work."""
    if reason not in {"settings_changed", "actor_unavailable", "scenario_preparation_failed", "test_case_generation_failed", "publication_unconfirmed"}:
        raise ValueError("Unsupported continuation failure reason")
    refs = _documents(client, claim.project_id, claim.run_id)

    @transactional
    def stop(transaction):
        run, intent, lease, now = _load_claim(transaction, refs, claim)
        state = "interrupted" if intent.get("dispatch") else "failed"
        _close(transaction, refs, run, intent, lease, state, now, reason)

    stop(client.transaction())


def cancel_stage(client, *, project_id, run_id, actor_id) -> bool:
    refs = _documents(client, project_id, run_id)

    @transactional
    def cancel(transaction):
        project = _read(transaction, refs.project)
        if not project:
            raise ProjectNotFoundError(project_id)
        if project.get("owner_user_id") != actor_id:
            raise ProjectPermissionError(project_id)
        run = _read(transaction, refs.run)
        intent = _continuation(run)
        if not intent:
            raise ProjectNotFoundError(run_id)
        if intent["state"] == "cancelled":
            return True
        if intent["state"] not in ("queued", "running", "interrupted"):
            return False
        lease = _read(transaction, refs.lease) or {}
        _close(transaction, refs, run, intent, lease, "cancelled", _utcnow(), "cancelled_by_owner")
        return True

    return cancel(client.transaction())


def prepare_result_publication(transaction, client, claim, snapshot):
    """Validate the claim before other transaction writes, then return writes only."""
    refs = _documents(client, claim.project_id, claim.run_id)
    run, intent, lease, _ = _load_claim(transaction, refs, claim)
    expected_stage = _STAGES[intent["stage"]]
    sources = intent["source_snapshot_ids"]
    metadata = snapshot.get("metadata") or {}
    source_stage = "requirements" if expected_stage == "use_cases" else "use_cases"
    if (
        (intent.get("dispatch") or {}).get("fence") != claim.fence
        or snapshot.get("project_id") != claim.project_id
        or snapshot.get("stage") != expected_stage
        or snapshot.get("operation") != _OPERATIONS[expected_stage]
        or snapshot.get("actor_user_id") != run["actor_user_id"]
        or snapshot.get("request_id") != claim.stage_request_id
        or snapshot.get("project_revision") != run["project_revision"] + 1
        or snapshot.get("source_snapshot_id") != sources[source_stage]
        or metadata.get("source_requirements_snapshot_id") != sources["requirements"]
        or metadata.get("source_context_snapshot_id") != sources["context"]
        or not snapshot.get("snapshot_id")
    ):
        raise ContinuationConflict("Publication does not match the dispatched stage and its approved sources")

    def publish():
        now = _utcnow()
        if lease["expires_at"] <= now:
            raise ContinuationConflict("Continuation lease expired before result publication")
        _close(
            transaction,
            refs,
            run,
            intent,
            lease,
            "awaiting_user" if expected_stage == "use_cases" else "completed",
            now,
            project_revision=snapshot["project_revision"],
            produced_snapshot_ids={expected_stage: snapshot["snapshot_id"]},
        )

    return publish
