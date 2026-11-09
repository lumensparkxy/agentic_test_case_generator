"""Consume approved generation work in an isolated emulator worker."""

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
from ..config import DEFAULT_MODEL_NAME, get_generation_settings, model_scope
from ..models import AuthUser, GenerateTestCasesInput, TestCaseTemplate, WorkflowSettings
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
from .guidance_runtime import prepare_run
from .guidance_service import enabled, guidance_scope
from .project_inputs import behavioural_version
from .use_case_generation_service import ApprovedScenarioSource, generate_project_use_cases
from .workflow_project_service import QA_PROJECTS_COLLECTION


DEMO_PROJECT_ID = "demo-tcg-v2-continuation"
_MAX_PROVIDER_REQUESTS = 7  # Up to three planner pairs and one complete-plan reviewer.
_MAX_TEST_CASE_PROVIDER_REQUESTS = 24
_MAX_REQUEST_SECONDS = 25
_DEADLINE_SECONDS = 240
_TEST_CASE_DEADLINE_SECONDS = 300
_LEASE_SECONDS = 60
_LOG = logging.getLogger(__name__)


def _emulator_mode_enabled() -> bool:
    if os.getenv("TCG_APPROVAL_SCENARIOS_ENABLED") != "true" or os.getenv("FIREBASE_PROJECT_ID") != DEMO_PROJECT_ID:
        return False
    target = re.fullmatch(r"(?:127\.0\.0\.1|\[::1\]):([0-9]{1,5})", os.getenv("FIRESTORE_EMULATOR_HOST", ""))
    return bool(
        target
        and 1 <= int(target.group(1)) <= 65535
        and os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"", "false", "0"}
        and os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME).startswith("gemini-")
    )


def approval_scenarios_enabled() -> bool:
    return _emulator_mode_enabled() and not enabled("memory", "use_cases") and not enabled("skills", "use_cases")


def approval_test_cases_enabled() -> bool:
    return (
        _emulator_mode_enabled()
        and not enabled("memory", "test_cases")
        and not enabled("skills", "test_cases")
        and get_generation_settings().parallel_test_case_max_workers <= 3
    )


def approval_scenario_client_allowed(client) -> bool:
    # The pinned Firestore SDK resolves its transport once when the client is constructed.
    return (
        approval_scenarios_enabled()
        and getattr(client, "project", None) == DEMO_PROJECT_ID
        and getattr(client, "_emulator_host", None) == os.getenv("FIRESTORE_EMULATOR_HOST")
    )


def _settings(stage):
    if stage == "prepare_scenarios":
        return WorkflowSettings(max_iterations=1, timeout_seconds=90, retry_attempts=0)
    return WorkflowSettings(max_iterations=2, timeout_seconds=120, retry_attempts=0)


def _continuation_options(stage, policy, max_requests, deadline, **extra):
    fingerprint_input = {
        "policy": policy,
        "model": os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME),
        "behavior": behavioural_version(),
        "settings": _settings(stage).model_dump(mode="json"),
        "max_request_seconds": _MAX_REQUEST_SECONDS,
        "max_provider_requests": max_requests,
        "deadline_seconds": deadline,
        **extra,
    }
    fingerprint = sha256(json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ContinuationOptions(settings_fingerprint=fingerprint, max_provider_requests=max_requests, deadline_seconds=deadline)


def scenario_continuation_options() -> ContinuationOptions:
    return _continuation_options("prepare_scenarios", "emulator-scenario-worker-v1", _MAX_PROVIDER_REQUESTS, _DEADLINE_SECONDS)


def _default_test_case_template():
    return TestCaseTemplate(name="Default", format="json", fields=[])


def test_case_continuation_options() -> ContinuationOptions:
    generation_settings = get_generation_settings().model_dump(mode="json")
    return _continuation_options(
        "generate_tests",
        "emulator-test-case-worker-v1",
        _MAX_TEST_CASE_PROVIDER_REQUESTS,
        _TEST_CASE_DEADLINE_SECONDS,
        template=_default_test_case_template().model_dump(mode="json"),
        parallel_settings={name: value for name, value in generation_settings.items() if name.startswith("parallel_test_case_")},
    )


def _utcnow():
    return datetime.now(timezone.utc)


class _StageBudget:
    def __init__(self, client, claim, intent, enabled_for_stage):
        self.client = client
        self.claim = claim
        self.enabled_for_stage = enabled_for_stage
        self.deadline_at = intent["deadline_at"]
        self.limit = intent["options"]["max_provider_requests"]
        self.started = 0
        self.failed = False
        self.lock = Lock()

    def reserve(self):
        with self.lock:
            if self.failed or self.started >= self.limit or not self.enabled_for_stage():
                raise ContinuationConflict("The model request budget is unavailable")
            try:
                renew_claim(self.client, self.claim, lease_seconds=_LEASE_SECONDS)
                if self.started == 0:
                    # No model call is permitted until its possible dispatch is durable.
                    mark_dispatched(self.client, self.claim, provider_requests=self.limit)
                remaining = (self.deadline_at - _utcnow()).total_seconds()
                if remaining <= 0:
                    raise ContinuationConflict("The generation deadline elapsed")
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


def _invoke_stage(claim, run, intent, actor):
    sources = intent["source_snapshot_ids"]
    stage = intent["stage"]
    requirement_ids = tuple(intent["requirement_ids"])
    workflow_settings = _settings(stage)

    def guard(transaction, store, snapshot):
        return prepare_result_publication(transaction, store, claim, snapshot)

    if stage == "prepare_scenarios":
        generate_project_use_cases(
            project_id=claim.project_id,
            actor=actor,
            request_id=claim.stage_request_id,
            base_project_revision=run["project_revision"],
            memory_bypass=True,
            approved_source=ApprovedScenarioSource(sources["requirements"], sources["context"], requirement_ids),
            workflow_settings=workflow_settings,
            publication_guard=guard,
        )
        return

    from ..routers.testcases import generate_bound_test_cases
    from .approved_test_case_source import ApprovedTestCaseSource

    source = ApprovedTestCaseSource(sources["requirements"], sources["context"], sources["use_cases"], intent["approval_id"], requirement_ids)
    payload = GenerateTestCasesInput(
        project_id=claim.project_id,
        base_project_revision=run["project_revision"],
        requirements=[],
        template=_default_test_case_template(),
        workflow_settings=workflow_settings,
    )
    manifest = prepare_run(
        "test_cases",
        actor,
        claim.project_id,
        claim.stage_request_id,
        requirement_ids=requirement_ids,
        memory_bypass=True,
        input_fingerprint=intent["options"]["settings_fingerprint"],
    )
    with guidance_scope(manifest):
        generate_bound_test_cases(payload, actor=actor, request_id=claim.stage_request_id, approved_source=source, stage_publication_guard=guard)


def _handle_due(client, project_id, run_id):
    project_doc = client.collection(QA_PROJECTS_COLLECTION).document(project_id)
    run = project_doc.collection("orchestrator_runs").document(run_id).get().to_dict()
    intent = (run or {}).get("continuation") or {}
    stage = intent.get("stage")
    if stage not in {"prepare_scenarios", "generate_tests"} or intent.get("schema_version") != 1:
        return False
    enabled_for_stage = approval_scenarios_enabled if stage == "prepare_scenarios" else approval_test_cases_enabled
    if not enabled_for_stage():
        return False
    claim = claim_stage(client, project_id=project_id, run_id=run_id, lease_seconds=_LEASE_SECONDS)
    if claim is None:
        return False

    reason = "scenario_preparation_failed" if stage == "prepare_scenarios" else "test_case_generation_failed"
    options = scenario_continuation_options if stage == "prepare_scenarios" else test_case_continuation_options
    try:
        if intent["options"] != options().model_dump(mode="json") or not enabled_for_stage():
            reason = "settings_changed"
        else:
            actor = _owner_actor(project_doc.get().to_dict(), run["actor_user_id"])
            if actor is None:
                reason = "actor_unavailable"
            else:
                budget = _StageBudget(client, claim, intent, enabled_for_stage)
                with bounded_model_requests(budget.reserve), model_scope(os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)):
                    _invoke_stage(claim, run, intent, actor)
                return True
    except HTTPException as error:
        if isinstance(error.detail, dict) and error.detail.get("code") in {"use_case_publication_unconfirmed", "generation_publication_unconfirmed"}:
            reason = "publication_unconfirmed"
    except Exception as error:
        # The durable state, not exception text, is what a restarted worker can trust.
        _LOG.warning("Approval continuation could not finish (%s)", type(error).__name__)

    try:
        stop_stage(client, claim, reason=reason)
    except ContinuationConflict:
        # A saved result, cancelled claim or changed source may have won the race.
        claim_stage(client, project_id=project_id, run_id=run_id, lease_seconds=_LEASE_SECONDS)
    return True


def run_due_scenarios(client, *, limit=12) -> int:
    """Handle a bounded scan of scenario and Test Cases continuations."""
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
            _LOG.warning("Could not handle an approval continuation (%s)", type(error).__name__)
    return handled
