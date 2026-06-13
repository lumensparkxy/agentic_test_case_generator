#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import QaProjectDetail, QaProjectExecutionRun, QaProjectStageSnapshot, QaProjectStageState
from app.services.orchestrator_service import build_orchestrator_status

BASE_TIME = datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _at(minutes: int) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes)


def _requirement_id(index: int) -> str:
    return f"REQ-{index:03d}"


def _test_case_id(requirement_id: str) -> str:
    return f"TC-{requirement_id.rsplit('-', 1)[-1]}"


def _requirements(count: int, *, modified_ids: set[str] | None = None) -> list[dict[str, Any]]:
    modified_ids = modified_ids or set()
    requirements: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        requirement_id = _requirement_id(index)
        text = f"{requirement_id} baseline checkout behavior shall be validated."
        if requirement_id in modified_ids:
            text = f"{requirement_id} changed payment retry and approval behavior shall be validated."
        requirements.append({"id": requirement_id, "text": text, "review_status": "Approved"})
    return requirements


def _coverage_plan(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": requirement["id"],
            "requirement_text": requirement["text"],
            "scenarios": [
                {
                    "id": f"{requirement['id']}-SCN-01",
                    "requirement_id": requirement["id"],
                    "scenario_type": "Happy Path",
                    "title": f"{requirement['id']} primary checkout behavior",
                    "objective": f"Validate {requirement['text']}",
                    "priority": "High",
                    "must_have": True,
                }
            ],
        }
        for requirement in requirements
    ]


def _test_case(requirement_id: str, *, artifact_version_number: int = 1, tags: list[str] | None = None) -> dict[str, Any]:
    test_case_id = _test_case_id(requirement_id)
    return {
        "id": test_case_id,
        "title": f"{requirement_id} checkout regression coverage",
        "description": f"Baseline coverage for {requirement_id}.",
        "priority": "High",
        "type": "Regression",
        "status": "Ready",
        "preconditions": "A signed-in user can access checkout.",
        "steps": [
            {
                "step": 1,
                "action": f"Exercise the workflow for {requirement_id}.",
                "expected": "The behavior satisfies the requirement.",
                "test_data": None,
            }
        ],
        "expected_result": "The checkout behavior is correct.",
        "test_data": None,
        "estimated_time": "5 mins",
        "automation_status": "Automated",
        "component": "Checkout",
        "tags": [requirement_id, *(tags or [])],
        "linked_requirement_ids": [requirement_id],
        "scenario_refs": [f"{requirement_id}-SCN-01"],
        "artifact_set_id": "tc-set-benchmark",
        "artifact_item_id": f"tc-item-{test_case_id.rsplit('-', 1)[-1]}",
        "artifact_version_id": f"tc-version-{test_case_id.rsplit('-', 1)[-1]}-{artifact_version_number}",
        "artifact_version_number": artifact_version_number,
    }


def _stage_state(
    snapshot_id: str | None,
    *,
    version: int,
    approved: bool,
    stale: bool = False,
    stale_reason: str | None = None,
    operation: str | None = None,
    metadata: dict[str, Any] | None = None,
    minutes: int = 0,
) -> QaProjectStageState:
    return QaProjectStageState(
        current_snapshot_id=snapshot_id,
        version=version,
        approved=approved,
        stale=stale,
        stale_reason=stale_reason,
        updated_at=_at(minutes),
        operation=operation,
        metadata=metadata or {},
    )


def _snapshot(
    *,
    project_id: str,
    snapshot_id: str,
    stage: str,
    version: int,
    project_revision: int,
    operation: str,
    approved: bool,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    source_snapshot_id: str | None = None,
    minutes: int = 0,
) -> QaProjectStageSnapshot:
    return QaProjectStageSnapshot(
        snapshot_id=snapshot_id,
        project_id=project_id,
        stage=stage,
        version=version,
        project_revision=project_revision,
        operation=operation,
        approved=approved,
        source_snapshot_id=source_snapshot_id,
        request_id=f"req-{snapshot_id}",
        actor_user_id="benchmark-user",
        title=snapshot_id,
        metadata=metadata or {},
        payload=payload,
        created_at=_at(minutes),
    )


def _impact_payload(
    fixture: dict[str, Any],
    *,
    baseline_requirements: list[dict[str, Any]],
    baseline_test_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    modified_ids = set(fixture.get("modified_requirement_ids") or [])
    changed_items_approved = bool(fixture.get("changed_items_approved", True))
    changed_items = [
        {
            "item_id": requirement_id,
            "kind": "requirement",
            "change_type": "modified",
            "title": f"{requirement_id} modified",
            "previous_text": next(item["text"] for item in baseline_requirements if item["id"] == requirement_id),
            "current_text": f"{requirement_id} changed payment retry and approval behavior shall be validated.",
            "approved": changed_items_approved,
            "requirement_id": requirement_id,
            "scenario_ids": [],
        }
        for requirement_id in sorted(modified_ids)
    ]
    impacted_test_cases = [
        {
            "test_case_id": _test_case_id(requirement_id),
            "title": f"{requirement_id} checkout regression coverage",
            "impact_source": "direct",
            "linked_requirement_ids": [requirement_id],
            "scenario_refs": [f"{requirement_id}-SCN-01"],
            "reason": f"Direct traceability match via linked requirements: {requirement_id}",
        }
        for requirement_id in sorted(modified_ids)
    ]
    recommendations: list[dict[str, Any]] = []
    for test_case in baseline_test_cases:
        linked_requirement_id = (test_case.get("linked_requirement_ids") or [""])[0]
        action = "update" if linked_requirement_id in modified_ids else "keep"
        recommendations.append(
            {
                "recommendation_id": f"impact-{action}-{test_case['id']}",
                "action": action,
                "title": f"{action.title()} {test_case['id']}",
                "reason": (
                    f"Direct traceability match via linked requirements: {linked_requirement_id}"
                    if action == "update"
                    else "No direct or semantic impact detected."
                ),
                "confidence": 0.86 if action == "update" else 0.93,
                "accepted": True,
                "impact_source": "direct",
                "test_case_id": test_case["id"],
                "requirement_id": linked_requirement_id,
                "scenario_refs": test_case.get("scenario_refs") or [],
            }
        )
    unchanged_count = max(0, len(baseline_requirements) - len(modified_ids))
    return {
        "baseline_snapshot_ids": {"requirements": "snap-req-v1", "context": None, "use_cases": "snap-use-v1", "test_cases": "snap-test-v1"},
        "current_snapshot_ids": {"requirements": "snap-req-v2", "context": None, "use_cases": "snap-use-v1", "test_cases": "snap-test-v1"},
        "changed_items": changed_items,
        "impacted_test_cases": impacted_test_cases,
        "recommendations": recommendations,
        "summary": {
            "changed_item_count": len(changed_items),
            "added_count": 0,
            "modified_count": len(changed_items),
            "removed_count": 0,
            "unchanged_requirement_count": unchanged_count,
            "directly_impacted_test_case_count": len(impacted_test_cases),
            "semantic_neighbor_count": 0,
            "recommendation_counts": {
                "keep": sum(1 for item in recommendations if item["action"] == "keep"),
                "update": sum(1 for item in recommendations if item["action"] == "update"),
                "add": 0,
                "deprecate": 0,
            },
        },
    }


def _execution_run(project_id: str) -> QaProjectExecutionRun:
    return QaProjectExecutionRun(
        run_record_id="record-staging",
        project_id=project_id,
        run_id="run-staging",
        target_environment="staging",
        target_base_url="https://staging.example.test/app",
        project_revision=8,
        test_case_count=10,
        status="passed",
        summary={"passed": 10, "failed": 0, "invalid": 0, "skipped": 0},
        snapshot_id="snap-exec-v1",
        source_snapshot_id="snap-test-v2",
        selected_test_case_ids=[f"TC-{index:03d}" for index in range(1, 11)],
        request_id="req-execution",
        actor_user_id="benchmark-user",
        created_at=_at(8),
    )


def _build_project(fixture: dict[str, Any]) -> QaProjectDetail:
    project_id = str(fixture.get("project_id") or "benchmark-project")
    requirement_count = int(fixture.get("requirement_count") or 10)
    modified_ids = set(fixture.get("modified_requirement_ids") or [])
    include_baseline_suite = bool(fixture.get("include_baseline_suite", False))
    include_impact_analysis = bool(fixture.get("include_impact_analysis", False))
    apply_impact_update = bool(fixture.get("apply_impact_update", False))
    include_execution = bool(fixture.get("include_execution", False))
    current_requirements_approved = bool(fixture.get("current_requirements_approved", True))
    baseline_requirements = _requirements(requirement_count)
    current_requirements = _requirements(requirement_count, modified_ids=modified_ids)
    baseline_coverage_plan = _coverage_plan(baseline_requirements)
    baseline_test_cases = [_test_case(requirement["id"]) for requirement in baseline_requirements]
    impact_payload = _impact_payload(fixture, baseline_requirements=baseline_requirements, baseline_test_cases=baseline_test_cases)
    stage_state: dict[str, QaProjectStageState] = {}
    snapshots: dict[str, QaProjectStageSnapshot] = {}
    current_revision = 1

    requirement_snapshot_id = "snap-req-v2" if include_baseline_suite and modified_ids else "snap-req-v1"
    requirement_version = 2 if requirement_snapshot_id == "snap-req-v2" else 1
    current_revision = max(current_revision, 5 if requirement_version == 2 else 1)
    stage_state["requirements"] = _stage_state(
        requirement_snapshot_id,
        version=requirement_version,
        approved=current_requirements_approved,
        operation="requirements.refine" if requirement_version == 2 else "requirements.parse",
        minutes=1 if requirement_version == 1 else 5,
    )
    snapshots["requirements"] = _snapshot(
        project_id=project_id,
        snapshot_id=requirement_snapshot_id,
        stage="requirements",
        version=requirement_version,
        project_revision=current_revision,
        operation="requirements.refine" if requirement_version == 2 else "requirements.parse",
        approved=current_requirements_approved,
        payload={
            "requirements": current_requirements,
            "review": {
                "approved": current_requirements_approved,
                "score": 96 if current_requirements_approved else 72,
                "threshold": 85,
                "summary": "Approved." if current_requirements_approved else "Approval is pending.",
                "blocking_issues": [] if current_requirements_approved else ["Requirements need human approval."],
            },
        },
        metadata={"requirement_count": len(current_requirements)},
        minutes=1 if requirement_version == 1 else 5,
    )

    if include_baseline_suite:
        stale_reason = "requirements changed in project revision 5" if modified_ids and not apply_impact_update else None
        stage_state["use_cases"] = _stage_state(
            "snap-use-v1",
            version=1,
            approved=True,
            stale=bool(modified_ids and not apply_impact_update),
            stale_reason=stale_reason,
            operation="testcases.generate.use_cases",
            metadata={"scenario_count": requirement_count},
            minutes=2,
        )
        snapshots["use_cases"] = _snapshot(
            project_id=project_id,
            snapshot_id="snap-use-v1",
            stage="use_cases",
            version=1,
            project_revision=2,
            operation="testcases.generate.use_cases",
            approved=True,
            source_snapshot_id="snap-req-v1",
            payload={"coverage_plan": baseline_coverage_plan, "requirement_analysis": []},
            metadata={"scenario_count": requirement_count},
            minutes=2,
        )
        if include_impact_analysis or apply_impact_update:
            current_revision = max(current_revision, 6)
            stage_state["impact_analysis"] = _stage_state(
                "snap-impact-v1",
                version=1,
                approved=False,
                operation="impact.analysis",
                metadata={"changed_item_count": impact_payload["summary"]["changed_item_count"]},
                minutes=6,
            )
            snapshots["impact_analysis"] = _snapshot(
                project_id=project_id,
                snapshot_id="snap-impact-v1",
                stage="impact_analysis",
                version=1,
                project_revision=6,
                operation="impact.analysis",
                approved=False,
                payload=impact_payload,
                metadata={"changed_item_count": impact_payload["summary"]["changed_item_count"]},
                minutes=6,
            )

        test_case_snapshot_id = "snap-test-v2" if apply_impact_update else "snap-test-v1"
        test_case_version = 2 if apply_impact_update else 1
        test_case_revision = 7 if apply_impact_update else 4
        current_revision = max(current_revision, test_case_revision)
        if apply_impact_update:
            test_cases = [
                _test_case(requirement["id"], artifact_version_number=2, tags=["impact:update"])
                if requirement["id"] in modified_ids
                else _test_case(requirement["id"])
                for requirement in baseline_requirements
            ]
        else:
            test_cases = baseline_test_cases
        stage_state["test_cases"] = _stage_state(
            test_case_snapshot_id,
            version=test_case_version,
            approved=True,
            stale=bool(modified_ids and not apply_impact_update),
            stale_reason=stale_reason,
            operation="impact.update.apply" if apply_impact_update else "testcases.generate",
            metadata={
                "test_case_count": len(test_cases),
                **({"preserved_count": requirement_count - len(modified_ids), "updated_count": len(modified_ids)} if apply_impact_update else {}),
            },
            minutes=7 if apply_impact_update else 4,
        )
        snapshots["test_cases"] = _snapshot(
            project_id=project_id,
            snapshot_id=test_case_snapshot_id,
            stage="test_cases",
            version=test_case_version,
            project_revision=test_case_revision,
            operation="impact.update.apply" if apply_impact_update else "testcases.generate",
            approved=True,
            source_snapshot_id="snap-impact-v1" if apply_impact_update else "snap-use-v1",
            payload={
                "test_cases": test_cases,
                "coverage_plan": baseline_coverage_plan,
                "requirement_analysis": [],
                "impact_analysis": impact_payload if apply_impact_update else None,
                "impact_update_result": {
                    "preserved_count": requirement_count - len(modified_ids),
                    "updated_count": len(modified_ids),
                    "added_count": 0,
                    "deprecated_count": 0,
                    "applied_recommendation_ids": [item["recommendation_id"] for item in impact_payload["recommendations"]],
                }
                if apply_impact_update
                else None,
                "review": {"approved": True, "score": 100, "threshold": 90, "summary": "Approved.", "blocking_issues": []},
            },
            metadata={"test_case_count": len(test_cases), "source_snapshot_ids": {"requirements": "snap-req-v1", "context": None, "use_cases": "snap-use-v1"}},
            minutes=7 if apply_impact_update else 4,
        )

    execution_runs: list[QaProjectExecutionRun] = []
    if include_execution:
        execution_runs.append(_execution_run(project_id))
        current_revision = max(current_revision, 8)
        stage_state["execution"] = _stage_state(
            "snap-exec-v1",
            version=1,
            approved=True,
            operation="automation.execution.run",
            metadata={"status": "passed", "target_environment": "staging", "run_id": "run-staging"},
            minutes=8,
        )
        snapshots["execution"] = _snapshot(
            project_id=project_id,
            snapshot_id="snap-exec-v1",
            stage="execution",
            version=1,
            project_revision=8,
            operation="automation.execution.run",
            approved=True,
            payload={"run_id": "run-staging", "status": "passed", "target_environment": "staging", "summary": {"passed": 10, "failed": 0}},
            metadata={"status": "passed", "run_id": "run-staging", "target_environment": "staging"},
            minutes=8,
        )

    return QaProjectDetail(
        project_id=project_id,
        name=str(fixture.get("name") or "Benchmark QA Project"),
        description=str(fixture.get("description") or ""),
        status="active",
        owner_user_id="benchmark-user",
        current_revision=current_revision,
        created_at=BASE_TIME,
        updated_at=_at(current_revision),
        stage_state=stage_state,
        current_snapshots=snapshots,
        timeline=[],
        execution_runs=execution_runs,
    )


def _primary_action(status: Any) -> dict[str, Any]:
    for action in status.next_actions:
        if action.primary:
            return action.model_dump(mode="json")
    return {}


def _action_by_id(status: Any, action_id: str) -> dict[str, Any]:
    for action in status.next_actions:
        if action.action == action_id:
            return action.model_dump(mode="json")
    return {}


def _strategy_metrics(fixture: dict[str, Any], project: QaProjectDetail) -> dict[str, Any]:
    test_case_snapshot = project.current_snapshots.get("test_cases")
    if not test_case_snapshot:
        return {
            "baseline_test_case_count": 0,
            "changed_items_expected": len(fixture.get("modified_requirement_ids") or []),
            "changed_items_detected": 0,
            "impact_precision": 1.0,
            "impact_recall": 1.0,
            "unchanged_preservation_ratio": 1.0,
            "full_regenerate_false_update_recommendations": 0,
            "impact_false_update_recommendations": 0,
            "false_update_recommendations_avoided": 0,
            "impact_update_preserved_count": 0,
            "impact_update_updated_count": 0,
            "full_regenerate_updated_count": 0,
        }

    baseline_test_cases = test_case_snapshot.payload.get("test_cases") or []
    modified_ids = set(fixture.get("modified_requirement_ids") or [])
    unchanged_ids = {
        requirement_id
        for requirement_id in [item.get("linked_requirement_ids", [""])[0] for item in baseline_test_cases]
        if requirement_id and requirement_id not in modified_ids
    }
    impact_payload = _impact_payload(
        fixture,
        baseline_requirements=_requirements(int(fixture.get("requirement_count") or 10)),
        baseline_test_cases=baseline_test_cases,
    )
    changed_detected = {item.get("requirement_id") for item in impact_payload["changed_items"] if item.get("requirement_id")}
    update_recommendations = [item for item in impact_payload["recommendations"] if item["action"] in {"update", "add", "deprecate"}]
    preserved_recommendations = [item for item in impact_payload["recommendations"] if item["action"] == "keep"]
    impact_false_updates = [item for item in update_recommendations if item.get("requirement_id") not in modified_ids]
    full_regenerate_false_updates = len(unchanged_ids)
    impact_precision = (len(update_recommendations) - len(impact_false_updates)) / len(update_recommendations) if update_recommendations else 1.0
    impact_recall = len(changed_detected & modified_ids) / len(modified_ids) if modified_ids else 1.0
    unchanged_preservation_ratio = len(preserved_recommendations) / len(unchanged_ids) if unchanged_ids else 1.0
    return {
        "baseline_test_case_count": len(baseline_test_cases),
        "changed_items_expected": len(modified_ids),
        "changed_items_detected": len(changed_detected),
        "impact_precision": round(impact_precision, 2),
        "impact_recall": round(impact_recall, 2),
        "unchanged_preservation_ratio": round(unchanged_preservation_ratio, 2),
        "full_regenerate_false_update_recommendations": full_regenerate_false_updates,
        "impact_false_update_recommendations": len(impact_false_updates),
        "false_update_recommendations_avoided": full_regenerate_false_updates - len(impact_false_updates),
        "impact_update_preserved_count": len(preserved_recommendations),
        "impact_update_updated_count": len(update_recommendations),
        "full_regenerate_updated_count": len(baseline_test_cases),
    }


def _governance_metrics(fixture: dict[str, Any], status: Any) -> dict[str, Any]:
    analysis_fixture = {**fixture, "include_impact_analysis": False, "apply_impact_update": False}
    analysis_status = build_orchestrator_status(_build_project(analysis_fixture))
    analysis_action = _action_by_id(analysis_status, "analyze_impact")
    primary = _primary_action(status)
    blocker_codes = sorted({blocker.get("code") for blocker in primary.get("blockers") or [] if blocker.get("code")})
    return {
        "analysis_available": bool(analysis_action and analysis_action.get("enabled")),
        "mutation_action": primary.get("action"),
        "mutation_enabled": bool(primary.get("enabled", False)),
        "mutation_blocker_codes": blocker_codes,
        "analysis_available_while_mutation_blocked": bool(analysis_action and analysis_action.get("enabled"))
        and primary.get("action") == "apply_update"
        and not primary.get("enabled", False),
    }


def _evaluate_expectations(result: dict[str, Any], expectation: dict[str, Any] | None) -> dict[str, Any]:
    if not expectation:
        return {"checks": [], "all_met": True}

    checks: list[dict[str, Any]] = []

    def add_check(name: str, expected: Any, actual: Any, met: bool) -> None:
        checks.append({"name": name, "expected": expected, "actual": actual, "met": bool(met)})

    primary = result["primary_action"]
    status = result["status"]
    metrics = result["metrics"]
    governance = result["governance"]

    if "expected_primary_action" in expectation:
        add_check(
            "expected_primary_action",
            expectation["expected_primary_action"],
            primary.get("action"),
            primary.get("action") == expectation["expected_primary_action"],
        )
    if "expected_current_stage" in expectation:
        add_check(
            "expected_current_stage",
            expectation["expected_current_stage"],
            status.get("current_stage"),
            status.get("current_stage") == expectation["expected_current_stage"],
        )
    if "require_enabled_primary" in expectation:
        add_check(
            "require_enabled_primary",
            expectation["require_enabled_primary"],
            primary.get("enabled"),
            bool(primary.get("enabled")) is bool(expectation["require_enabled_primary"]),
        )
    if "expected_has_baseline_test_suite" in expectation:
        add_check(
            "expected_has_baseline_test_suite",
            expectation["expected_has_baseline_test_suite"],
            status.get("has_baseline_test_suite"),
            bool(status.get("has_baseline_test_suite")) is bool(expectation["expected_has_baseline_test_suite"]),
        )
    if "expected_upstream_changed" in expectation:
        add_check(
            "expected_upstream_changed",
            expectation["expected_upstream_changed"],
            status.get("upstream_changed"),
            bool(status.get("upstream_changed")) is bool(expectation["expected_upstream_changed"]),
        )
    if "expected_changed_item_count" in expectation:
        add_check(
            "expected_changed_item_count",
            expectation["expected_changed_item_count"],
            metrics["changed_items_detected"],
            metrics["changed_items_detected"] == int(expectation["expected_changed_item_count"]),
        )
    if "minimum_impact_precision" in expectation:
        add_check(
            "minimum_impact_precision",
            expectation["minimum_impact_precision"],
            metrics["impact_precision"],
            metrics["impact_precision"] >= float(expectation["minimum_impact_precision"]),
        )
    if "minimum_impact_recall" in expectation:
        add_check(
            "minimum_impact_recall",
            expectation["minimum_impact_recall"],
            metrics["impact_recall"],
            metrics["impact_recall"] >= float(expectation["minimum_impact_recall"]),
        )
    if "minimum_unchanged_preservation_ratio" in expectation:
        add_check(
            "minimum_unchanged_preservation_ratio",
            expectation["minimum_unchanged_preservation_ratio"],
            metrics["unchanged_preservation_ratio"],
            metrics["unchanged_preservation_ratio"] >= float(expectation["minimum_unchanged_preservation_ratio"]),
        )
    if "minimum_false_update_recommendations_avoided" in expectation:
        add_check(
            "minimum_false_update_recommendations_avoided",
            expectation["minimum_false_update_recommendations_avoided"],
            metrics["false_update_recommendations_avoided"],
            metrics["false_update_recommendations_avoided"] >= int(expectation["minimum_false_update_recommendations_avoided"]),
        )
    if "expected_full_regenerate_false_update_recommendations" in expectation:
        add_check(
            "expected_full_regenerate_false_update_recommendations",
            expectation["expected_full_regenerate_false_update_recommendations"],
            metrics["full_regenerate_false_update_recommendations"],
            metrics["full_regenerate_false_update_recommendations"] == int(expectation["expected_full_regenerate_false_update_recommendations"]),
        )
    if "expected_impact_false_update_recommendations" in expectation:
        add_check(
            "expected_impact_false_update_recommendations",
            expectation["expected_impact_false_update_recommendations"],
            metrics["impact_false_update_recommendations"],
            metrics["impact_false_update_recommendations"] == int(expectation["expected_impact_false_update_recommendations"]),
        )
    if "expected_resume_action_match" in expectation:
        add_check(
            "expected_resume_action_match",
            expectation["expected_resume_action_match"],
            result["resumability"]["primary_action_matches"],
            result["resumability"]["primary_action_matches"] is bool(expectation["expected_resume_action_match"]),
        )
    if "expected_analysis_available_while_mutation_blocked" in expectation:
        add_check(
            "expected_analysis_available_while_mutation_blocked",
            expectation["expected_analysis_available_while_mutation_blocked"],
            governance["analysis_available_while_mutation_blocked"],
            governance["analysis_available_while_mutation_blocked"] is bool(expectation["expected_analysis_available_while_mutation_blocked"]),
        )
    if expectation.get("expected_blocker_codes"):
        expected_codes = sorted(expectation["expected_blocker_codes"])
        add_check(
            "expected_blocker_codes",
            expected_codes,
            governance["mutation_blocker_codes"],
            all(code in governance["mutation_blocker_codes"] for code in expected_codes),
        )

    return {"checks": checks, "all_met": all(check["met"] for check in checks)}


def _build_benchmark_result(input_path: Path, expectation_path: Path | None, fixture: dict[str, Any]) -> dict[str, Any]:
    expectation = _load_json(expectation_path) if expectation_path and expectation_path.exists() else None
    project = _build_project(fixture)
    status = build_orchestrator_status(project)
    resumed_project = QaProjectDetail.model_validate(json.loads(project.model_dump_json()))
    resumed_status = build_orchestrator_status(resumed_project)
    primary = _primary_action(status)
    resumed_primary = _primary_action(resumed_status)
    result = {
        "name": str((expectation or {}).get("name") or fixture.get("name") or input_path.stem),
        "input_file": str(input_path.relative_to(REPO_ROOT)),
        "expectation_file": str(expectation_path.relative_to(REPO_ROOT)) if expectation_path and expectation_path.exists() else None,
        "description": str((expectation or {}).get("description") or fixture.get("description") or ""),
        "execution_mode": "offline-deterministic",
        "status": status.model_dump(mode="json"),
        "primary_action": primary,
        "metrics": _strategy_metrics(fixture, project),
        "governance": _governance_metrics(fixture, status),
        "resumability": {
            "initial_primary_action": primary.get("action"),
            "resumed_primary_action": resumed_primary.get("action"),
            "primary_action_matches": primary.get("action") == resumed_primary.get("action"),
            "initial_project_revision": project.current_revision,
            "resumed_project_revision": resumed_project.current_revision,
        },
    }
    result["expectation_result"] = _evaluate_expectations(result, expectation)
    return result


def _load_payloads(input_dir: Path, expectation_dir: Path) -> list[tuple[Path, Path | None, dict[str, Any]]]:
    payloads: list[tuple[Path, Path | None, dict[str, Any]]] = []
    for input_path in sorted(input_dir.glob("*.json")):
        expectation_path = expectation_dir / input_path.name
        payloads.append((input_path, expectation_path if expectation_path.exists() else None, _load_json(input_path)))
    return payloads


def _build_overall_summary(results: list[dict[str, Any]], strict: bool) -> dict[str, Any]:
    if not results:
        return {"benchmark_count": 0, "all_expectations_met": True, "strict_mode": strict}
    precision_values = [float(result["metrics"]["impact_precision"]) for result in results if result["metrics"]["baseline_test_case_count"]]
    preservation_values = [float(result["metrics"]["unchanged_preservation_ratio"]) for result in results if result["metrics"]["baseline_test_case_count"]]
    avoided_values = [int(result["metrics"]["false_update_recommendations_avoided"]) for result in results]
    return {
        "benchmark_count": len(results),
        "all_expectations_met": all(result["expectation_result"]["all_met"] for result in results),
        "strict_mode": strict,
        "execution_modes": sorted({result["execution_mode"] for result in results}),
        "average_impact_precision": round(mean(precision_values), 2) if precision_values else 1.0,
        "average_unchanged_preservation_ratio": round(mean(preservation_values), 2) if preservation_values else 1.0,
        "total_false_update_recommendations_avoided": sum(avoided_values),
        "resumability_match_count": sum(1 for result in results if result["resumability"]["primary_action_matches"]),
    }


def _print_result(result: dict[str, Any]) -> None:
    expectation_result = result["expectation_result"]
    metrics = result["metrics"]
    governance = result["governance"]
    primary = result["primary_action"]
    status = "PASS" if expectation_result["all_met"] else "WARN"
    print(
        f"[{status}] {result['name']} | mode={result['execution_mode']} | primary={primary.get('action')} "
        f"enabled={primary.get('enabled')} | stage={result['status'].get('current_stage')} | "
        f"impact_precision={metrics['impact_precision']:.2f} | preserved={metrics['impact_update_preserved_count']} | "
        f"avoided_false_updates={metrics['false_update_recommendations_avoided']} | resume={result['resumability']['primary_action_matches']}"
    )
    if result.get("description"):
        print(f"  {result['description']}")
    if metrics["baseline_test_case_count"]:
        print(
            "  compare: "
            f"full_regenerate updates={metrics['full_regenerate_updated_count']} false_updates={metrics['full_regenerate_false_update_recommendations']} | "
            f"impact_update updates={metrics['impact_update_updated_count']} preserves={metrics['impact_update_preserved_count']} "
            f"false_updates={metrics['impact_false_update_recommendations']}"
        )
    if governance["mutation_blocker_codes"]:
        print(f"  governance blockers: {', '.join(governance['mutation_blocker_codes'])}")
    unmet_checks = [check for check in expectation_result["checks"] if not check["met"]]
    for check in unmet_checks:
        print(f"  unmet: {check['name']} expected={check['expected']} actual={check['actual']}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate deterministic orchestrator lifecycle scenarios.")
    parser.add_argument(
        "--input-dir",
        default=str(REPO_ROOT / "scripts" / "benchmark_orchestrator_inputs"),
        help="Directory containing orchestrator benchmark input JSON payloads.",
    )
    parser.add_argument(
        "--expectation-dir",
        default=str(REPO_ROOT / "scripts" / "benchmark_orchestrator_expectations"),
        help="Directory containing orchestrator benchmark expectation JSON files.",
    )
    parser.add_argument("--output-json", default="", help="Optional path to write the evaluation report as JSON.")
    parser.add_argument("--offline", action="store_true", help="Accepted for parity with other benchmark scripts; scenarios are always offline.")
    parser.add_argument("--strict", action="store_true", help="Exit with a non-zero status code if any expectation is unmet.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    input_dir = Path(args.input_dir).resolve()
    expectation_dir = Path(args.expectation_dir).resolve()
    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2
    payloads = _load_payloads(input_dir, expectation_dir)
    if not payloads:
        print(f"No benchmark input files were found in {input_dir}", file=sys.stderr)
        return 2

    results = [_build_benchmark_result(input_path, expectation_path, fixture) for input_path, expectation_path, fixture in payloads]
    overall = _build_overall_summary(results, strict=args.strict)

    print(f"Evaluated {overall['benchmark_count']} orchestrator benchmark fixture(s).")
    for result in results:
        _print_result(result)
    print(
        "Overall | "
        f"avg_impact_precision={overall.get('average_impact_precision', 0.0):.2f} | "
        f"avg_preservation={overall.get('average_unchanged_preservation_ratio', 0.0):.2f} | "
        f"false_updates_avoided={overall.get('total_false_update_recommendations_avoided', 0)} | "
        f"resumability_matches={overall.get('resumability_match_count', 0)}/{overall['benchmark_count']}"
    )

    report = {"overall": overall, "benchmarks": results}
    if args.output_json:
        output_path = Path(args.output_json).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote JSON report to {output_path}")

    if args.strict and not overall["all_expectations_met"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
