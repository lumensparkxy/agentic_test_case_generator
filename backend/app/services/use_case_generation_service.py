"""Generate a fresh, reviewable Use Cases snapshot from the durable baseline."""

from hashlib import sha256
import json

from fastapi import HTTPException
from pydantic import ValidationError

from ..agents.use_case_agent import generate_use_cases
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectUseCaseSnapshotInput
from ..models import GenerateTestCasesInput, Requirement, TestCaseTemplate
from .audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from .billing_service import enforce_billing_access
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope
from .workflow_project_service import ProjectConflictError, append_stage_snapshot, get_project


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


def generate_project_use_cases(*, project_id, actor, request_id, base_project_revision, memory_bypass=False):
    project = get_project(project_id, actor=actor)
    if project.current_revision != base_project_revision:
        raise ProjectConflictError(project.current_revision)
    if project.status != "active":
        raise HTTPException(409, "Restore this project before generating Use Cases.")
    baseline = project.current_snapshots.get("requirements")
    state = project.stage_state.get("requirements")
    if not baseline or not state or state.current_snapshot_id != baseline.snapshot_id or state.stale or not state.approved:
        raise HTTPException(409, "Review and approve the current requirements before generating Use Cases.")
    active = [Requirement.model_validate(r) for r in baseline.payload.get("requirements", [])]
    active = [r for r in active if r.lifecycle_status == "active" and r.review_status != "Rejected"]
    if not active or any(r.review_status != "Approved" for r in active) or len({r.id for r in active}) != len(active):
        raise HTTPException(409, "Review and approve the current requirements before generating Use Cases.")

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
    manifest = prepare_run(
        "use_cases",
        actor,
        project_id,
        request_id,
        requirement_ids=[r.id for r in active],
        memory_bypass=memory_bypass,
        input_fingerprint=sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()).hexdigest(),
    )
    metadata = {"project_id": project_id, "requirement_count": len(active), "source_requirements_snapshot_id": baseline.snapshot_id}
    run_id = start_workflow_run(operation="use_cases.generate", actor=actor, request_id=request_id, metadata=metadata)
    try:
        with guidance_scope(manifest):
            try:
                result = generate_use_cases(payload, actor_user_id=actor.sub, request_id=request_id, workflow_run_id=run_id, operation="use_cases.generate")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, "Use Cases generation failed. The previous snapshot was preserved. Retry generation.") from exc
            output = _validate_output(result, active)
            snapshot = append_stage_snapshot(
                project_id=project_id,
                stage="use_cases",
                payload=output.model_dump(mode="json", exclude={"approved", "source_snapshot_id", "base_project_revision"}),
                operation="use_cases.generate",
                actor=actor,
                request_id=request_id,
                workflow_run_id=run_id,
                approved=False,
                source_snapshot_id=baseline.snapshot_id,
                title="Use Cases generated for human review",
                metadata={**metadata, "source_context_snapshot_id": context_snapshot.snapshot_id if context_snapshot else None},
                base_project_revision=base_project_revision,
            )
    except Exception:
        complete_workflow_run(run_id, status="failed", metadata=metadata, error_message="Use Cases generation or revision commit failed")
        raise
    count = sum(len(group.scenarios) for group in output.coverage_plan)
    complete_workflow_run(run_id, status="completed", metadata={**metadata, "scenario_count": count, "snapshot_id": snapshot.snapshot_id})
    record_usage_event(
        event_type="use_cases.generated",
        billing_key="use_cases.generate",
        quantity=count,
        unit="use_case",
        actor=actor,
        request_id=request_id,
        workflow_run_id=run_id,
        status="completed",
        metadata=metadata,
    )
    return get_project(project_id, actor=actor)
