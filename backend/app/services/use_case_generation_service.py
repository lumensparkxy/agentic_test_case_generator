"""Generate a fresh, reviewable Use Cases snapshot from the durable baseline."""

from hashlib import sha256
import json

from fastapi import HTTPException
from pydantic import ValidationError

from ..agents.use_case_agent import RetainedUseCaseGroup, generate_use_cases
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectUseCaseSnapshotInput
from ..models import GenerateTestCasesInput, TestCaseTemplate
from ..observability.metrics import record_workflow_completed
from .audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from .billing_service import enforce_billing_access
from .firestore_repository import get_required_firestore_client
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope
from .orchestrator_service import _use_cases_human_approved
from .requirement_reconciliation import canonical_requirements
from .requirement_scope import approved_generation_requirements
from .test_case_quality import case_source_hash
from .workflow_project_service import (
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectPermissionError,
    append_stage_snapshot,
    find_idempotent_stage_snapshot,
    get_project,
    get_project_stage_snapshot,
)

_OPERATION = "use_cases.generate"


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


def _scoped_refresh(project, active, requested, actor):
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

    selected = set(requested)
    current = {row.id: row for row in active}
    if len(selected) != len(requested) or not selected <= current.keys():
        raise HTTPException(422, "Select distinct current approved requirement IDs for the scoped refresh.")
    required = {
        row.id
        for row in active
        if row.id not in prior_plan or row.text != old_by_id[row.id].text or _requirement_identity(row) != _requirement_identity(old_by_id[row.id])
    }
    if missing := required - selected:
        raise HTTPException(
            409,
            {"message": f"Include the changed or added requirements: {', '.join(sorted(missing))}.", "required_requirement_ids": sorted(missing)},
        )
    removed = sorted(prior_plan.keys() - current.keys())
    if not selected and not removed:
        raise HTTPException(422, "Select at least one requirement to refresh.")
    retained = {req_id: RetainedUseCaseGroup(prior_analysis[req_id], prior_plan[req_id]) for req_id in current if req_id not in selected}
    return retained, {
        "previous_snapshot_id": previous.snapshot_id,
        "refreshed_requirement_ids": [req_id for req_id in current if req_id in selected],
        "retained_requirement_ids": list(retained),
        "removed_requirement_ids": removed,
    }


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


def generate_project_use_cases(*, project_id, actor, request_id, base_project_revision, refresh_requirement_ids=None, memory_bypass=False):
    idempotency_key = case_source_hash([actor.sub, request_id])
    fingerprint = case_source_hash([base_project_revision, refresh_requirement_ids, memory_bypass])
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
    baseline = project.current_snapshots.get("requirements")
    active = approved_generation_requirements(project)
    if not active:
        raise HTTPException(409, "Review current requirements or explicitly defer unresolved scope before generating Use Cases.")

    retained, refresh = _scoped_refresh(project, active, refresh_requirement_ids, actor) if refresh_requirement_ids is not None else ({}, None)

    context_snapshot = project.current_snapshots.get("context")
    context_payload = context_snapshot.payload if context_snapshot else {}
    context = EnrichInput.model_validate({**context_payload, "requirements": active, "project_id": project_id, "base_project_revision": base_project_revision})
    payload = GenerateTestCasesInput(
        requirements=active,
        context=context,
        template=TestCaseTemplate(name="Use Cases", format="json", fields=[]),
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
        memory_bypass=memory_bypass,
        input_fingerprint=sha256(json.dumps(generation_input, sort_keys=True).encode()).hexdigest(),
    )
    metadata = {"project_id": project_id, "requirement_count": len(active), "source_requirements_snapshot_id": baseline.snapshot_id}
    if refresh:
        metadata["use_case_refresh"] = refresh
    run_id = start_workflow_run(operation=_OPERATION, actor=actor, request_id=request_id, metadata=metadata)
    try:
        with guidance_scope(manifest):
            try:
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
    except _PublicationUnconfirmed:
        raise
    except Exception:
        complete_workflow_run(run_id, status="failed", metadata=metadata, error_message="Use Cases generation or revision commit failed")
        raise
    if snapshot.workflow_run_id == run_id:
        record_workflow_completed(run_id, "completed")
    else:
        complete_workflow_run(run_id, status="completed", metadata={**metadata, "reused_snapshot_id": snapshot.snapshot_id})
    return _current_result(project_id, actor, snapshot)
