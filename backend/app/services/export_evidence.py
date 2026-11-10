"""Bind portable export evidence to the exact saved suite, never client approval."""

from datetime import datetime, timezone
import os

from fastapi import HTTPException
from pydantic import ValidationError

from ..contracts.exports import DesignDeliveryEvidence, ExportAuditMetadata, ExportTestCasesInput
from ..contracts.projects import QaProjectDetail, QaProjectStageSnapshot
from ..contracts.requirements import Requirement, ReviewResult
from ..contracts.test_cases import TestCase
from .suite_quality import DESIGN_REASSESSMENT_REQUIRED
from .test_case_quality import placeholder_reason


def _conflict(message: str) -> None:
    raise HTTPException(409, message)


def design_delivery_enabled() -> bool:
    return os.getenv("TCG_DESIGN_DELIVERY_ENABLED") == "true"


def reviewed_design_available(project: QaProjectDetail, *, raw_suite: QaProjectStageSnapshot) -> bool:
    quality = project.suite_quality
    if (
        not project.design_delivery_enabled
        or project.status != "active"
        or not quality
        or quality.design_status != "complete"
        or quality.source_freshness != "current"
    ):
        return False
    try:
        payload = ExportTestCasesInput(
            project_id=project.project_id,
            base_project_revision=project.current_revision,
            source_snapshot_id=raw_suite.snapshot_id,
            test_cases=raw_suite.payload.get("test_cases", []),
            delivery_intent="reviewed_design",
        )
        return build_export_evidence(payload, project).design_delivery is not None
    except HTTPException, ValidationError:
        return False


def _require_design_request(payload, project, *, allow_saved_design):
    if not allow_saved_design and not design_delivery_enabled():
        raise HTTPException(409, {"code": "design_delivery_disabled", "message": "Reviewed design export is disabled."})
    if payload.draft_override_requested or (payload.draft_override_reason or "").strip():
        raise HTTPException(
            422, {"code": "design_delivery_conflicting_intent", "message": "Choose reviewed design or a reasoned draft export for this request."}
        )
    if not project or payload.project_id != project.project_id or not payload.source_snapshot_id or payload.base_project_revision is None:
        raise HTTPException(
            422,
            {
                "code": "design_delivery_requires_saved_suite",
                "message": "Reviewed design export requires the exact saved suite and current project revision.",
            },
        )


def _design_delivery_evidence(project, snapshot, active_cases, case_count, source_ids, unresolved, human_status, *, legacy_approved):
    # Project Detail derives this projection from the raw immutable suite before
    # hiding legacy placeholders from the workbench's case rows.
    quality = project.suite_quality
    if (
        not quality
        or quality.suite_snapshot_id != snapshot.snapshot_id
        or quality.source_freshness != "current"
        or quality.design_status != "complete"
        or quality.scenario_decision != "approved"
        or quality.scenario_snapshot_id != source_ids.get("use_cases")
        or not quality.scenario_snapshot_id
        or not quality.scenario_review_id
        or not source_ids.get("requirements")
        or quality.human_acceptance == "changes_requested"
        or human_status == "changes_requested"
        or unresolved != 0
        or not quality.required_requirement_ids
        or not active_cases
        or quality.active_case_count != len(active_cases)
        or quality.deprecated_case_count != case_count - len(active_cases)
    ):
        raise HTTPException(
            409,
            {"code": "design_delivery_unavailable", "message": "Review the current saved suite and its approved sources before delivering this design."},
        )
    return DesignDeliveryEvidence(
        legacy_approved=legacy_approved,
        suite_snapshot_id=quality.suite_snapshot_id,
        scenario_snapshot_id=quality.scenario_snapshot_id,
        scenario_review_id=quality.scenario_review_id,
        human_acceptance=quality.human_acceptance,
        declared_setup=quality.declared_setup,
        required_requirement_ids=quality.required_requirement_ids,
        deferred_requirement_ids=quality.deferred_requirement_ids,
        excluded_requirement_ids=quality.excluded_requirement_ids,
        active_case_count=quality.active_case_count,
        deprecated_case_count=quality.deprecated_case_count,
    )


def build_export_evidence(payload: ExportTestCasesInput, project: QaProjectDetail | None, *, allow_saved_design: bool = False) -> ExportAuditMetadata:
    design_requested = payload.delivery_intent == "reviewed_design"
    if design_requested:
        _require_design_request(payload, project, allow_saved_design=allow_saved_design)
    if any(
        placeholder_reason(case)
        or (design_requested and case.status == "Deprecated" and placeholder_reason(case.model_copy(update={"status": "Draft"}), strict=False))
        for case in payload.test_cases
    ):
        raise HTTPException(422, "Unfinished generation instructions cannot be exported as test cases. Generate concrete tests first.")
    snapshot = None
    state = None
    if project:
        if payload.base_project_revision != project.current_revision:
            _conflict("Project has changed. Reload before exporting the current suite.")
        snapshot = project.current_snapshots.get("test_cases")
        state = project.stage_state.get("test_cases")
        if snapshot:
            if not state or state.current_snapshot_id != snapshot.snapshot_id or state.stale:
                _conflict("The saved test suite is stale or cannot be verified. Refresh and review it before exporting.")
            if payload.source_snapshot_id and payload.source_snapshot_id != snapshot.snapshot_id:
                _conflict("The requested test-case snapshot is no longer current. Reload before exporting.")
            try:
                saved_cases = [TestCase.model_validate(case).model_dump(mode="json") for case in snapshot.payload.get("test_cases", [])]
            except ValidationError, TypeError, ValueError:
                _conflict("The saved test-case snapshot cannot be verified.")
            if saved_cases != [case.model_dump(mode="json") for case in payload.test_cases]:
                _conflict("Export only the exact current saved suite. Reload to recover its cases and review.")
        elif payload.source_snapshot_id or (state and state.current_snapshot_id):
            _conflict("The requested test-case snapshot is unavailable. Reload before exporting.")
    elif payload.source_snapshot_id:
        _conflict("A project is required to verify the requested test-case snapshot.")

    saved = snapshot.payload if snapshot else {}
    metadata = snapshot.metadata if snapshot else {}
    quality = project.suite_quality if snapshot else None
    reassessment_required = quality and quality.suite_snapshot_id == snapshot.snapshot_id and DESIGN_REASSESSMENT_REQUIRED in quality.reason_codes
    review = None
    # Leave the superseded approval on the saved suite and its historical reports.
    if not reassessment_required and isinstance(saved.get("review"), dict) and isinstance(saved["review"].get("approved"), bool):
        try:
            review = ReviewResult.model_validate(saved["review"])
        except ValidationError:
            pass
    human = state.metadata.get("latest_human_review") if state else None
    human_status = "unavailable"
    if snapshot and isinstance(human, dict) and human.get("snapshot_id") == snapshot.snapshot_id:
        human_status = {"approve": "approved", "request_changes": "changes_requested"}.get(human.get("decision"), "unavailable")
    tasks = saved.get("generation_tasks")
    unresolved = len(tasks) if isinstance(tasks, list) else None
    suite_complete = not tasks if isinstance(tasks, list) else None

    # Only allowlisted, artifact-bound sources are exported. Older unbound sources
    # remain unavailable instead of borrowing the project's latest requirements.
    source_ids = {}
    source_mappings = []
    requirements = None
    for stage, key in (
        ("requirements", "source_requirements_snapshot_id"),
        ("context", "source_context_snapshot_id"),
        ("use_cases", "source_use_case_snapshot_id"),
    ):
        recorded_ids = metadata.get("source_snapshot_ids")
        recorded_ids = recorded_ids if isinstance(recorded_ids, dict) else {}
        source_id = metadata.get(key) or recorded_ids.get(stage)
        current = project.current_snapshots.get(stage) if project else None
        current_state = project.stage_state.get(stage) if project else None
        if source_id and (not current or current.snapshot_id != source_id or (current_state and current_state.stale)):
            _conflict("The suite references older upstream inputs. Refresh and review it before exporting.")
        if source_id and current and current.snapshot_id == source_id:
            source_ids[stage] = source_id
            if stage == "requirements":
                try:
                    requirements = [Requirement.model_validate(item) for item in current.payload.get("requirements", [])]
                except ValidationError, TypeError, ValueError:
                    requirements = None
    if snapshot:
        source_ids["test_cases"] = snapshot.snapshot_id
    for requirement in requirements or []:
        source_mappings.append(
            {
                "normalized_requirement_id": requirement.id,
                "original_requirement_ids": requirement.original_requirement_ids,
                "sources": [
                    {
                        "source_id": source.source_id,
                        "source_version": source.source_version,
                        "import_id": source.import_id,
                        "original_requirement_ids": source.original_requirement_ids,
                        "excerpt": source.excerpt,
                        "excerpt_verified": source.excerpt_verified,
                        "source_section": source.source_section,
                    }
                    for source in requirement.sources
                ],
            }
        )

    plan = saved.get("coverage_plan")
    expected_requirements = {item.id for item in requirements} if requirements is not None else None
    if expected_requirements is None and isinstance(plan, list) and plan:
        expected_requirements = {item["requirement_id"] for item in plan if isinstance(item, dict) and item.get("requirement_id")}
    expected_scenarios = None
    if isinstance(plan, list):
        expected_scenarios = {
            str(scenario["id"])
            for item in plan
            if isinstance(item, dict)
            for scenario in (item.get("scenarios") if isinstance(item.get("scenarios"), list) else [])
            if isinstance(scenario, dict) and scenario.get("id")
        }
    linked = {ref for case in payload.test_cases for ref in case.linked_requirement_ids}
    scenarios = {ref for case in payload.test_cases for ref in case.scenario_refs}

    def coverage(expected, actual):
        return {
            "total": len(expected) if expected is not None else None,
            "covered_count": len(expected & actual) if expected is not None else None,
            "missing_count": len(expected - actual) if expected is not None else None,
            "covered_ids": sorted(expected & actual) if expected is not None else None,
            "missing_ids": sorted(expected - actual) if expected is not None else None,
            "referenced_ids": sorted(actual),
        }

    requirements_coverage = coverage(expected_requirements, linked)
    scenarios_coverage = coverage(expected_scenarios, scenarios)
    if requirements_coverage["missing_ids"] or scenarios_coverage["missing_ids"]:
        suite_complete = False
    approved = bool(snapshot and state.approved and review and review.approved and suite_complete is True and human_status != "changes_requested")
    reason = (payload.draft_override_reason or "").strip()
    design_delivery = None
    if design_requested:
        active_cases = [case for case in payload.test_cases if case.status != "Deprecated"]
        design_delivery = _design_delivery_evidence(
            project, snapshot, active_cases, len(payload.test_cases), source_ids, unresolved, human_status, legacy_approved=approved
        )
        approved_scope = set(design_delivery.required_requirement_ids)
        active_requirements = {ref for case in active_cases for ref in case.linked_requirement_ids}
        active_scenarios = {ref for case in active_cases for ref in case.scenario_refs}
        if not expected_scenarios or active_requirements != approved_scope or active_scenarios != expected_scenarios:
            _conflict("The saved design does not cover its approved scope. Refresh and review before exporting.")
        requirements_coverage = coverage(approved_scope, active_requirements)
        scenarios_coverage = coverage(expected_scenarios, active_scenarios)
        suite_complete = True
    is_draft = not approved and design_delivery is None
    if is_draft and not (payload.draft_override_requested and reason):
        raise HTTPException(
            422,
            {
                "code": "draft_override_required",
                "message": (
                    "The saved design needs reassessment. Request a draft export and provide a nonempty reason."
                    if reassessment_required
                    else "Export is unapproved, incomplete or unverifiable. Request a draft export and provide a nonempty reason."
                ),
            },
        )

    matching_runs = [run for run in project.execution_runs if snapshot and run.source_snapshot_id == snapshot.snapshot_id] if project else []
    matching_runs.sort(key=lambda run: run.created_at, reverse=True)
    execution_status = matching_runs[0].status if matching_runs else "not_executed" if snapshot and not project.execution_runs else "unknown"
    if matching_runs and matching_runs[0].snapshot_id:
        source_ids["execution"] = matching_runs[0].snapshot_id
    artifact_sets = {case.artifact_set_id for case in payload.test_cases if case.artifact_set_id}
    return ExportAuditMetadata(
        exported_at=datetime.now(timezone.utc),
        project_id=payload.project_id,
        project_revision=project.current_revision if project else None,
        snapshot_id=snapshot.snapshot_id if snapshot else None,
        snapshot_version=snapshot.version if snapshot else None,
        artifact_set_id=next(iter(artifact_sets))
        if snapshot and len(artifact_sets) == 1 and all(case.artifact_set_id for case in payload.test_cases)
        else None,
        # Artifact versions are per case, so there is no invented suite version ID.
        artifact_version_id=None,
        case_versions=[
            {
                "case_id": case.id,
                "artifact_item_id": case.artifact_item_id,
                "artifact_version_id": case.artifact_version_id,
                "artifact_version_number": case.artifact_version_number,
            }
            for case in payload.test_cases
        ]
        if snapshot
        else [],
        human_review_status=design_delivery.human_acceptance if design_delivery else human_status,
        machine_review_status="approved" if review and review.approved else "failed" if review else "unavailable",
        machine_review=review,
        is_draft=is_draft,
        draft_override_used=is_draft,
        draft_override_reason=reason if is_draft else None,
        suite_complete=suite_complete,
        delivered_case_count=len(payload.test_cases),
        unresolved_task_count=unresolved,
        coverage={
            "basis": "explicit references; not behavioral acceptance",
            "requirements": requirements_coverage,
            "scenarios": scenarios_coverage,
            "behavioral_assessment": None,
        },
        source_mappings=source_mappings,
        provenance_status=(
            "available"
            if source_mappings
            and all(
                item["original_requirement_ids"] and item["sources"] and all(source["excerpt_verified"] for source in item["sources"])
                for item in source_mappings
            )
            else "partial"
            if any(item["sources"] for item in source_mappings)
            else "unavailable"
        ),
        source_snapshot_ids=source_ids,
        execution_status=execution_status,
        execution_run_ids=[run.run_id for run in matching_runs],
        execution_runs=[
            {"run_id": run.run_id, "status": run.status, "selected_test_case_ids": run.selected_test_case_ids, "source_snapshot_id": run.source_snapshot_id}
            for run in matching_runs
        ],
        design_delivery=design_delivery,
    )
