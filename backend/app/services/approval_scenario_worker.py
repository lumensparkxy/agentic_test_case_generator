"""Consume approved scenario preparations in an isolated emulator worker."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
import re
from threading import Lock

from fastapi import HTTPException
from google.cloud.firestore_v1 import FieldFilter

from ..agents.adk_runtime import bounded_model_requests
from ..config import DEFAULT_MODEL_NAME, model_scope
from ..models import AuthUser, WorkflowSettings
from .approval_continuation import (
    OUTBOX_COLLECTION,
    ContinuationConflict,
    ContinuationOptions,
    claim_stage,
    mark_dispatched,
    prepare_result_publication,
    renew_claim,
    stop_stage,
)
from .guidance_service import enabled
from .project_inputs import behavioural_version
from .use_case_generation_service import ApprovedScenarioSource, generate_project_use_cases
from .workflow_project_service import QA_PROJECTS_COLLECTION


DEMO_PROJECT_ID = "demo-tcg-v2-continuation"
_MAX_PROVIDER_REQUESTS = 7  # Up to three planner pairs and one complete-plan reviewer.
_MAX_REQUEST_SECONDS = 25
_DEADLINE_SECONDS = 240
_LEASE_SECONDS = 60
_LOG = logging.getLogger(__name__)


def approval_scenarios_enabled() -> bool:
    if os.getenv("TCG_APPROVAL_SCENARIOS_ENABLED") != "true" or os.getenv("FIREBASE_PROJECT_ID") != DEMO_PROJECT_ID:
        return False
    target = re.fullmatch(r"(?:127\.0\.0\.1|\[::1\]):([0-9]{1,5})", os.getenv("FIRESTORE_EMULATOR_HOST", ""))
    return bool(
        target
        and 1 <= int(target.group(1)) <= 65535
        and os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"", "false", "0"}
        and os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME).startswith("gemini-")
        and not enabled("memory", "use_cases")
        and not enabled("skills", "use_cases")
    )


def approval_scenario_client_allowed(client) -> bool:
    # The pinned Firestore SDK resolves its transport once when the client is constructed.
    return (
        approval_scenarios_enabled()
        and getattr(client, "project", None) == DEMO_PROJECT_ID
        and getattr(client, "_emulator_host", None) == os.getenv("FIRESTORE_EMULATOR_HOST")
    )


def _settings():
    return WorkflowSettings(max_iterations=1, timeout_seconds=90, retry_attempts=0)


def scenario_continuation_options() -> ContinuationOptions:
    fingerprint_input = {
        "policy": "emulator-scenario-worker-v1",
        "model": os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME),
        "behavior": behavioural_version(),
        "settings": _settings().model_dump(mode="json"),
        "max_request_seconds": _MAX_REQUEST_SECONDS,
        "max_provider_requests": _MAX_PROVIDER_REQUESTS,
        "deadline_seconds": _DEADLINE_SECONDS,
    }
    fingerprint = sha256(json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ContinuationOptions(settings_fingerprint=fingerprint, max_provider_requests=_MAX_PROVIDER_REQUESTS, deadline_seconds=_DEADLINE_SECONDS)


def _utcnow():
    return datetime.now(timezone.utc)


class _ScenarioBudget:
    def __init__(self, client, claim, intent):
        self.client = client
        self.claim = claim
        self.deadline_at = intent["deadline_at"]
        self.limit = intent["options"]["max_provider_requests"]
        self.started = 0
        self.failed = False
        self.lock = Lock()

    def reserve(self):
        with self.lock:
            if self.failed or self.started >= self.limit or not approval_scenarios_enabled():
                raise ContinuationConflict("The scenario model request budget is unavailable")
            try:
                renew_claim(self.client, self.claim, lease_seconds=_LEASE_SECONDS)
                if self.started == 0:
                    # No model call is permitted until its possible dispatch is durable.
                    mark_dispatched(self.client, self.claim, provider_requests=self.limit)
                remaining = (self.deadline_at - _utcnow()).total_seconds()
                if remaining <= 0:
                    raise ContinuationConflict("The scenario preparation deadline elapsed")
            except Exception:
                # Other shard callbacks must not retry a reservation with an unknown outcome.
                self.failed = True
                raise
            self.started += 1
            return min(remaining, _MAX_REQUEST_SECONDS)


def _owner_actor(project, actor_id):
    if not project or project.get("owner_user_id") != actor_id or project.get("status") != "active":
        return None
    recorded = project.get("actor") or {}
    if recorded.get("user_id") != actor_id or recorded.get("tenant_id") or recorded.get("organization_domain") or recorded.get("is_org_admin"):
        return None
    # Use the trusted emulator project owner identity. Do not recreate elevated roles or persist a token.
    return AuthUser(sub=actor_id, name=recorded.get("name") or actor_id, email=recorded.get("email"), provider=recorded.get("provider"))


def _handle_due(client, project_id, run_id):
    project_doc = client.collection(QA_PROJECTS_COLLECTION).document(project_id)
    run = project_doc.collection("orchestrator_runs").document(run_id).get().to_dict()
    intent = (run or {}).get("continuation") or {}
    if intent.get("stage") != "prepare_scenarios" or intent.get("schema_version") != 1:
        return False
    claim = claim_stage(client, project_id=project_id, run_id=run_id, lease_seconds=_LEASE_SECONDS)
    if claim is None:
        return False

    reason = "scenario_preparation_failed"
    try:
        if intent["options"] != scenario_continuation_options().model_dump(mode="json") or not approval_scenarios_enabled():
            reason = "settings_changed"
        else:
            actor = _owner_actor(project_doc.get().to_dict(), run["actor_user_id"])
            if actor is None:
                reason = "actor_unavailable"
            else:
                sources = intent["source_snapshot_ids"]
                budget = _ScenarioBudget(client, claim, intent)
                with bounded_model_requests(budget.reserve), model_scope(os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)):
                    generate_project_use_cases(
                        project_id=project_id,
                        actor=actor,
                        request_id=claim.stage_request_id,
                        base_project_revision=run["project_revision"],
                        memory_bypass=True,
                        approved_source=ApprovedScenarioSource(sources["requirements"], sources["context"], tuple(intent["requirement_ids"])),
                        workflow_settings=_settings(),
                        publication_guard=lambda tx, store, snapshot: prepare_result_publication(tx, store, claim, snapshot),
                    )
                return True
    except HTTPException as error:
        if isinstance(error.detail, dict) and error.detail.get("code") == "use_case_publication_unconfirmed":
            reason = "publication_unconfirmed"
    except Exception as error:
        # The durable state, not exception text, is what a restarted worker can trust.
        _LOG.warning("Scenario preparation could not finish (%s)", type(error).__name__)

    try:
        stop_stage(client, claim, reason=reason)
    except ContinuationConflict:
        # A saved result, cancelled claim or changed source may have won the race.
        claim_stage(client, project_id=project_id, run_id=run_id, lease_seconds=_LEASE_SECONDS)
    return True


def run_due_scenarios(client, *, limit=12) -> int:
    """Claim a bounded number of due records. Return the number of handled claims."""
    if not approval_scenario_client_allowed(client):
        raise RuntimeError("Scenario preparation requires the enabled loopback demo environment")
    if not 1 <= limit <= 50:
        raise ValueError("Scenario worker scan limit must be between 1 and 50")
    due = client.collection(OUTBOX_COLLECTION).where(filter=FieldFilter("available_at", "<=", _utcnow())).order_by("available_at").limit(limit).stream()
    handled = 0
    for record in due:
        row = record.to_dict()
        try:
            handled += _handle_due(client, row["project_id"], row["run_id"])
        except Exception as error:
            _LOG.warning("Could not handle a scenario continuation (%s)", type(error).__name__)
    return handled
