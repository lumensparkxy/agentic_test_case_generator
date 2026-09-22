from __future__ import annotations

from uuid import uuid4
from hashlib import sha256
import json
import logging
from typing import Any, Optional

from fastapi import HTTPException, status

from ..agents.impact_update_agent import analyze_impact
from ..models import (
    AuthUser,
    ImpactAnalysisResult,
    ImpactRecommendation,
    ImpactUpdateApplyResult,
    QaProjectDetail,
    QaProjectStageSnapshot,
    TestCase,
    Requirement,
    GenerateTestCasesInput,
    GenerateTestCasesResponse,
    TestCaseTemplate,
    EnrichInput,
)
from .impact_application_state import ApplicationLease
from .versioning_service import persist_test_case_versions
from .scenario_review_state import current_scenario_reviews, scenario_key
from ..agents.test_case_agent import generate_test_cases
from .test_case_quality import placeholder_reason, separate_generation_work, delivered_coverage
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope, public_manifest
from .billing_service import enforce_billing_access, record_billing_consumption
from .audit_service import start_workflow_run, complete_workflow_run, record_usage_event
from .workflow_project_service import (
    ProjectConflictError,
    append_stage_snapshot,
    get_project,
    get_project_stage_snapshot,
    project_error_to_http,
)


class ImpactWorkflowError(RuntimeError):
    pass


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_payload(item) for key, item in value.items()}
    return value


def _load_snapshot_by_id(project_id: str, snapshot_id: Optional[str], *, actor: AuthUser) -> Optional[QaProjectStageSnapshot]:
    if not snapshot_id:
        return None
    return get_project_stage_snapshot(project_id, snapshot_id, actor=actor)


def _baseline_snapshot_ids(test_cases_snapshot: Optional[QaProjectStageSnapshot]) -> dict[str, Optional[str]]:
    if test_cases_snapshot is None:
        return {"requirements": None, "context": None, "use_cases": None}
    metadata = dict(test_cases_snapshot.metadata or {})
    nested = metadata.get("source_snapshot_ids")
    if isinstance(nested, dict):
        return {
            "requirements": nested.get("requirements") or metadata.get("source_requirements_snapshot_id"),
            "context": nested.get("context") or metadata.get("source_context_snapshot_id"),
            "use_cases": nested.get("use_cases") or metadata.get("source_use_case_snapshot_id") or test_cases_snapshot.source_snapshot_id,
        }
    return {
        "requirements": metadata.get("source_requirements_snapshot_id"),
        "context": metadata.get("source_context_snapshot_id"),
        "use_cases": metadata.get("source_use_case_snapshot_id") or test_cases_snapshot.source_snapshot_id,
    }


def _build_analysis(
    *,
    project: QaProjectDetail,
    actor: AuthUser,
) -> ImpactAnalysisResult:
    current_requirements_snapshot = project.current_snapshots.get("requirements")
    current_context_snapshot = project.current_snapshots.get("context")
    current_use_cases_snapshot = project.current_snapshots.get("use_cases")
    test_cases_snapshot = project.current_snapshots.get("test_cases")
    baseline_ids = _baseline_snapshot_ids(test_cases_snapshot)
    baseline_requirements_snapshot = _load_snapshot_by_id(project.project_id, baseline_ids.get("requirements"), actor=actor)
    baseline_context_snapshot = _load_snapshot_by_id(project.project_id, baseline_ids.get("context"), actor=actor)
    baseline_use_cases_snapshot = _load_snapshot_by_id(project.project_id, baseline_ids.get("use_cases"), actor=actor)
    return analyze_impact(
        current_requirements_snapshot=current_requirements_snapshot,
        current_use_cases_snapshot=current_use_cases_snapshot,
        current_context_snapshot=current_context_snapshot,
        baseline_requirements_snapshot=baseline_requirements_snapshot,
        baseline_use_cases_snapshot=baseline_use_cases_snapshot,
        baseline_context_snapshot=baseline_context_snapshot,
        test_cases_snapshot=test_cases_snapshot,
    )


def analyze_project_impact(
    *,
    project_id: str,
    actor: AuthUser,
    request_id: str,
    base_project_revision: Optional[int] = None,
) -> QaProjectDetail:
    project = get_project(project_id, actor=actor)
    if not project.current_snapshots.get("test_cases"):
        raise ImpactWorkflowError("Generate an initial test-case suite before running impact analysis.")
    analysis = _build_analysis(project=project, actor=actor)
    tasks = project.current_snapshots["test_cases"].payload.get("generation_tasks") or []
    original = get_project_stage_snapshot(project_id, project.current_snapshots["test_cases"].snapshot_id, actor=actor)
    saved_ids = {row["id"] for row in original.payload.get("test_cases", [])}
    repair_ids = {task.get("source_test_case_id") for task in tasks if task.get("source_test_case_id")}
    analysis.recommendations = [r for r in analysis.recommendations if r.test_case_id not in repair_ids]
    for index, task in enumerate(tasks):
        for req_id in task.get("requirement_ids") or []:
            analysis.recommendations.append(
                ImpactRecommendation(
                    recommendation_id=f"repair-{index}-{req_id}",
                    action="update" if task.get("source_test_case_id") in saved_ids else "add",
                    title=f"Generate concrete coverage for {req_id}",
                    reason=task["reason"],
                    accepted=False,
                    requirement_id=req_id,
                    test_case_id=task.get("source_test_case_id") if task.get("source_test_case_id") in saved_ids else None,
                    scenario_refs=task.get("scenario_refs") or [],
                )
            )
    analysis.summary.recommendation_counts = {
        action: sum(r.action == action for r in analysis.recommendations) for action in ("keep", "update", "add", "deprecate")
    }
    snapshot = append_stage_snapshot(
        project_id=project_id,
        stage="impact_analysis",
        payload=analysis.model_dump(mode="json"),
        operation="impact.analysis",
        actor=actor,
        request_id=request_id,
        approved=False,
        source_snapshot_id=project.current_snapshots.get("test_cases").snapshot_id if project.current_snapshots.get("test_cases") else None,
        title=f"Impact analysis: {analysis.summary.changed_item_count} changed item(s)",
        metadata={
            "changed_item_count": analysis.summary.changed_item_count,
            "directly_impacted_test_case_count": analysis.summary.directly_impacted_test_case_count,
            "semantic_neighbor_count": analysis.summary.semantic_neighbor_count,
            "recommendation_counts": analysis.summary.recommendation_counts,
        },
        base_project_revision=base_project_revision,
    )
    updated_project = get_project(project_id, actor=actor)
    updated_project.current_snapshots["impact_analysis"] = snapshot
    return updated_project


def _test_cases_from_snapshot(snapshot: Optional[QaProjectStageSnapshot]) -> list[TestCase]:
    payload = dict(snapshot.payload or {}) if snapshot else {}
    return [TestCase.model_validate(item) for item in payload.get("test_cases") or [] if isinstance(item, dict)]


def _analysis_from_snapshot(snapshot: Optional[QaProjectStageSnapshot]) -> ImpactAnalysisResult:
    if snapshot is None:
        raise ImpactWorkflowError("Run impact analysis before applying an impact update.")
    return ImpactAnalysisResult.model_validate(snapshot.payload or {})


def _require_apply_approval(analysis: ImpactAnalysisResult) -> None:
    unapproved = [item.item_id for item in analysis.changed_items if item.change_type in {"added", "modified"} and not item.approved]
    if unapproved:
        raise ImpactWorkflowError(f"Approve changed requirements/use cases before applying impact updates: {', '.join(unapproved)}")


def apply_project_impact_update(
    *,
    project_id: str,
    actor: AuthUser,
    request_id: str,
    analysis_snapshot_id: Optional[str] = None,
    accepted_recommendation_ids: Optional[list[str]] = None,
    base_project_revision: Optional[int] = None,
) -> QaProjectDetail:
    project = get_project(project_id, actor=actor)
    current_analysis = project.current_snapshots.get("impact_analysis")
    if analysis_snapshot_id and (not current_analysis or analysis_snapshot_id != current_analysis.snapshot_id):
        # An old tab must never apply its selection to a newly generated analysis.
        raise ImpactWorkflowError("Impact analysis changed. Reload the project before applying.")
    if project.impact_application and project.impact_application.status in {"applying", "applied"}:
        return project
    if base_project_revision is None or base_project_revision != project.current_revision:
        raise ProjectConflictError(project.current_revision)
    if project.status != "active":
        raise ImpactWorkflowError("Restore the project before applying impact updates.")
    impact_snapshot = project.current_snapshots.get("impact_analysis")
    test_snapshot = project.current_snapshots.get("test_cases")
    if not test_snapshot:
        raise ImpactWorkflowError("Generate an initial test-case suite first.")
    analysis = _analysis_from_snapshot(impact_snapshot)
    if project.stage_state["impact_analysis"].stale or impact_snapshot.source_snapshot_id != test_snapshot.snapshot_id:
        raise ImpactWorkflowError("Impact analysis is stale. Analyze impact again.")
    for stage in ("requirements", "context", "use_cases"):
        expected = analysis.current_snapshot_ids.get(stage)
        current = project.current_snapshots.get(stage)
        if expected != (current.snapshot_id if current else None):
            raise ImpactWorkflowError("Inputs changed after impact analysis. Analyze impact again.")
    _require_apply_approval(analysis)
    known = {r.recommendation_id for r in analysis.recommendations}
    selected = (
        set(accepted_recommendation_ids) if accepted_recommendation_ids is not None else {r.recommendation_id for r in analysis.recommendations if r.accepted}
    )
    if not selected or not selected <= known:
        raise ImpactWorkflowError("Select current impact recommendations before applying.")
    accepted = [r for r in analysis.recommendations if r.recommendation_id in selected]
    # Read immutable originals, including legacy placeholders hidden by the current-project view.
    original_snapshot = get_project_stage_snapshot(project_id, test_snapshot.snapshot_id, actor=actor)
    existing = _test_cases_from_snapshot(original_snapshot)
    by_id = {case.id: case for case in existing}
    requirements = {r["id"]: Requirement.model_validate(r) for r in project.current_snapshots["requirements"].payload.get("requirements", [])}
    plans = project.current_snapshots.get("use_cases")
    plan_by_req = {p["requirement_id"]: p for p in (plans.payload.get("coverage_plan", []) if plans else [])}
    pending = [r for r in accepted if r.action in {"add", "update"}]
    if len(pending) > 40:
        raise ImpactWorkflowError("Select at most 40 generation recommendations per update.")
    inputs = []
    seen_updates = set()
    seen_add_refs = set()
    for rec in pending:
        if rec.action == "update" and rec.test_case_id in seen_updates:
            continue
        if rec.action == "update":
            seen_updates.add(rec.test_case_id)
        old = by_id.get(rec.test_case_id) if rec.test_case_id else None
        related = [r for r in pending if old and r.test_case_id == old.id] if old else [rec]
        ids = list(dict.fromkeys((old.linked_requirement_ids if old else []) + [r.requirement_id for r in related if r.requirement_id]))
        if not ids or any(i not in requirements or requirements[i].review_status != "Approved" or requirements[i].lifecycle_status != "active" for i in ids):
            raise ImpactWorkflowError("Approve the affected current requirements before generating tests.")
        state = project.stage_state.get("use_cases")
        req_state = project.stage_state.get("requirements")
        if not state or not state.approved or state.stale or not req_state.approved or req_state.stale:
            raise ImpactWorkflowError("Approve current requirements and regenerate/review current Use Cases before applying test updates.")
        if rec.action == "update" and old is None:
            raise ImpactWorkflowError("A selected test case no longer exists. Analyze impact again.")
        current_refs = {sc["id"] for req_id in ids for sc in plan_by_req.get(req_id, {}).get("scenarios", [])}
        refs = {ref for r in related for ref in r.scenario_refs} | (set(old.scenario_refs if old else []) & current_refs)
        selected_plans = []
        for req_id in ids:
            plan = plan_by_req.get(req_id)
            if not plan:
                raise ImpactWorkflowError("Generate and review Use Cases for the affected requirements first.")
            scenarios = [item for item in plan["scenarios"] if not refs or item["id"] in refs]
            if scenarios:
                selected_plans.append({**plan, "scenarios": scenarios})
        available = {item["id"] for plan in selected_plans for item in plan["scenarios"]}
        if not available or (refs and not refs <= available):
            raise ImpactWorkflowError("Selected scenarios no longer match current Use Cases. Analyze impact again.")
        if rec.action == "add":
            available -= seen_add_refs
            if not available:
                continue
            seen_add_refs.update(available)
            selected_plans = [{**p, "scenarios": [sc for sc in p["scenarios"] if sc["id"] in available]} for p in selected_plans]
            selected_plans = [p for p in selected_plans if p["scenarios"]]
        reviews = current_scenario_reviews(plans.payload, state.metadata, plans.snapshot_id)
        if any(reviews.get(scenario_key(p["requirement_id"], sc["id"]), {}).get("status") != "approved" for p in selected_plans for sc in p["scenarios"]):
            raise ImpactWorkflowError("Review and approve the affected Use Cases before generating targeted tests.")
        reqs = [requirements[i] for i in ids]
        context = project.current_snapshots.get("context")
        payload = GenerateTestCasesInput(
            requirements=reqs,
            coverage_plan=selected_plans,
            context=EnrichInput.model_validate({**(context.payload if context else {}), "requirements": reqs}),
            template=TestCaseTemplate(name="Targeted tests", format="json", fields=["id", "title", "steps", "expected_result"]),
            project_id=project_id,
            base_project_revision=base_project_revision,
            feedback="Generate concrete user/API actions, necessary input values, and observable assertions. Do not return instructions to review or generate tests.",
        )
        inputs.append((rec, old, payload, available))
    lease = ApplicationLease(project_id, impact_snapshot.snapshot_id, actor, base_project_revision, selected)
    if not lease.reserve():
        return get_project(project_id, actor=actor)
    with lease:
        try:
            billing_context = enforce_billing_access(current_user=actor, billing_key="testcases.generate") if pending else None
            run_id = start_workflow_run(operation="impact.update.apply", actor=actor, request_id=request_id, metadata={"project_id": project_id})
            replacements = {}
            additions = []
            generated_count = 0
            evidence = []
            try:
                for rec, old, payload, refs in inputs:
                    manifest = prepare_run(
                        "test_cases",
                        actor,
                        project_id,
                        f"{request_id}:{rec.recommendation_id}",
                        requirement_ids=[r.id for r in payload.requirements],
                        input_fingerprint=sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()).hexdigest(),
                    )
                    with guidance_scope(manifest):
                        response = GenerateTestCasesResponse.model_validate(
                            generate_test_cases(
                                payload, actor_user_id=actor.sub, request_id=request_id, workflow_run_id=run_id, operation="impact.update.apply"
                            )
                        )
                    evidence.append(
                        {
                            "recommendation_id": rec.recommendation_id,
                            "generation_evidence": _model_payload(response.generation_evidence),
                            "guidance": public_manifest(manifest),
                        }
                    )
                    generated = response.test_cases
                    covered = {ref for case in generated for ref in case.scenario_refs}
                    allowed = {r.id for r in payload.requirements}
                    if (
                        not response.approved
                        or not generated
                        or response.generation_tasks
                        or response.workflow_diagnostics.used_fallback
                        or response.workflow_diagnostics.timed_out
                        or response.workflow_diagnostics.failed_shard_count
                        or any(
                            placeholder_reason(c)
                            or not c.linked_requirement_ids
                            or not set(c.linked_requirement_ids) <= allowed
                            or not c.scenario_refs
                            or not set(c.scenario_refs) <= refs
                            for c in generated
                        )
                        or not refs <= covered
                    ):
                        raise ImpactWorkflowError("Targeted generation is incomplete or failed quality checks. The existing suite was preserved.")
                    for index, case in enumerate(generated):
                        change = {
                            "id": old.id if old and index == 0 else f"TC-{uuid4().hex[:12].upper()}",
                            "status": "In Review",
                            "tags": list(dict.fromkeys((case.tags or []) + [f"impact:{rec.action}"])),
                        }
                        if old and index == 0:
                            change.update(
                                {key: getattr(old, key) for key in ("artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number")}
                            )
                            replacements[old.id] = case.model_copy(update=change)
                        else:
                            additions.append(case.model_copy(update=change))
                        generated_count += 1
                deprecated = {r.test_case_id for r in accepted if r.action == "deprecate"}
                next_cases = [replacements.get(c.id, c.model_copy(update={"status": "Deprecated"}) if c.id in deprecated else c) for c in existing]
                next_cases += additions
                if get_project(project_id, actor=actor).current_revision != base_project_revision:
                    raise ProjectConflictError(get_project(project_id, actor=actor).current_revision)
                pending_writes = []
                versioned = persist_test_case_versions(
                    current_test_cases=next_cases,
                    previous_test_cases=existing,
                    actor=actor,
                    request_id=request_id,
                    workflow_run_id=run_id,
                    source_event_id=None,
                    operation="impact.update.apply",
                    approved=False,
                    reuse_unchanged_versions=True,
                    pending_writes=pending_writes,
                )
                concrete_cases, remaining_tasks = separate_generation_work(versioned, plans.payload.get("coverage_plan", []) if plans else [])
                result = ImpactUpdateApplyResult(
                    test_cases=versioned,
                    applied_recommendation_ids=sorted(selected),
                    changed_test_case_ids=sorted(set(replacements) | {c.id for c in additions} | deprecated),
                    preserved_count=len(existing) - len(replacements) - len(deprecated),
                    updated_count=len(replacements),
                    added_count=len(additions),
                    deprecated_count=len(deprecated),
                )
                snapshot_payload = {
                    **original_snapshot.payload,
                    "test_cases": _model_payload(versioned),
                    "approved": False,
                    "generation_tasks": remaining_tasks,
                    "impact_analysis": analysis.model_dump(mode="json"),
                    "impact_update_result": result.model_dump(mode="json", exclude={"test_cases"}),
                    "review": {
                        "approved": False,
                        "score": 0,
                        "threshold": 90,
                        "summary": "Targeted changes require suite review.",
                        "blocking_issues": ["Review the changed test suite before export."],
                        "suggestions": [],
                        "unmet_criteria": [],
                    },
                    "coverage_plan": plans.payload.get("coverage_plan", []) if plans else [],
                    "coverage_metrics": delivered_coverage(separate_generation_work(versioned)[0], plans.payload.get("coverage_plan", []) if plans else []),
                    "workflow_diagnostics": {"status": "completed", "used_fallback": False},
                    "generation_evidence": {"operation": "impact.update.apply", "final_test_case_count": len(concrete_cases), "final_status": "needs_review"},
                }
                event_id = None
                if generated_count:
                    event_id = record_usage_event(
                        event_type="testcases.generated",
                        billing_key="testcases.generate",
                        quantity=generated_count,
                        unit="test_case",
                        actor=actor,
                        request_id=request_id,
                        workflow_run_id=run_id,
                        status="completed",
                        event_id=f"impact-{project_id}-{impact_snapshot.snapshot_id}",
                        pending_writes=pending_writes,
                        firestore_client=lease.client,
                    )
                append_stage_snapshot(
                    transaction_guard=lease.commit,
                    project_id=project_id,
                    stage="test_cases",
                    payload=snapshot_payload,
                    operation="impact.update.apply",
                    actor=actor,
                    request_id=request_id,
                    workflow_run_id=run_id,
                    approved=False,
                    source_snapshot_id=impact_snapshot.snapshot_id,
                    title="Targeted tests generated for review",
                    base_project_revision=base_project_revision,
                    pending_writes=pending_writes,
                    metadata={
                        "test_case_count": len(concrete_cases),
                        "preserved_count": result.preserved_count,
                        "updated_count": result.updated_count,
                        "added_count": result.added_count,
                        "deprecated_count": result.deprecated_count,
                        "source_snapshot_ids": analysis.current_snapshot_ids,
                        "targeted_generation_runs": evidence,
                    },
                )
            except Exception:
                complete_workflow_run(run_id, status="failed", error_message="Targeted generation or commit failed")
                raise
            complete_workflow_run(run_id, status="completed", metadata={"generated_count": generated_count})
            if generated_count:
                try:
                    record_billing_consumption(
                        current_user=actor,
                        billing_context=billing_context,
                        source_event_id=event_id,
                        request_id=request_id,
                        workflow_run_id=run_id,
                        billing_key="testcases.generate",
                        quantity=generated_count,
                        unit="test_case",
                    )
                except Exception as exc:
                    logging.warning("Impact generation billing recording failed: %s", exc)
            return get_project(project_id, actor=actor)
        except Exception:
            # If commit succeeded but its response was lost, fail() cannot overwrite
            # the atomic receipt. The client reconciles through a project read.
            lease.fail()
            raise


def impact_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ImpactWorkflowError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return project_error_to_http(exc)
