"""Assess immutable saved tests without a generation pass."""

from contextlib import nullcontext
import logging

from fastapi import HTTPException
from pydantic import ValidationError

from ..agents.adk_runtime import bounded_model_requests
from ..agents.substantive_review import apply_assessment, assess_delivered_suite
from ..agents.test_case_review import _heuristic_test_case_review, _resolve_test_case_workflow_settings
from ..config import get_settings
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectDetail
from ..contracts.requirements import RequirementCoveragePlan, WorkflowSettings
from ..contracts.test_cases import TestCase
from ..models import AuthUser
from .audit_service import complete_workflow_run, start_workflow_run
from .guidance_runtime import prepare_run
from .guidance_service import enabled, guidance_scope, public_manifest
from .orchestrator_service import _use_cases_human_approved
from .project_draft_admission import REPAIR_OPERATION, SCOPED_IMPACT_OPERATION, claim_draft_attempt, draft_admission_client, read_draft_attempt_result
from .requirement_scope import approved_generation_requirements
from .suite_quality import build_suite_design_proof
from .test_case_quality import case_source_hash, delivered_coverage, generated_suite_review_retryable, separate_generation_work
from .test_case_review_progress import SavedReviewProgress
from .workflow_project_service import ProjectConflictError, _serialize_value, append_stage_snapshot, get_project, get_project_stage_snapshot

REVIEW_OPERATION = "test_cases.review"
REVIEWABLE_OPERATIONS = {"impact.update.apply", "testcases.refine", "quality.feedback.apply", REPAIR_OPERATION}
_MAX_REVIEW_PROVIDER_REQUESTS = 24
_REVIEW_DEADLINE_SECONDS = 240
_SOURCE_SNAPSHOT_KEYS = {
    "requirements": "source_requirements_snapshot_id",
    "context": "source_context_snapshot_id",
    "use_cases": "source_use_case_snapshot_id",
}
_RECORDED_SOURCE_FIELDS = {
    *_SOURCE_SNAPSHOT_KEYS.values(),
    "source_snapshot_ids",
    "source_requirement_hashes",
    "source_use_case_hashes",
    "source_context_hash",
    "source_context_omitted",
    "source_context_artifact_ids",
    "generation_base_project_revision",
}


def _current_review_inputs(project, suite):
    requirements = approved_generation_requirements(project)
    use_cases = project.current_snapshots.get("use_cases")
    state = project.stage_state.get("use_cases")
    if (
        not requirements
        or not use_cases
        or not state
        or state.stale
        or not _use_cases_human_approved(state)
        or state.current_snapshot_id != use_cases.snapshot_id
    ):
        raise HTTPException(409, "Approve current Requirements and Use Cases before reviewing the saved suite.")

    sources = {stage: project.current_snapshots[stage].snapshot_id for stage in ("requirements", "context", "use_cases") if stage in project.current_snapshots}
    context = project.current_snapshots.get("context")
    context_state = project.stage_state.get("context")
    if context and (not context_state or context_state.stale or context_state.current_snapshot_id != context.snapshot_id):
        raise HTTPException(409, "Refresh Context before reviewing the saved suite.")
    if suite.operation == "quality.feedback.apply":
        expected_requirements = (suite.metadata.get("source_snapshot_ids") or {}).get("requirements")
        if (
            not sources.get("context")
            or sources["context"] != suite.metadata.get("source_context_snapshot_id")
            or project.current_snapshots["requirements"].project_revision > suite.project_revision
            or (expected_requirements and sources["requirements"] != expected_requirements)
        ):
            raise HTTPException(409, "Source inputs changed after prerequisite correction. Analyze their impact before reviewing.")

    required_ids = {row.id for row in requirements}
    try:
        plans = [RequirementCoveragePlan.model_validate(row) for row in use_cases.payload.get("coverage_plan", [])]
        plans = [plan.model_dump(mode="json") for plan in plans if plan.requirement_id in required_ids]
        enriched = EnrichInput.model_validate({**context.payload, "requirements": requirements}) if context else None
    except (TypeError, ValidationError) as exc:
        raise HTTPException(409, "The current source plan or Context needs review before these tests can be assessed.") from exc
    if len(plans) != len(required_ids) or {plan["requirement_id"] for plan in plans if plan["scenarios"]} != required_ids:
        raise HTTPException(409, "The current approved Use Cases plan does not cover the requirement scope.")
    analysis = [row for row in use_cases.payload.get("requirement_analysis", []) if row.get("requirement_id") in required_ids]
    return requirements, plans, analysis, enriched, sources


def _require_recorded_scope(suite, requirements):
    required_ids = {row.id for row in requirements}
    hashes = suite.metadata.get("source_requirement_hashes")
    saved_plan = suite.payload.get("coverage_plan")
    plan_ids = {
        group["requirement_id"]
        for group in (saved_plan if isinstance(saved_plan, list) else [])
        if isinstance(group, dict) and isinstance(group.get("requirement_id"), str)
    }
    if (isinstance(hashes, dict) and hashes and not required_ids.issubset(hashes)) or (plan_ids and not required_ids.issubset(plan_ids)):
        raise HTTPException(409, "The saved suite covers a narrower requirement scope. Refresh the affected tests before reviewing.")


def _recorded_sources_match(metadata, sources):
    recorded_ids = metadata.get("source_snapshot_ids", {})
    if not isinstance(recorded_ids, dict):
        return False
    for stage, key in _SOURCE_SNAPSHOT_KEYS.items():
        expected = sources.get(stage)
        recorded = [value for value in (recorded_ids.get(stage), metadata.get(key)) if value is not None]
        if (expected is not None and not recorded) or any(value != expected for value in recorded):
            return False
    context_hash = metadata.get("source_context_hash")
    if sources.get("context") is None:
        return metadata.get("source_context_omitted") is True and context_hash is None and "source_context_artifact_ids" not in metadata
    return metadata.get("source_context_omitted") is not True and isinstance(context_hash, str) and bool(context_hash)


def _review_publication(project, reviewed, sources, recorded, exact_sources, parent_snapshot_id):
    metadata = {
        "source_snapshot_ids": sources,
        **{key: sources.get(stage) for stage, key in _SOURCE_SNAPSHOT_KEYS.items()},
        "source_test_cases_snapshot_id": parent_snapshot_id,
        "approved": reviewed["approved"],
    }
    if exact_sources:
        matched_metadata = {**recorded, **metadata}
        proof = build_suite_design_proof(project, reviewed, matched_metadata, plan_snapshot_id=sources["use_cases"])
        if proof is not None:
            return matched_metadata, proof
    if recorded:
        metadata["reviewed_suite_source_provenance"] = recorded
    return metadata, None


def _review_payload(payload, requirements, plans, analysis, context, *, progress):
    try:
        cases = [TestCase.model_validate(case) for case in payload.get("test_cases", [])]
        workflow_settings = WorkflowSettings.model_validate(payload.get("workflow_settings") or {})
    except (TypeError, ValidationError) as exc:
        raise HTTPException(409, "The saved suite contains invalid test data. Repair it before review.") from exc
    # Historical cases stay in the immutable suite and cannot count as current coverage.
    active = [case.model_dump(mode="json") for case in cases if case.status != "Deprecated"]
    if not active or len({case["id"] for case in active}) != len(active):
        raise HTTPException(409, "Review requires concrete saved test cases with distinct IDs.")
    pending = [task for task in payload.get("generation_tasks", []) if task.get("kind") != "substantive_coverage"]
    concrete, tasks = separate_generation_work(active, plans, pending_tasks=pending)
    if not concrete:
        raise HTTPException(409, "Repair unfinished generation before reviewing the saved suite.")
    review = _heuristic_test_case_review(
        concrete, requirements, _resolve_test_case_workflow_settings(workflow_settings)["approval_threshold"], plans, analysis, context
    )
    refs = {plan["requirement_id"]: {scenario["id"] for scenario in plan["scenarios"]} for plan in plans}
    invalid = [
        case["id"]
        for case in concrete
        if not case["linked_requirement_ids"]
        or not set(case["linked_requirement_ids"]) <= refs.keys()
        or not case["scenario_refs"]
        or not set(case["scenario_refs"]) <= set().union(*(refs.get(req_id, set()) for req_id in case["linked_requirement_ids"]))
    ]
    if invalid:
        review["approved"] = False
        review["blocking_issues"].append(f"These tests need exact current requirement and scenario links: {', '.join(invalid[:10])}.")
        review["summary"] = "Review current scenario links before approving the saved suite."

    try:
        settings = get_settings()
    except RuntimeError as exc:
        raise HTTPException(503, "Independent review is unavailable. The saved suite is unchanged.") from exc
    assessment = assess_delivered_suite(concrete, requirements, context, settings=settings, progress=progress)
    if assessment.get("model_status", assessment["status"]) == "unknown":
        diagnostics = assessment.get("diagnostics") or {}
        if diagnostics.get("failure_reason") == "review_input_too_large":
            raise HTTPException(422, "The saved suite exceeds the independent review input limit. The saved cases are unchanged.")
        if diagnostics.get("unverified_group_count"):
            raise HTTPException(
                503,
                {
                    "code": "review_groups_unverified",
                    "message": f"{assessment['reason']} The saved suite is unchanged. Try review again.",
                    "groups": diagnostics["groups"],
                },
            )
        raise HTTPException(503, "Independent review is unavailable. The saved suite is unchanged. Try review again.")
    reviewed = apply_assessment(
        {
            **payload,
            "test_cases": concrete,
            "approved": review["approved"],
            "review": review,
            "coverage_plan": plans,
            "requirement_analysis": analysis,
            "generation_tasks": tasks,
            "coverage_metrics": delivered_coverage(concrete, plans),
            "workflow_diagnostics": dict(payload.get("workflow_diagnostics") or {}),
            "generation_evidence": dict(payload.get("generation_evidence") or {}),
        },
        assessment,
    )
    return {**reviewed, "test_cases": payload["test_cases"]}


def _generated_review_context(suite, context):
    expected = suite.metadata.get("source_context_hash")
    if expected is None and (context is None or suite.metadata.get("source_context_omitted") is True):
        return None  # Generation had no Context in its input.
    if context and expected:
        artifact_ids = suite.metadata.get("source_context_artifact_ids")
        grounded = context.grounded_context
        if grounded and artifact_ids is not None:
            if not isinstance(artifact_ids, list) or not all(isinstance(item, str) for item in artifact_ids):
                raise HTTPException(409, "The original generation Context cannot be matched to the saved sources. Analyze their impact before reviewing.")
            selected = set(artifact_ids)
            grounded = grounded.model_copy(
                update={
                    "artifact_sources": [source for source in grounded.artifact_sources if source.id in selected],
                    "ui_elements": [element for element in grounded.ui_elements if not element.source_id or element.source_id in selected],
                }
            )
            context = context.model_copy(update={"grounded_context": grounded})

        # The workbench sends empty link lists as null and adds the originating
        # project revision. Older drafts have the full request hash only.
        workbench_context = context.model_copy(
            update={
                "diagram_links": context.diagram_links or None,
                "image_links": context.image_links or None,
                "project_id": suite.project_id,
                "base_project_revision": suite.metadata.get("generation_base_project_revision", suite.project_revision - 1),
            }
        )
        for candidate in (context, workbench_context):
            if case_source_hash(candidate.model_dump(mode="json")) == expected:
                return candidate
    raise HTTPException(409, "The original generation Context cannot be matched to the saved sources. Analyze their impact before reviewing.")


def _prepare_generated_review(suite, sources, requirements, context, actor, request_id, *, memory_bypass=False):
    recorded = suite.metadata.get("source_snapshot_ids") or {}
    expected_requirements = {requirement.id: case_source_hash(requirement.model_dump(mode="json")) for requirement in requirements}
    if (
        suite.source_snapshot_id != sources["use_cases"]
        or any(recorded.get(stage) != sources.get(stage) for stage in ("requirements", "context", "use_cases"))
        or suite.metadata.get("source_requirement_hashes") != expected_requirements
    ):
        raise HTTPException(409, "The saved suite was generated from different sources. Analyze their impact before reviewing.")
    context = _generated_review_context(suite, context)
    manifest = prepare_run(
        "test_cases",
        actor,
        suite.project_id,
        f"saved-review:{request_id}",
        requirement_ids=[requirement.id for requirement in requirements],
        input_fingerprint=suite.snapshot_id,
        memory_bypass=memory_bypass,
    )
    recorded_guidance = suite.metadata.get("guidance") or {}
    if manifest.project_inputs:
        recorded_inputs = recorded_guidance.get("project_inputs")
        current_inputs = manifest.project_inputs.model_dump(mode="json", exclude_none=True)
        if (recorded_inputs and current_inputs != recorded_inputs) or (not recorded_inputs and manifest.project_inputs.decisions):
            raise HTTPException(409, "Project decisions or sources changed after generation. Analyze their impact before reviewing.")
    original_id = recorded_guidance.get("manifest_id")
    if isinstance(original_id, str) and original_id:
        # The request fingerprint differs between generation and review. Bind
        # to the generation manifest only when all current guidance matches.
        original = manifest.model_copy(update={"manifest_id": original_id})
        if _serialize_value(public_manifest(original)) == recorded_guidance:
            manifest = original
    return guidance_scope(manifest), context


def review_saved_test_cases(*, project_id: str, snapshot_id: str, base_project_revision: int, actor: AuthUser, request_id: str) -> QaProjectDetail:
    project = get_project(project_id, actor=actor)
    client = draft_admission_client()
    if client is not None and read_draft_attempt_result(
        client,
        project_id=project_id,
        actor_id=actor.sub,
        request_id=request_id,
        operation=REVIEW_OPERATION,
        source_snapshot_id=snapshot_id,
        base_revision=base_project_revision,
    ):
        return get_project(project_id, actor=actor)
    current = project.current_snapshots.get("test_cases")
    if project.status != "active":
        raise HTTPException(409, "Restore the project before reviewing its test cases.")
    if (
        current
        and current.operation == REVIEW_OPERATION
        and (client is None or current.request_id == request_id)
        and current.source_snapshot_id == snapshot_id
        and current.project_revision == project.current_revision == base_project_revision + 1
    ):
        return project  # The exact request already committed, for example after a lost response.
    if project.current_revision != base_project_revision:
        raise ProjectConflictError(project.current_revision)
    if not current or current.snapshot_id != snapshot_id:
        raise HTTPException(409, "The saved test suite changed. Reload the current suite before reviewing.")
    generated_retry = generated_suite_review_retryable(current)
    pending_review = (current.payload.get("substantive_assessment") or {}).get("status") == "unknown"
    if not generated_retry and (current.operation not in REVIEWABLE_OPERATIONS or not pending_review):
        raise HTTPException(409, "The current suite has no pending saved-suite assessment.")
    if project.stage_state["test_cases"].stale and current.operation != "quality.feedback.apply":
        raise HTTPException(409, "Upstream inputs changed. Analyze their impact before reviewing the saved suite.")

    requirements, plans, analysis, context, sources = _current_review_inputs(project, current)
    original = get_project_stage_snapshot(project_id, snapshot_id, actor=actor)
    _require_recorded_scope(original, requirements)
    recorded = {key: original.metadata[key] for key in _RECORDED_SOURCE_FIELDS if key in original.metadata}
    exact_sources = _recorded_sources_match(recorded, sources)
    requires_exact_sources = current.operation == REPAIR_OPERATION or (current.operation == SCOPED_IMPACT_OPERATION and "scoped_impact" in original.metadata)
    if requires_exact_sources and not exact_sources:
        raise HTTPException(409, "The updated suite no longer matches its recorded sources. Analyze their impact before reviewing.")
    admission = None
    if client is not None:
        if enabled("skills", "test_cases") or enabled("memory", "test_cases"):
            raise HTTPException(409, "Saved-suite review in this demo requires the standard guidance settings.")
        admission = claim_draft_attempt(
            client,
            project_id=project_id,
            actor_id=actor.sub,
            request_id=request_id,
            operation=REVIEW_OPERATION,
            source_snapshot_ids={**sources, "context": sources.get("context"), "test_cases": snapshot_id},
            base_revision=base_project_revision,
            max_requests=_MAX_REVIEW_PROVIDER_REQUESTS,
            deadline_seconds=_REVIEW_DEADLINE_SECONDS,
        )
        if admission.result_snapshot_id:
            return get_project(project_id, actor=actor)

    progress = SavedReviewProgress(project_id, snapshot_id, base_project_revision, actor, admission=admission)
    run_id = None

    def publish_review(transaction, snapshot):
        if admission is not None:
            admission.finish_on_commit(transaction, snapshot)
        progress.clear_on_commit(transaction, snapshot)

    try:
        review_scope = nullcontext()
        if generated_retry:
            review_scope, context = _prepare_generated_review(original, sources, requirements, context, actor, request_id, memory_bypass=admission is not None)
        elif exact_sources:
            try:
                context = _generated_review_context(original, context)
            except HTTPException:
                if requires_exact_sources:
                    raise
                # Older supported operations can still use the existing assessment path.
                exact_sources = False
        run_id = start_workflow_run(operation=REVIEW_OPERATION, actor=actor, request_id=request_id, metadata={"source_snapshot_id": snapshot_id})
        model_scope = bounded_model_requests(admission.reserve) if admission is not None else nullcontext()
        with review_scope, model_scope:
            reviewed = _review_payload(original.payload, requirements, plans, analysis, context, progress=progress)
            metadata, proof = _review_publication(project, reviewed, sources, recorded, exact_sources, original.snapshot_id)
            append_stage_snapshot(
                project_id=project_id,
                stage="test_cases",
                payload=reviewed,
                operation=REVIEW_OPERATION,
                actor=actor,
                request_id=request_id,
                workflow_run_id=run_id,
                approved=reviewed["approved"],
                source_snapshot_id=snapshot_id,
                title="Saved test suite reviewed",
                metadata=metadata,
                base_project_revision=base_project_revision,
                idempotency_key=admission.operation_id if admission is not None else None,
                transaction_guard=publish_review,
                suite_design_proof=proof,
            )
    except Exception:
        if admission is not None:
            try:
                admission.stop()
            except Exception as exc:
                logging.warning("Could not reconcile saved-suite review admission (%s)", type(exc).__name__)
        if run_id is not None:
            complete_workflow_run(run_id, status="failed", error_message="Saved suite review was not published")
        raise
    complete_workflow_run(run_id, status="completed", metadata={"approved": reviewed["approved"]})
    return get_project(project_id, actor=actor)
