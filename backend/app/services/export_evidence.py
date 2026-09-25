"""Bind portable export evidence to the exact saved suite, never client approval."""

from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError

from ..contracts.exports import ExportAuditMetadata, ExportTestCasesInput
from ..contracts.projects import QaProjectDetail
from ..contracts.requirements import Requirement, ReviewResult
from ..contracts.test_cases import TestCase
from .test_case_quality import placeholder_reason


def _conflict(message: str) -> None:
    raise HTTPException(409, message)


def build_export_evidence(payload: ExportTestCasesInput, project: QaProjectDetail | None) -> ExportAuditMetadata:
    if any(placeholder_reason(case) for case in payload.test_cases):
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
        elif payload.source_snapshot_id:
            _conflict("The requested test-case snapshot is unavailable. Reload before exporting.")
    elif payload.source_snapshot_id:
        _conflict("A project is required to verify the requested test-case snapshot.")

    saved = snapshot.payload if snapshot else {}
    metadata = snapshot.metadata if snapshot else {}
    review = None
    if isinstance(saved.get("review"), dict) and isinstance(saved["review"].get("approved"), bool):
        try:
            review = ReviewResult.model_validate(saved["review"])
        except ValidationError:
            pass
    human = state.metadata.get("latest_human_review") if state else None
    human_status = "unavailable"
    if isinstance(human, dict) and human.get("snapshot_id") == snapshot.snapshot_id:
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
    if not approved and not (payload.draft_override_requested and reason):
        raise HTTPException(
            422,
            {
                "code": "draft_override_required",
                "message": "Export is unapproved, incomplete or unverifiable. Request a draft export and provide a nonempty reason.",
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
        human_review_status=human_status,
        machine_review_status="approved" if review and review.approved else "failed" if review else "unavailable",
        machine_review=review,
        is_draft=not approved,
        draft_override_used=not approved,
        draft_override_reason=reason if not approved else None,
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
    )
