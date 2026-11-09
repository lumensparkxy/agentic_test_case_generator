"""Generate a fresh, reviewable Use Cases snapshot from the durable baseline."""

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import os

from fastapi import HTTPException
from pydantic import ValidationError

from ..agents.adk_runtime import bounded_model_requests
from ..agents.use_case_agent import RetainedUseCaseGroup, generate_use_cases
from ..config import DEFAULT_MODEL_NAME, model_scope
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectUseCaseSnapshotInput
from ..contracts.scenario_refresh import ScenarioRefreshResult, ScenarioRefreshStatus
from ..models import GenerateTestCasesInput, Requirement, TestCaseTemplate, WorkflowSettings
from ..observability.metrics import record_workflow_completed
from .audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from .billing_service import enforce_billing_access
from .firestore_repository import get_required_firestore_client
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope
from .orchestrator_service import _use_cases_human_approved
from .project_draft_admission import (
    SCENARIO_REFRESH_OPERATION,
    claim_draft_attempt,
    draft_admission_client,
    draft_refresh_deferred,
    read_scenario_refresh_admission,
    read_scenario_refresh_result,
    release_draft_attempt,
)
from .requirement_reconciliation import canonical_requirements
from .requirement_scope import approved_generation_requirements
from .test_case_quality import case_source_hash
from .workflow_project_service import (
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectPermissionError,
    QA_PROJECTS_COLLECTION,
    append_stage_snapshot,
    find_idempotent_stage_snapshot,
    get_project,
    get_project_stage_snapshot,
    get_project_summary,
)

_OPERATION = "use_cases.generate"
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovedScenarioSource:
    requirements_snapshot_id: str
    context_snapshot_id: str | None
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ScopedRefreshBasis:
    previous_snapshot_id: str
    previous_requirements_snapshot_id: str
    current: dict[str, Requirement]
    prior_plan: dict
    prior_analysis: dict
    required_ids: list[str]
    removed_ids: list[str]


class _PublicationUnconfirmed(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "use_case_publication_unconfirmed", "message": "Use Cases publication could not be confirmed. Retry this request."})


def _check_fingerprint(snapshot, fingerprint):
    if snapshot.metadata.get("generation_request_fingerprint") != fingerprint:
        raise HTTPException(409, "This request already published Use Cases for different inputs.")


def _current_result(project_id, actor, snapshot):
    try:
        project = get_project(project_id, actor=actor)
    except ProjectNotFoundError, ProjectPermissionError:
        raise
    except Exception as exc:
        raise _PublicationUnconfirmed() from exc
    state = project.stage_state.get("use_cases")
    if project.status != "active" or not state or state.stale or state.current_snapshot_id != snapshot.snapshot_id:
        raise ProjectConflictError(project.current_revision)
    if project.current_snapshots.get("use_cases") is None:
        raise _PublicationUnconfirmed()
    return project


def _replay_result(project_id, actor, idempotency_key, fingerprint):
    receipt = find_idempotent_stage_snapshot(project_id, "use_cases", _OPERATION, idempotency_key, actor=actor)
    if receipt is not None:
        _check_fingerprint(receipt, fingerprint)
        return _current_result(project_id, actor, receipt)
    return None


def _validate_output(result, requirements):
    try:
        output = QaProjectUseCaseSnapshotInput.model_validate(result)
    except ValidationError as exc:
        raise HTTPException(502, "Use Cases generation returned invalid output. The previous snapshot was preserved.") from exc
    diagnostics = output.workflow_diagnostics
    if (
        diagnostics.used_fallback
        or diagnostics.status in {"failed", "fallback"}
        or (diagnostics.timed_out and diagnostics.failure_reason != "semantic_review_timeout")
        or diagnostics.failed_shard_count
        or diagnostics.fallback_shard_count
    ):
        raise HTTPException(503, "Use Cases generation did not finish successfully. The previous snapshot was preserved. Retry generation.")
    expected = {r.id: r.text for r in requirements}
    for items in (output.requirement_analysis, output.coverage_plan):
        ids = [item.requirement_id for item in items]
        if len(ids) != len(set(ids)) or set(ids) != set(expected):
            raise HTTPException(502, "Generated Use Cases did not cover every approved requirement. The previous snapshot was preserved.")
        if any(item.requirement_text != expected[item.requirement_id] for item in items):
            raise HTTPException(502, "Generated Use Cases referenced a different requirements baseline. The previous snapshot was preserved.")
    scenario_ids = set()
    for group in output.coverage_plan:
        if not group.scenarios:
            raise HTTPException(502, "Generated Use Cases contained an empty requirement group. The previous snapshot was preserved.")
        for scenario in group.scenarios:
            if (
                scenario.requirement_id != group.requirement_id
                or not scenario.id.strip()
                or scenario.id in scenario_ids
                or not scenario.title.strip()
                or not scenario.objective.strip()
            ):
                raise HTTPException(502, "Generated Use Cases contained invalid scenarios. The previous snapshot was preserved.")
            scenario_ids.add(scenario.id)
    return output


def _requirement_identity(row):
    return {
        "requirement_uid": row.requirement_uid,
        "content_version": row.content_version,
        "clarification_status": row.clarification_status,
        "parent_requirement_id": row.parent_requirement_id,
    }


def _scoped_refresh_basis(project, active, actor):
    previous = project.current_snapshots.get("use_cases")
    state = project.stage_state.get("use_cases")
    if not previous or not state or not state.stale or state.current_snapshot_id != previous.snapshot_id or not _use_cases_human_approved(state):
        raise HTTPException(409, "Scoped refresh needs a stale, previously approved Use Cases snapshot. Use full regeneration for other snapshots.")
    context = project.current_snapshots.get("context")
    context_state = project.stage_state.get("context")
    if context and (not context_state or context_state.stale or context_state.current_snapshot_id != context.snapshot_id):
        raise HTTPException(409, "Refresh the saved Context before choosing which Use Cases to keep.")
    source_id = previous.metadata.get("source_requirements_snapshot_id") or previous.source_snapshot_id
    try:
        if not source_id:
            raise ValueError("Missing source snapshot")
        source = get_project_stage_snapshot(project.project_id, source_id, actor=actor)
        if source.stage != "requirements":
            raise ValueError("Incorrect source stage")
        old_rows, _ = canonical_requirements(project.project_id, source.model_dump(mode="json"))
        old_by_id = {row.id: row for row in old_rows}
        if len(old_by_id) != len(old_rows):
            raise ValueError("Ambiguous source requirements")
        prior_plan = {row["requirement_id"]: row for row in previous.payload.get("coverage_plan", [])}
        prior_analysis = {row["requirement_id"]: row for row in previous.payload.get("requirement_analysis", [])}
        _validate_output(previous.payload, [old_by_id[req_id] for req_id in prior_plan])
    except (HTTPException, KeyError, ProjectNotFoundError, TypeError, ValueError) as exc:
        raise HTTPException(409, "The previous Use Cases source or plan could not be verified. Use full regeneration.") from exc

    current = {row.id: row for row in active}
    required = [
        row.id
        for row in active
        if row.id not in prior_plan or row.text != old_by_id[row.id].text or _requirement_identity(row) != _requirement_identity(old_by_id[row.id])
    ]
    return _ScopedRefreshBasis(previous.snapshot_id, source_id, current, prior_plan, prior_analysis, required, sorted(prior_plan.keys() - current.keys()))


def _scoped_refresh(project, active, requested, actor, *, basis=None):
    basis = basis or _scoped_refresh_basis(project, active, actor)
    selected = set(requested)
    if len(selected) != len(requested) or not selected <= basis.current.keys():
        raise HTTPException(422, "Select distinct current approved requirement IDs for the scoped refresh.")
    if missing := set(basis.required_ids) - selected:
        raise HTTPException(
            409,
            {"message": f"Include the changed or added requirements: {', '.join(sorted(missing))}.", "required_requirement_ids": sorted(missing)},
        )
    if not selected and not basis.removed_ids:
        raise HTTPException(422, "Select at least one requirement to refresh.")
    retained = {req_id: RetainedUseCaseGroup(basis.prior_analysis[req_id], basis.prior_plan[req_id]) for req_id in basis.current if req_id not in selected}
    return retained, {
        "previous_snapshot_id": basis.previous_snapshot_id,
        "refreshed_requirement_ids": [req_id for req_id in basis.current if req_id in selected],
        "retained_requirement_ids": list(retained),
        "removed_requirement_ids": basis.removed_ids,
    }


def _current_project_snapshot(project, stage, *, stale=False):
    state = project.stage_state.get(stage)
    snapshot = project.current_snapshots.get(stage)
    if state and snapshot and state.current_snapshot_id == snapshot.snapshot_id and state.stale == stale:
        return snapshot
    return None


def _refresh_unavailable():
    return HTTPException(
        409,
        {
            "code": "scenario_refresh_unavailable",
            "message": "The scoped refresh needs current reviewed Requirements and Context, and a verified prior plan and suite.",
        },
    )


def _demo_refresh_basis(client, project, active, actor):
    baseline = _current_project_snapshot(project, "requirements")
    context = _current_project_snapshot(project, "context")
    previous = _current_project_snapshot(project, "use_cases", stale=True)
    suite = _current_project_snapshot(project, "test_cases", stale=True)
    if (
        project.status != "active"
        or not active
        or not baseline
        or baseline.operation != "requirements.review"
        or baseline.actor_user_id != actor.sub
        or not context
        or project.stage_state["context"].approved is not True
        or not previous
        or previous.operation != _OPERATION
        or not suite
    ):
        raise _refresh_unavailable()
    basis = _scoped_refresh_basis(project, active, actor)
    previous_context_id = previous.metadata.get("source_context_snapshot_id")
    recorded_sources = {
        "requirements": basis.previous_requirements_snapshot_id,
        "context": previous_context_id,
        "use_cases": previous.snapshot_id,
    }
    nested_sources = suite.metadata.get("source_snapshot_ids")
    if (
        not (basis.required_ids or basis.removed_ids)
        or baseline.snapshot_id == basis.previous_requirements_snapshot_id
        or previous.source_snapshot_id != basis.previous_requirements_snapshot_id
        or previous.metadata.get("source_requirements_snapshot_id") != basis.previous_requirements_snapshot_id
        or (previous.metadata.get("source_context_omitted") is True) != (previous_context_id is None)
        or not isinstance(nested_sources, dict)
        or any(nested_sources.get(stage) != snapshot_id for stage, snapshot_id in recorded_sources.items())
        or suite.metadata.get("source_requirements_snapshot_id") != recorded_sources["requirements"]
        or suite.metadata.get("source_context_snapshot_id") != previous_context_id
        or suite.metadata.get("source_use_case_snapshot_id") != previous.snapshot_id
    ):
        raise _refresh_unavailable()
    human = project.stage_state["use_cases"].metadata["latest_human_review"]
    review_id = human.get("review_id")
    if not isinstance(review_id, str) or not review_id or "/" in review_id or human.get("reviewer_user_id") != actor.sub:
        raise _refresh_unavailable()
    review = client.collection(QA_PROJECTS_COLLECTION).document(project.project_id).collection("use_case_reviews").document(review_id).get().to_dict()
    if not review or any(
        review.get(key) != value
        for key, value in {
            "review_id": review_id,
            "project_id": project.project_id,
            "stage": "use_cases",
            "snapshot_id": previous.snapshot_id,
            "decision": "approve",
            "reviewer_user_id": actor.sub,
            "resulting_project_revision": human.get("resulting_project_revision"),
        }.items()
    ):
        raise _refresh_unavailable()
    sources = {"requirements": baseline.snapshot_id, "context": context.snapshot_id, "use_cases": previous.snapshot_id, "test_cases": suite.snapshot_id}
    return basis, sources, review_id


def _scenario_refresh_result(project_id, actor, request_id):
    client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
    snapshot = read_scenario_refresh_result(client, project_id=project_id, actor_id=actor.sub, request_id=request_id)
    if snapshot is None:
        return None
    return ScenarioRefreshResult(
        request_id=snapshot["request_id"],
        snapshot_id=snapshot["snapshot_id"],
        project_revision=snapshot["project_revision"],
        **snapshot["metadata"]["use_case_refresh"],
    )


def scenario_refresh_status(project_id, actor, *, request_id=None):
    from .approval_scenario_worker import approval_scenarios_enabled

    get_project_summary(project_id, actor=actor)
    result = _scenario_refresh_result(project_id, actor, request_id) if request_id else None
    client = draft_admission_client(read_only=True)
    if client is None:
        return ScenarioRefreshStatus(result=result)
    admission = read_scenario_refresh_admission(client, project_id, actor.sub)
    if not approval_scenarios_enabled():
        return ScenarioRefreshStatus(result=result, **admission)
    project = get_project(project_id, actor=actor)
    try:
        basis, _, _ = _demo_refresh_basis(client, project, approved_generation_requirements(project), actor)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return ScenarioRefreshStatus(enabled=True, result=result, **admission)
    return ScenarioRefreshStatus(enabled=True, eligible=True, required_requirement_ids=basis.required_ids, result=result, **admission)


def release_scenario_refresh(project_id, actor, operation_id, fence):
    client = draft_admission_client()
    if client is None:
        raise _refresh_unavailable()
    return release_draft_attempt(client, project_id, actor.sub, operation_id, fence, scenario_refresh=True)


def _snapshot_payload(result, active, retained, *, scoped):
    output = _validate_output(result, active)
    payload = output.model_dump(mode="json", exclude={"approved", "source_snapshot_id", "base_project_revision"})
    if not scoped:
        return payload
    assessment = output.review.semantic_assessment
    if not assessment or assessment.assessed_count != sum(len(group.scenarios) for group in output.coverage_plan):
        raise HTTPException(503, "The combined Use Cases review is unavailable. The previous snapshot was preserved. Retry the scoped refresh.")
    for field, attribute in (("requirement_analysis", "analysis"), ("coverage_plan", "plan")):
        for index, row in enumerate(result[field]):
            if group := retained.get(row["requirement_id"]):
                prior = getattr(group, attribute)
                if row != prior:
                    raise HTTPException(502, "Scoped refresh changed a retained Use Cases group. The previous snapshot was preserved.")
                payload[field][index] = prior
    return payload


def generate_project_use_cases(
    *,
    project_id,
    actor,
    request_id,
    base_project_revision,
    refresh_requirement_ids=None,
    memory_bypass=False,
    approved_source: ApprovedScenarioSource | None = None,
    workflow_settings: WorkflowSettings | None = None,
    publication_guard=None,
    request_id_explicit=True,
):
    admitted_continuation = approved_source is not None and publication_guard is not None
    draft_client = None if admitted_continuation else draft_admission_client()
    idempotency_key = case_source_hash([actor.sub, request_id])
    request_inputs = [base_project_revision, refresh_requirement_ids, memory_bypass]
    if approved_source is not None:
        request_inputs.extend(
            [
                approved_source.requirements_snapshot_id,
                approved_source.context_snapshot_id,
                approved_source.requirement_ids,
                workflow_settings.model_dump(mode="json") if workflow_settings else None,
            ]
        )
    fingerprint = case_source_hash(request_inputs)
    replay = _replay_result(project_id, actor, idempotency_key, fingerprint)
    if replay is not None:
        return replay

    project = get_project(project_id, actor=actor)
    if project.current_revision != base_project_revision:
        # The original request may have committed between the two reads.
        replay = _replay_result(project_id, actor, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        raise ProjectConflictError(project.current_revision)
    if project.status != "active":
        raise HTTPException(409, "Restore this project before generating Use Cases.")
    demo_refresh = draft_client is not None
    if demo_refresh and refresh_requirement_ids is None:
        raise draft_refresh_deferred()
    baseline = project.current_snapshots.get("requirements")
    active = approved_generation_requirements(project)
    if not active:
        raise HTTPException(409, "Review current requirements or explicitly defer unresolved scope before generating Use Cases.")

    scope_basis = None
    if demo_refresh:
        from .approval_scenario_worker import approval_scenarios_enabled, scenario_continuation_options, scenario_workflow_settings

        if not approval_scenarios_enabled():
            raise _refresh_unavailable()
        if not request_id_explicit or not request_id.strip() or len(request_id) > 160:
            raise HTTPException(422, "A bounded X-Request-ID is required for scoped scenario refresh.")
        scope_basis, source_ids, previous_review_id = _demo_refresh_basis(draft_client, project, active, actor)
        workflow_settings = scenario_workflow_settings()
        options = scenario_continuation_options()
    retained, refresh = (
        _scoped_refresh(project, active, refresh_requirement_ids, actor, basis=scope_basis) if refresh_requirement_ids is not None else ({}, None)
    )

    context_snapshot = project.current_snapshots.get("context")
    if approved_source is not None:
        context_state = project.stage_state.get("context")
        if (
            baseline.snapshot_id != approved_source.requirements_snapshot_id
            or baseline.operation != "requirements.review"
            or baseline.actor_user_id != actor.sub
            or tuple(row.id for row in active) != approved_source.requirement_ids
            or (context_snapshot.snapshot_id if context_snapshot else None) != approved_source.context_snapshot_id
            or (context_snapshot and (not context_state or context_state.stale or context_state.current_snapshot_id != context_snapshot.snapshot_id))
        ):
            raise HTTPException(409, "The saved scope or Context no longer matches this approval.")
    context_payload = context_snapshot.payload if context_snapshot else {}
    context = EnrichInput.model_validate({**context_payload, "requirements": active, "project_id": project_id, "base_project_revision": base_project_revision})
    payload = GenerateTestCasesInput(
        requirements=active,
        context=context,
        template=TestCaseTemplate(name="Use Cases", format="json", fields=[]),
        workflow_settings=workflow_settings,
        project_id=project_id,
        base_project_revision=base_project_revision,
    )
    # Same access gate as test generation; this operation creates no billable test cases.
    enforce_billing_access(current_user=actor, billing_key="testcases.generate")
    generation_input = payload.model_dump(mode="json")
    if refresh:
        generation_input["use_case_refresh"] = refresh
    manifest = prepare_run(
        "use_cases",
        actor,
        project_id,
        request_id,
        requirement_ids=refresh["refreshed_requirement_ids"] if refresh else [r.id for r in active],
        memory_bypass=memory_bypass or demo_refresh,
        input_fingerprint=sha256(json.dumps(generation_input, sort_keys=True).encode()).hexdigest(),
    )
    metadata = {"project_id": project_id, "requirement_count": len(active), "source_requirements_snapshot_id": baseline.snapshot_id}
    if refresh:
        metadata["use_case_refresh"] = refresh
    claim = None
    if demo_refresh:
        predecessor = {
            "previous_requirements_snapshot_id": scope_basis.previous_requirements_snapshot_id,
            "previous_review_id": previous_review_id,
        }
        claim = claim_draft_attempt(
            draft_client,
            project_id=project_id,
            actor_id=actor.sub,
            request_id=request_id,
            operation=SCENARIO_REFRESH_OPERATION,
            source_snapshot_ids=source_ids,
            base_revision=base_project_revision,
            max_requests=options.max_provider_requests,
            deadline_seconds=options.deadline_seconds,
            settings_fingerprint=case_source_hash(["scoped-scenario-refresh-v1", options.settings_fingerprint]),
            scenario_refresh={**predecessor, "scope": refresh, "request_fingerprint": fingerprint},
        )
        if claim.result_snapshot_id:
            replay = _replay_result(project_id, actor, idempotency_key, fingerprint)
            if replay is None:
                raise _PublicationUnconfirmed()
            return replay
        metadata["scenario_refresh_predecessor"] = {**predecessor, "source_test_cases_snapshot_id": source_ids["test_cases"]}

        def publication_guard(transaction, _client, snapshot):
            return claim.prepare_publication(transaction, snapshot)

    run_id = None
    try:
        run_id = start_workflow_run(operation=_OPERATION, actor=actor, request_id=request_id, metadata=metadata)
        with guidance_scope(manifest):
            try:
                budget = bounded_model_requests(claim.reserve) if claim is not None else nullcontext()
                model = model_scope(os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)) if claim is not None else nullcontext()
                with budget, model:
                    result = generate_use_cases(
                        payload,
                        actor_user_id=actor.sub,
                        request_id=request_id,
                        workflow_run_id=run_id,
                        operation=_OPERATION,
                        **({"retained_groups": retained} if refresh else {}),
                    )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, "Use Cases generation failed. The previous snapshot was preserved. Retry generation.") from exc
            saved_payload = _snapshot_payload(result, active, retained, scoped=refresh is not None)
            count = sum(len(group["scenarios"]) for group in saved_payload["coverage_plan"] if group["requirement_id"] not in retained)
            client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
            pending_writes = []
            event_id = record_usage_event(
                event_type="use_cases.generated",
                billing_key=_OPERATION,
                quantity=count,
                unit="use_case",
                actor=actor,
                request_id=request_id,
                workflow_run_id=run_id,
                status="completed",
                metadata=metadata,
                pending_writes=pending_writes,
                firestore_client=client,
            )

            def complete_in_transaction(transaction, snapshot):
                advance = publication_guard(transaction, client, snapshot) if publication_guard is not None else None
                writes = []
                complete_workflow_run(
                    run_id,
                    status="completed",
                    metadata={**metadata, "scenario_count": count, "snapshot_id": snapshot["snapshot_id"]},
                    pending_writes=writes,
                    firestore_client=client,
                )
                for reference, value, merge in writes:
                    transaction.set(reference, value, merge=merge)
                if advance is not None:
                    advance()

            try:
                snapshot = append_stage_snapshot(
                    project_id=project_id,
                    stage="use_cases",
                    payload=saved_payload,
                    operation=_OPERATION,
                    actor=actor,
                    request_id=request_id,
                    workflow_run_id=run_id,
                    source_event_id=event_id,
                    approved=False,
                    source_snapshot_id=baseline.snapshot_id,
                    title="Use Cases generated for human review",
                    metadata={
                        **metadata,
                        "source_context_snapshot_id": context_snapshot.snapshot_id if context_snapshot else None,
                        **({"source_context_omitted": True} if context_snapshot is None else {}),
                        "source_requirement_identities": {row.id: _requirement_identity(row) for row in active},
                        "generation_request_fingerprint": fingerprint,
                    },
                    base_project_revision=base_project_revision,
                    idempotency_key=idempotency_key,
                    pending_writes=pending_writes,
                    transaction_guard=complete_in_transaction,
                )
            except Exception:
                try:
                    # Reconcile the commit before checking whether its result is still current.
                    snapshot = find_idempotent_stage_snapshot(project_id, "use_cases", _OPERATION, idempotency_key, actor=actor, require_current=False)
                except ProjectConflictError, ProjectNotFoundError, ProjectPermissionError:
                    raise
                except Exception as exc:
                    raise _PublicationUnconfirmed() from exc
                if snapshot is None:
                    raise
            _check_fingerprint(snapshot, fingerprint)
    except Exception as error:
        stopped = claim is None and not isinstance(error, _PublicationUnconfirmed)
        if claim is not None:
            try:
                if claim.read_result():
                    replay = _replay_result(project_id, actor, idempotency_key, fingerprint)
                    if replay is None:
                        raise _PublicationUnconfirmed()
                    return replay
                stopped = claim.stop()
            except ProjectConflictError, ProjectNotFoundError, ProjectPermissionError:
                raise
            except Exception as exc:
                _LOG.warning("Could not reconcile scoped scenario refresh (%s)", type(exc).__name__)
        if run_id and stopped:
            complete_workflow_run(run_id, status="failed", metadata=metadata, error_message="Use Cases generation or revision commit failed")
        raise
    if snapshot.workflow_run_id == run_id:
        record_workflow_completed(run_id, "completed")
    else:
        complete_workflow_run(run_id, status="completed", metadata={**metadata, "reused_snapshot_id": snapshot.snapshot_id})
    return _current_result(project_id, actor, snapshot)
