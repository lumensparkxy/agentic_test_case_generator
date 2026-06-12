from __future__ import annotations

import re
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
    TestStep,
)
from .versioning_service import persist_test_case_versions
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


def _normalize_identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    return normalized[:32] or "ITEM"


def _append_tag(tags: Optional[list[str]], *new_tags: str) -> list[str]:
    merged = list(tags or [])
    for tag in new_tags:
        if tag and tag not in merged:
            merged.append(tag)
    return merged


def _append_note(value: Optional[str], note: str) -> str:
    existing = str(value or "").strip()
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}\n\n{note}"


def _changed_item_by_id(analysis: ImpactAnalysisResult) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in analysis.changed_items:
        mapping[item.item_id] = item
        if item.requirement_id:
            mapping.setdefault(item.requirement_id, item)
        for scenario_id in item.scenario_ids:
            mapping[scenario_id] = item
    return mapping


def _requirement_texts(project: QaProjectDetail) -> dict[str, str]:
    payload = project.current_snapshots.get("requirements").payload if project.current_snapshots.get("requirements") else {}
    requirements = payload.get("requirements") or []
    result: dict[str, str] = {}
    for item in requirements:
        if not isinstance(item, dict):
            continue
        req_id = str(item.get("id") or "").strip()
        if req_id:
            result[req_id] = str(item.get("text") or "").strip()
    return result


def _new_test_case_id(existing_ids: set[str], requirement_id: str, index: int) -> str:
    base_id = f"TC-IMPACT-{_normalize_identifier(requirement_id)}"
    candidate = base_id if index == 1 else f"{base_id}-{index:02d}"
    while candidate in existing_ids:
        index += 1
        candidate = f"{base_id}-{index:02d}"
    existing_ids.add(candidate)
    return candidate


def _build_added_test_case(
    *,
    recommendation: ImpactRecommendation,
    requirement_texts: dict[str, str],
    changed_items: dict[str, Any],
    existing_ids: set[str],
    add_index: int,
) -> TestCase:
    requirement_id = recommendation.requirement_id or recommendation.use_case_id or "REQ-IMPACT"
    changed_item = (
        changed_items.get(requirement_id) or changed_items.get(recommendation.use_case_id or "") or changed_items.get(recommendation.recommendation_id)
    )
    requirement_text = requirement_texts.get(requirement_id) or getattr(changed_item, "current_text", None) or recommendation.reason
    scenario_refs = list(recommendation.scenario_refs or getattr(changed_item, "scenario_ids", []) or [])
    case_id = _new_test_case_id(existing_ids, requirement_id, add_index)
    return TestCase(
        id=case_id,
        title=f"Impact coverage for {requirement_id}",
        description=f"Validates the changed requirement/use-case slice: {requirement_text}",
        priority="High",
        type="Regression",
        status="Ready",
        preconditions="The changed requirement has been approved for impact update.",
        steps=[
            TestStep(
                step=1, action=f"Review the implemented behavior for {requirement_id}.", expected="The behavior reflects the approved changed requirement."
            ),
            TestStep(
                step=2,
                action="Execute the primary user path and affected validation/error handling paths.",
                expected="The changed behavior works without regressing preserved coverage.",
            ),
        ],
        expected_result="The impacted behavior satisfies the approved requirement and does not invalidate preserved tests.",
        automation_status="Manual",
        tags=_append_tag([], "impact:add", f"requirement:{requirement_id}"),
        linked_requirement_ids=[requirement_id] if requirement_id else [],
        scenario_refs=scenario_refs,
    )


def _apply_recommendations(
    *,
    existing_test_cases: list[TestCase],
    analysis: ImpactAnalysisResult,
    accepted_recommendation_ids: Optional[list[str]],
    requirement_texts: dict[str, str],
) -> ImpactUpdateApplyResult:
    if accepted_recommendation_ids is None:
        accepted_ids = {recommendation.recommendation_id for recommendation in analysis.recommendations if recommendation.accepted}
    else:
        accepted_ids = set(accepted_recommendation_ids)
    accepted = [recommendation for recommendation in analysis.recommendations if recommendation.recommendation_id in accepted_ids]
    by_case: dict[str, list[ImpactRecommendation]] = {}
    add_recommendations: list[ImpactRecommendation] = []
    for recommendation in accepted:
        if recommendation.action == "add":
            add_recommendations.append(recommendation)
        elif recommendation.test_case_id:
            by_case.setdefault(recommendation.test_case_id, []).append(recommendation)

    changed_items = _changed_item_by_id(analysis)
    next_cases: list[TestCase] = []
    updated_count = 0
    deprecated_count = 0
    preserved_count = 0
    existing_ids = {test_case.id for test_case in existing_test_cases}
    for test_case in existing_test_cases:
        recommendations = by_case.get(test_case.id, [])
        deprecate = next((item for item in recommendations if item.action == "deprecate"), None)
        update = next((item for item in recommendations if item.action == "update"), None)
        if deprecate is not None:
            deprecated_count += 1
            next_cases.append(
                test_case.model_copy(
                    update={
                        "status": "Deprecated",
                        "description": _append_note(test_case.description, f"Impact update deprecated this case: {deprecate.reason}"),
                        "tags": _append_tag(test_case.tags, "impact:deprecated"),
                    }
                )
            )
        elif update is not None:
            updated_count += 1
            next_cases.append(
                test_case.model_copy(
                    update={
                        "status": "Ready",
                        "description": _append_note(test_case.description, f"Impact update required: {update.reason}"),
                        "tags": _append_tag(test_case.tags, "impact:update"),
                    }
                )
            )
        else:
            preserved_count += 1
            next_cases.append(test_case)

    added_count = 0
    for index, recommendation in enumerate(add_recommendations, start=1):
        added_count += 1
        next_cases.append(
            _build_added_test_case(
                recommendation=recommendation,
                requirement_texts=requirement_texts,
                changed_items=changed_items,
                existing_ids=existing_ids,
                add_index=index,
            )
        )

    return ImpactUpdateApplyResult(
        test_cases=next_cases,
        applied_recommendation_ids=[recommendation.recommendation_id for recommendation in accepted],
        preserved_count=preserved_count,
        updated_count=updated_count,
        added_count=added_count,
        deprecated_count=deprecated_count,
    )


def _require_apply_approval(analysis: ImpactAnalysisResult) -> None:
    unapproved = [item.item_id for item in analysis.changed_items if item.change_type in {"added", "modified"} and not item.approved]
    if unapproved:
        raise ImpactWorkflowError(f"Approve changed requirements/use cases before applying impact updates: {', '.join(unapproved)}")


def apply_project_impact_update(
    *,
    project_id: str,
    actor: AuthUser,
    request_id: str,
    accepted_recommendation_ids: Optional[list[str]] = None,
    base_project_revision: Optional[int] = None,
) -> QaProjectDetail:
    project = get_project(project_id, actor=actor)
    if base_project_revision is not None and int(base_project_revision) != int(project.current_revision):
        raise ProjectConflictError(project.current_revision)
    impact_snapshot = project.current_snapshots.get("impact_analysis")
    test_cases_snapshot = project.current_snapshots.get("test_cases")
    if test_cases_snapshot is None:
        raise ImpactWorkflowError("Generate an initial test-case suite before applying impact updates.")
    analysis = _analysis_from_snapshot(impact_snapshot)
    _require_apply_approval(analysis)
    existing_test_cases = _test_cases_from_snapshot(test_cases_snapshot)
    apply_result = _apply_recommendations(
        existing_test_cases=existing_test_cases,
        analysis=analysis,
        accepted_recommendation_ids=accepted_recommendation_ids,
        requirement_texts=_requirement_texts(project),
    )
    versioned_test_cases = persist_test_case_versions(
        current_test_cases=apply_result.test_cases,
        previous_test_cases=existing_test_cases,
        actor=actor,
        request_id=request_id,
        workflow_run_id=None,
        source_event_id=None,
        operation="impact.update.apply",
        approved=True,
        reuse_unchanged_versions=True,
    )
    current_use_cases_payload = project.current_snapshots.get("use_cases").payload if project.current_snapshots.get("use_cases") else {}
    snapshot_payload = {
        "test_cases": _model_payload(versioned_test_cases),
        "approved": True,
        "impact_analysis": analysis.model_dump(mode="json"),
        "impact_update_result": apply_result.model_dump(mode="json", exclude={"test_cases"}),
        "review": {
            "approved": True,
            "score": 100,
            "threshold": 0,
            "summary": "Impact update applied to accepted recommendations.",
            "blocking_issues": [],
            "suggestions": [],
            "unmet_criteria": [],
        },
        "coverage_plan": _model_payload(current_use_cases_payload.get("coverage_plan") or []),
        "requirement_analysis": _model_payload(current_use_cases_payload.get("requirement_analysis") or []),
        "coverage_metrics": _model_payload(current_use_cases_payload.get("coverage_metrics") or {}),
        "workflow_settings": _model_payload(current_use_cases_payload.get("workflow_settings") or {}),
        "workflow_diagnostics": {
            "status": "completed",
            "used_fallback": False,
            "failure_reason": None,
            "timed_out": False,
            "stalled": False,
            "max_iterations_reached": False,
            "parser_failures": [],
            "warnings": [],
            "best_iteration": None,
            "attempt_count": 1,
        },
    }
    append_stage_snapshot(
        project_id=project_id,
        stage="test_cases",
        payload=snapshot_payload,
        operation="impact.update.apply",
        actor=actor,
        request_id=request_id,
        approved=True,
        source_snapshot_id=impact_snapshot.snapshot_id if impact_snapshot else None,
        title="Impact update applied",
        metadata={
            "test_case_count": len(versioned_test_cases),
            "preserved_count": apply_result.preserved_count,
            "updated_count": apply_result.updated_count,
            "added_count": apply_result.added_count,
            "deprecated_count": apply_result.deprecated_count,
            "applied_recommendation_ids": apply_result.applied_recommendation_ids,
            "source_snapshot_ids": {
                "requirements": project.current_snapshots.get("requirements").snapshot_id if project.current_snapshots.get("requirements") else None,
                "context": project.current_snapshots.get("context").snapshot_id if project.current_snapshots.get("context") else None,
                "use_cases": project.current_snapshots.get("use_cases").snapshot_id if project.current_snapshots.get("use_cases") else None,
                "impact_analysis": impact_snapshot.snapshot_id if impact_snapshot else None,
            },
        },
    )
    return get_project(project_id, actor=actor)


def impact_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ImpactWorkflowError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return project_error_to_http(exc)
