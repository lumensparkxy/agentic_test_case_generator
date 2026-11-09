"""Bounded direct source updates using the existing deterministic impact and specialist path."""

from dataclasses import asdict, dataclass
import json
import logging
import os
from time import monotonic

from fastapi import HTTPException
from pydantic import ValidationError

from ..agents.adk_runtime import bounded_model_requests
from ..agents.evaluation_limits import current_evaluation_limits
from ..agents.substantive_review import RUBRIC, bind_reviewed_inputs, review_input, validate_assessment
from ..config import DEFAULT_MODEL_NAME, get_generation_settings, model_scope
from ..contracts.scoped_impact import ScopedImpactResult, ScopedImpactStatus
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectStageSnapshot
from ..models import GenerateTestCasesInput, ImpactAnalysisResult, ImpactUpdateApplyResult, Requirement, TestCase, TestCaseTemplate, WorkflowSettings
from .approval_scenario_worker import approval_test_cases_enabled
from .approved_test_case_source import _generation_context
from .firestore_repository import get_required_firestore_client
from .guidance_runtime import prepare_run
from .guidance_service import public_manifest
from .impact_application_state import SharedImpactApplication
from .orchestrator_service import _matching_use_case_human_review, _use_cases_human_approved
from .project_draft_admission import (
    SCOPED_IMPACT_OPERATION,
    claim_draft_attempt,
    draft_admission_client,
    draft_refresh_deferred,
    read_scoped_impact_admission,
    read_scoped_impact_result,
    release_draft_attempt,
)
from .project_inputs import behavioural_version
from .requirement_scope import approved_generation_requirements
from .scenario_review_state import current_scenario_reviews, scenario_key
from .test_case_quality import case_source_hash, delivered_coverage, placeholder_reason, separate_generation_work
from .use_case_generation_service import _current_project_snapshot, _validate_output
from .use_case_review_service import recorded_use_case_context_matches
from .workflow_project_service import QA_PROJECTS_COLLECTION, ProjectConflictError, get_project, get_project_stage_snapshot, get_project_summary


_LOG = logging.getLogger(__name__)
_MAX_REQUESTS = 24
_MAX_CASES = 6
_DEADLINE_SECONDS = 300
_SETTINGS = WorkflowSettings(max_iterations=1, timeout_seconds=120, retry_attempts=0)
_TEMPLATE = TestCaseTemplate(name="Targeted tests", format="json", fields=["id", "title", "steps", "expected_result"])
_BILLING_KEY = "testcases.generate"


@dataclass(frozen=True)
class _ImpactSource:
    ids: dict[str, str]
    review_id: str
    analysis: ImpactAnalysisResult
    plan: QaProjectStageSnapshot
    original: QaProjectStageSnapshot
    cases: list[TestCase]
    context: EnrichInput
    requirements: list[Requirement]
    scenario_hashes: dict[str, str]
    work: dict


def _unavailable():
    return HTTPException(
        409,
        {"code": "scoped_impact_unavailable", "message": "Approve the exact refreshed scenario plan and analyze its impact on the saved suite."},
    )


def _scope_error():
    return HTTPException(
        422,
        {"code": "scoped_impact_invalid_selection", "message": "Select every direct update shown, up to six distinct saved test cases."},
    )


def _receipt(client, project_id, actor, request_id, fingerprint=None):
    snapshot = read_scoped_impact_result(client, project_id=project_id, actor_id=actor.sub, request_id=request_id, request_fingerprint=fingerprint)
    if snapshot is None:
        return None
    result = snapshot["payload"]["impact_update_result"]
    return ScopedImpactResult(
        request_id=snapshot["request_id"],
        analysis_snapshot_id=snapshot["source_snapshot_id"],
        source_snapshot_id=snapshot["metadata"]["source_snapshot_ids"]["test_cases"],
        result_snapshot_id=snapshot["snapshot_id"],
        project_revision=snapshot["project_revision"],
        accepted_recommendation_ids=result["applied_recommendation_ids"],
        changed_test_case_ids=result["changed_test_case_ids"],
        preserved_count=result["preserved_count"],
        updated_count=result["updated_count"],
    )


def _read_review(client, project, plan, actor):
    state = project.stage_state["use_cases"]
    human = _matching_use_case_human_review(state)
    identity = human.get("review_id")
    revision = human.get("resulting_project_revision")
    if (
        not _use_cases_human_approved(state)
        or not isinstance(identity, str)
        or not identity
        or "/" in identity
        or human.get("reviewer_user_id") != actor.sub
        or type(revision) is not int
        or not plan.project_revision < revision < project.current_revision
    ):
        raise _unavailable()
    doc = client.collection(QA_PROJECTS_COLLECTION).document(project.project_id).collection("use_case_reviews").document(identity)
    review = doc.get().to_dict()
    expected = {
        "review_id": identity,
        "project_id": project.project_id,
        "stage": "use_cases",
        "snapshot_id": plan.snapshot_id,
        "decision": "approve",
        "reviewer_user_id": actor.sub,
        "resulting_project_revision": revision,
    }
    if not review or any(review.get(key) != value for key, value in expected.items()):
        raise _unavailable()
    return identity


def _load_source(client, project, actor):
    from . import impact_update_service as impact

    requirements = approved_generation_requirements(project)
    baseline = _current_project_snapshot(project, "requirements")
    context = _current_project_snapshot(project, "context")
    plan = _current_project_snapshot(project, "use_cases")
    old = _current_project_snapshot(project, "test_cases", stale=True)
    analysis_snapshot = _current_project_snapshot(project, "impact_analysis")
    if (
        project.status != "active"
        or not requirements
        or not baseline
        or baseline.operation != "requirements.review"
        or baseline.actor_user_id != actor.sub
        or not context
        or project.stage_state["context"].approved is not True
        or not plan
        or plan.operation != "use_cases.generate"
        or plan.source_snapshot_id != baseline.snapshot_id
        or plan.metadata.get("source_requirements_snapshot_id") != baseline.snapshot_id
        or not recorded_use_case_context_matches(plan.metadata, context.snapshot_id)
        or not old
        or not analysis_snapshot
        or analysis_snapshot.operation != "impact.analysis"
        or analysis_snapshot.project_revision != project.current_revision
        or analysis_snapshot.source_snapshot_id != old.snapshot_id
    ):
        raise _unavailable()
    sources = {"requirements": baseline.snapshot_id, "context": context.snapshot_id, "use_cases": plan.snapshot_id, "test_cases": old.snapshot_id}
    analysis = ImpactAnalysisResult.model_validate(analysis_snapshot.payload)
    old_sources = impact._baseline_snapshot_ids(old)
    predecessor = plan.metadata.get("scenario_refresh_predecessor")
    refresh = plan.metadata.get("use_case_refresh")
    baseline_ids = {**old_sources, "test_cases": old.snapshot_id}
    if (
        analysis.current_snapshot_ids != sources
        or not isinstance(predecessor, dict)
        or predecessor.get("source_test_cases_snapshot_id") != old.snapshot_id
        or predecessor.get("previous_requirements_snapshot_id") != old_sources["requirements"]
        or not isinstance(refresh, dict)
        or refresh.get("previous_snapshot_id") != old_sources["use_cases"]
        or not analysis.baseline_snapshot_ids.keys() <= baseline_ids.keys()
        or any(analysis.baseline_snapshot_ids.get(stage) != snapshot_id for stage, snapshot_id in baseline_ids.items())
    ):
        raise _unavailable()
    review_id = _read_review(client, project, plan, actor)
    try:
        impact._require_apply_approval(analysis)
        validated = _validate_output(plan.payload, requirements)
    except (impact.ImpactWorkflowError, HTTPException) as exc:
        raise _unavailable() from exc
    full_context = _generation_context(context, requirements, project)
    original = get_project_stage_snapshot(project.project_id, old.snapshot_id, actor=actor)
    cases = impact._test_cases_from_snapshot(original)
    raw_rows = original.payload.get("test_cases")
    if not isinstance(raw_rows, list) or len(cases) != len(raw_rows) or len({case.id for case in cases}) != len(cases):
        raise _unavailable()
    # Only rows recreated by the deterministic analyzer may enter this path.
    canonical = impact._build_analysis(project=project, actor=actor)
    known = {row.recommendation_id: row for row in canonical.recommendations}
    by_case = {case.id: case for case in cases}
    by_req = {row.id: row for row in requirements}
    groups = {group.requirement_id: group for group in validated.coverage_plan}
    reviews = current_scenario_reviews(plan.payload, project.stage_state["use_cases"].metadata, plan.snapshot_id)
    work = {}
    for rec in analysis.recommendations:
        if rec.action != "update" or rec.impact_source != "direct" or known.get(rec.recommendation_id) != rec:
            continue
        case = by_case.get(rec.test_case_id)
        target = _target_work(project, case, rec, by_req, groups, validated.requirement_analysis, reviews, full_context)
        if target:
            work[rec.recommendation_id] = target
    required = {rec.recommendation_id for rec in canonical.recommendations if rec.action == "update" and rec.impact_source == "direct"}
    obsolete = any(
        rec.action == "deprecate" and rec.impact_source == "direct" and rec.test_case_id in by_case and by_case[rec.test_case_id].status != "Deprecated"
        for rec in canonical.recommendations
    )
    if obsolete or set(work) != required or len(required) > _MAX_CASES or len({target.previous_case.id for target in work.values()}) != len(work):
        raise _unavailable()
    scenario_hashes = {scenario.id: case_source_hash(scenario.model_dump(mode="json")) for group in validated.coverage_plan for scenario in group.scenarios}
    return _ImpactSource(
        {**sources, "impact_analysis": analysis_snapshot.snapshot_id},
        review_id,
        analysis,
        plan,
        original,
        cases,
        full_context,
        requirements,
        scenario_hashes,
        work,
    )


def _target_work(project, case, rec, requirements, groups, analysis, reviews, full_context):
    from .impact_update_service import _TargetedGeneration

    if (
        not case
        or case.status == "Deprecated"
        or placeholder_reason(case)
        or not all((case.artifact_set_id, case.artifact_item_id, case.artifact_version_id, case.artifact_version_number))
    ):
        return None
    ids = list(dict.fromkeys([*case.linked_requirement_ids, *([rec.requirement_id] if rec.requirement_id else [])]))
    if not ids or not set(ids) <= requirements.keys():
        return None
    current_refs = {scenario.id for requirement_id in ids for scenario in groups[requirement_id].scenarios}
    refs = set(rec.scenario_refs) | (set(case.scenario_refs) & current_refs)
    if not refs or not refs <= current_refs:
        return None
    selected_plans = []
    for requirement_id in ids:
        group = groups[requirement_id]
        selected = [scenario for scenario in group.scenarios if scenario.id in refs]
        if not selected or any(reviews.get(scenario_key(requirement_id, scenario.id), {}).get("status") != "approved" for scenario in selected):
            return None
        selected_plans.append(group.model_copy(update={"scenarios": selected}))
    reqs = [requirements[requirement_id] for requirement_id in ids]
    payload = GenerateTestCasesInput(
        project_id=project.project_id,
        base_project_revision=project.current_revision,
        requirements=reqs,
        coverage_plan=selected_plans,
        requirement_analysis=[row for row in analysis if row.requirement_id in ids],
        context=full_context.model_copy(update={"requirements": reqs}),
        template=_TEMPLATE,
        workflow_settings=_SETTINGS,
        feedback=(
            "Replace the single affected saved test with exactly one concrete test using its selected scenario scope. "
            "Use the current approved requirement and Context. Exercise the changed input, assert the observable outcome, "
            "and keep assertions about unaffected behavior where supported. Identify unknown facts instead of inventing rules or setup. "
            f"Impact evidence: {rec.reason}"
        ),
    )
    return _TargetedGeneration(rec, case, payload, frozenset(refs))


def _verify_target(work, response):
    from .impact_update_service import ImpactWorkflowError

    rejected = "Targeted generation needs exactly one independently verified replacement. The saved suite was preserved."
    if len(response.test_cases) != 1:
        raise ImpactWorkflowError(rejected)
    case = response.test_cases[0]
    assessment = response.substantive_assessment or {}
    diagnostics = assessment.get("diagnostics") or {}
    data, input_hash, _ = review_input([case], work.payload.requirements, work.payload.context, scope_plan=work.payload.coverage_plan)
    if (
        case.status == "Deprecated"
        or case.generation_source in {"deterministic_full_fallback", "deterministic_coverage_completion"}
        or len(case.linked_requirement_ids) != len(work.payload.requirements)
        or set(case.linked_requirement_ids) != {row.id for row in work.payload.requirements}
        or len(case.scenario_refs) != len(work.scenario_refs)
        or set(case.scenario_refs) != work.scenario_refs
        or all(getattr(case, field) == getattr(work.previous_case, field) for field in ("steps", "expected_result", "test_data", "preconditions"))
        or assessment.get("input_hash") != input_hash
        or assessment.get("rubric") != RUBRIC
        or assessment.get("scope") != "selected_scenarios"
        or assessment.get("status") != "assessed_complete"
        or assessment.get("design_status") != "assessed_complete"
        or assessment.get("model_status") == "unknown"
        or not isinstance(diagnostics, dict)
        or diagnostics.get("failure_reason")
        or diagnostics.get("unverified_group_count", 0) != 0
    ):
        raise ImpactWorkflowError(rejected)
    try:
        raw = {key: assessment[key] for key in ("input_hash", "obligations", "case_grounding")}
        verified = bind_reviewed_inputs(validate_assessment(json.dumps(raw), data, input_hash), data)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ImpactWorkflowError(rejected) from exc
    if (
        verified["status"] != "assessed_complete"
        or verified["reviewed_case_content_hash"] != assessment.get("reviewed_case_content_hash")
        or verified["reviewed_source_binding"] != assessment.get("reviewed_source_binding")
    ):
        raise ImpactWorkflowError(rejected)


def scoped_impact_status(project_id, actor, *, request_id=None):
    get_project_summary(project_id, actor=actor)
    result = None
    if request_id:
        client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
        result = _receipt(client, project_id, actor, request_id)
    client = draft_admission_client(read_only=True)
    if client is None:
        return ScopedImpactStatus(result=result)
    admission = read_scoped_impact_admission(client, project_id, actor.sub)
    if not approval_test_cases_enabled():
        return ScopedImpactStatus(result=result, **admission)
    try:
        source = _load_source(client, get_project(project_id, actor=actor), actor)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return ScopedImpactStatus(enabled=True, result=result, **admission)
    return ScopedImpactStatus(enabled=True, eligible=bool(source.work), selectable_recommendation_ids=list(source.work), result=result, **admission)


def release_scoped_impact(project_id, actor, operation_id, fence):
    client = draft_admission_client()
    if client is None:
        raise _unavailable()
    return release_draft_attempt(client, project_id, actor.sub, operation_id, fence, scoped_impact=True)


def scoped_impact_fingerprint(analysis_id, revision, selected):
    return case_source_hash([analysis_id, revision, sorted(selected) if selected is not None else None])


def replay_scoped_impact(client, project, actor, request_id, fingerprint):
    result = _receipt(client, project.project_id, actor, request_id, fingerprint)
    if result is None:
        return None
    current = get_project(project.project_id, actor=actor)
    state = current.stage_state.get("test_cases")
    if not state or state.stale or state.current_snapshot_id != result.result_snapshot_id:
        raise ProjectConflictError(current.current_revision)
    return current


def apply_scoped_impact(client, project, actor, request_id, *, analysis_id, revision, selected, request_id_explicit):
    from . import impact_update_service as impact

    if not approval_test_cases_enabled():
        raise draft_refresh_deferred()
    if not request_id_explicit or not request_id.strip() or len(request_id) > 160:
        raise HTTPException(422, "A bounded X-Request-ID is required for scoped impact application.")
    if not analysis_id or selected is None or not 1 <= len(selected) <= _MAX_CASES or len(set(selected)) != len(selected):
        raise _scope_error()
    if revision != project.current_revision:
        raise ProjectConflictError(project.current_revision)
    source = _load_source(client, project, actor)
    if source.ids["impact_analysis"] != analysis_id:
        raise _unavailable()
    if set(selected) != source.work.keys():
        raise _scope_error()
    inputs = [source.work[rec_id] for rec_id in sorted(selected)]
    if len({work.previous_case.id for work in inputs}) != len(inputs):
        raise _scope_error()
    settings = get_generation_settings()
    model = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
    policy = case_source_hash(
        [
            "scoped-impact-v1",
            model,
            behavioural_version(),
            settings.model_dump(mode="json"),
            asdict(current_evaluation_limits.get()),
            _SETTINGS.model_dump(mode="json"),
            _TEMPLATE.model_dump(mode="json"),
            RUBRIC,
            _MAX_REQUESTS,
            _DEADLINE_SECONDS,
        ]
    )
    fingerprint = scoped_impact_fingerprint(analysis_id, revision, selected)
    versions = {
        work.previous_case.id: {
            "case_version_id": work.previous_case.artifact_version_id,
            "case_version_number": work.previous_case.artifact_version_number,
        }
        for work in inputs
    }
    intent = {"review_id": source.review_id, "request_fingerprint": fingerprint, "accepted_recommendation_ids": sorted(selected), "case_versions": versions}
    recovery_binding = case_source_hash([project.project_id, actor.sub, revision, source.ids, intent, policy])
    claim = claim_draft_attempt(
        client,
        project_id=project.project_id,
        actor_id=actor.sub,
        request_id=request_id,
        operation=SCOPED_IMPACT_OPERATION,
        source_snapshot_ids=source.ids,
        base_revision=revision,
        max_requests=_MAX_REQUESTS,
        deadline_seconds=_DEADLINE_SECONDS,
        settings_fingerprint=policy,
        scoped_impact=intent,
    )
    if claim.result_snapshot_id:
        return _confirmed(client, project, actor, request_id, fingerprint)
    application = SharedImpactApplication(client, project.project_id, analysis_id, claim, intent["accepted_recommendation_ids"], recovery_binding)
    run_id = None
    try:
        application.open()
        billing = impact.enforce_billing_access(current_user=actor, billing_key=_BILLING_KEY)
        run_id = impact.start_workflow_run(
            operation=SCOPED_IMPACT_OPERATION,
            actor=actor,
            request_id=request_id,
            metadata={"project_id": project.project_id, "analysis_snapshot_id": analysis_id},
        )
        hashes = [case_source_hash(work.payload.model_dump(mode="json")) for work in inputs]
        manifests = [
            prepare_run(
                "test_cases",
                actor,
                project.project_id,
                f"scoped-impact:{recovery_binding}:{work.recommendation.recommendation_id}",
                requirement_ids=[row.id for row in work.payload.requirements],
                memory_bypass=True,
                input_fingerprint=input_hash,
            )
            for work, input_hash in zip(inputs, hashes, strict=True)
        ]
        keys = [
            case_source_hash([work.recommendation.recommendation_id, input_hash, manifest.manifest_id, recovery_binding])
            for work, input_hash, manifest in zip(inputs, hashes, manifests, strict=True)
        ]
        started = monotonic()
        worker_limit = min(3, settings.parallel_test_case_max_workers) if settings.parallel_test_case_generation_enabled else 1
        with bounded_model_requests(claim.reserve), model_scope(model):
            responses, workers, reused = impact._run_targeted_batch(
                inputs,
                manifests,
                keys,
                application,
                worker_limit=worker_limit,
                actor_user_id=actor.sub,
                request_id=request_id,
                run_id=run_id,
                verifier=_verify_target,
            )
        metrics = {
            "targeted_generation_worker_count": workers,
            "targeted_generation_reused_count": reused,
            "targeted_generation_duration_ms": round((monotonic() - started) * 1000, 2),
        }
        _publish(source, actor, request_id, revision, intent, application, run_id, billing, inputs, responses, manifests, keys, metrics)
    except Exception:
        try:
            if claim.read_result():
                return _confirmed(client, project, actor, request_id, fingerprint)
            if claim.stop() and run_id:
                impact.complete_workflow_run(run_id, status="failed", error_message="Scoped impact generation or publication failed.")
        except ProjectConflictError:
            raise
        except Exception as exc:
            _LOG.warning("Could not reconcile scoped impact publication (%s)", type(exc).__name__)
        raise
    return _confirmed(client, project, actor, request_id, fingerprint)


def _confirmed(client, project, actor, request_id, fingerprint):
    result = replay_scoped_impact(client, project, actor, request_id, fingerprint)
    if result is None:
        raise HTTPException(503, {"code": "scoped_impact_unconfirmed", "message": "Check the exact saved impact request before trying again."})
    return result


def _replacement(work, response):
    old = work.previous_case
    candidate = response.test_cases[0]
    return candidate.model_copy(
        update={
            "id": old.id,
            "status": "In Review",
            "tags": list(dict.fromkeys((candidate.tags or []) + ["impact:update"])),
            **{field: getattr(old, field) for field in ("artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number")},
        }
    )


def _publish(source, actor, request_id, revision, intent, application, run_id, billing, inputs, responses, manifests, keys, metrics):
    from . import impact_update_service as impact

    replacements = {work.previous_case.id: _replacement(work, response) for work, response in zip(inputs, responses, strict=True)}
    writes = []
    project_id = source.original.project_id
    event_id = impact.record_usage_event(
        event_type="testcases.generated",
        billing_key=_BILLING_KEY,
        quantity=len(replacements),
        unit="test_case",
        actor=actor,
        request_id=request_id,
        workflow_run_id=run_id,
        status="completed",
        metadata={"operation": SCOPED_IMPACT_OPERATION, "analysis_snapshot_id": source.ids["impact_analysis"]},
        event_id=f"impact-{project_id}-{source.ids['impact_analysis']}",
        pending_writes=writes,
        firestore_client=application.client,
    )
    cases = impact.persist_test_case_versions(
        current_test_cases=[replacements.get(case.id, case) for case in source.cases],
        previous_test_cases=source.cases,
        actor=actor,
        request_id=request_id,
        workflow_run_id=run_id,
        source_event_id=event_id,
        operation=SCOPED_IMPACT_OPERATION,
        approved=False,
        changed_case_ids=set(replacements),
        pending_writes=writes,
    )
    raw_rows = {row["id"]: row for row in source.original.payload["test_cases"]}
    rows = [case.model_dump(mode="json") if case.id in replacements else raw_rows[case.id] for case in cases]
    plan = source.plan.payload["coverage_plan"]
    concrete, new_tasks = separate_generation_work(rows, plan)
    # Existing findings remain available for the separately requested whole-suite review.
    tasks = list(source.original.payload.get("generation_tasks") or [])
    tasks.extend(task for task in new_tasks if task not in tasks)
    result = ImpactUpdateApplyResult(
        applied_recommendation_ids=intent["accepted_recommendation_ids"],
        changed_test_case_ids=sorted(replacements),
        preserved_count=len(cases) - len(replacements),
        updated_count=len(replacements),
    ).model_dump(mode="json", exclude={"test_cases"})
    payload = {
        **source.original.payload,
        "test_cases": rows,
        "approved": False,
        "substantive_assessment": {"status": "unknown", "reason": "Targeted changes require assessment of the combined suite."},
        "generation_tasks": tasks,
        "impact_analysis": source.analysis.model_dump(mode="json"),
        "impact_update_result": result,
        "review": {"approved": False, "summary": "Targeted changes require suite review.", "blocking_issues": ["Review the changed test suite before export."]},
        "coverage_plan": plan,
        "requirement_analysis": source.plan.payload["requirement_analysis"],
        "coverage_metrics": delivered_coverage(concrete, plan),
        "workflow_diagnostics": {"status": "completed", "used_fallback": False},
        "generation_evidence": {"operation": SCOPED_IMPACT_OPERATION, "final_test_case_count": len(concrete), "final_status": "needs_review"},
    }
    sources = {stage: source.ids[stage] for stage in ("requirements", "context", "use_cases", "test_cases")}
    metadata = {
        "test_case_count": len(concrete),
        "source_snapshot_ids": sources,
        "source_requirements_snapshot_id": sources["requirements"],
        "source_context_snapshot_id": sources["context"],
        "source_use_case_snapshot_id": sources["use_cases"],
        "source_test_cases_snapshot_id": sources["test_cases"],
        "source_impact_analysis_snapshot_id": source.ids["impact_analysis"],
        "source_requirement_hashes": {row.id: case_source_hash(row.model_dump(mode="json")) for row in source.requirements},
        "source_use_case_hashes": source.scenario_hashes,
        "source_context_hash": case_source_hash(source.context.model_dump(mode="json")),
        "generation_base_project_revision": revision,
        "scoped_impact": intent,
        "review_retry_available": True,
        **({"source_context_artifact_ids": [row.id for row in source.context.grounded_context.artifact_sources]} if source.context.grounded_context else {}),
        **{key: value for key, value in result.items() if key.endswith("_count")},
        **metrics,
        "targeted_generation_runs": [
            {
                "recommendation_id": work.recommendation.recommendation_id,
                "source_test_case_id": work.previous_case.id,
                "source_project_revision": revision,
                "scenario_refs": sorted(work.scenario_refs),
                "generation_evidence": response.generation_evidence.model_dump(mode="json"),
                "substantive_assessment": response.substantive_assessment,
                "guidance": public_manifest(manifest),
            }
            for work, response, manifest in zip(inputs, responses, manifests, strict=True)
        ],
    }
    impact.complete_workflow_run(
        run_id,
        status="completed",
        metadata={"generated_count": len(replacements), **metrics},
        pending_writes=writes,
        firestore_client=application.client,
    )

    def commit(transaction, snapshot):
        finish = application.prepare_commit(transaction, snapshot, keys)
        impact.record_billing_consumption(
            current_user=actor,
            billing_context=billing,
            source_event_id=event_id,
            request_id=request_id,
            workflow_run_id=run_id,
            billing_key=_BILLING_KEY,
            quantity=len(replacements),
            unit="test_case",
            transaction=transaction,
            firestore_client=application.client,
        )
        finish()

    impact.append_stage_snapshot(
        project_id=project_id,
        stage="test_cases",
        payload=payload,
        operation=SCOPED_IMPACT_OPERATION,
        actor=actor,
        request_id=request_id,
        workflow_run_id=run_id,
        source_event_id=event_id,
        approved=False,
        source_snapshot_id=source.ids["impact_analysis"],
        title="Targeted tests generated for review",
        base_project_revision=revision,
        idempotency_key=application.claim.operation_id,
        pending_writes=writes,
        metadata=metadata,
        transaction_guard=commit,
    )
