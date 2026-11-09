"""Bind independent design evidence to the exact immutable saved Test Cases suite."""

import json
import re

from pydantic import ValidationError

from ..contracts.grounding import EnrichInput
from ..contracts.requirements import Requirement, RequirementCoveragePlan
from ..contracts.suite_quality import SuiteQualityProjection
from ..contracts.test_cases import TestCase
from .requirement_scope import approved_generation_requirements
from .test_case_quality import case_source_hash, placeholder_reason

_PROOF_VERSION = 1
_PROOF_OPERATIONS = {"testcases.generate", "test_cases.review"}
_VERSION_FIELDS = {"artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number"}
_SOURCE_FIELDS = {
    "requirements": "source_requirements_snapshot_id",
    "context": "source_context_snapshot_id",
    "use_cases": "source_use_case_snapshot_id",
}
_FAILED_GENERATION = (
    "failure_reason",
    "used_fallback",
    "timed_out",
    "stalled",
    "max_iterations_reached",
    "failed_shard_count",
    "fallback_shard_count",
    "deterministic_total_additions",
    "scenario_ref_coverage_degraded",
    "missing_requirements_count",
    "missing_must_have_scenario_count",
)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _without_null_fields(value):
    if isinstance(value, dict):
        return {key: _without_null_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_null_fields(item) for item in value]
    return value


def _case_rows(cases):
    return [
        TestCase.model_validate(
            case.model_dump(mode="json") if isinstance(case, TestCase) else case,
            strict=True,
            extra="forbid",
        ).model_dump(mode="json")
        for case in cases
    ]


def reviewed_case_content_hash(cases):
    """Hash the ordered rows actually reviewed, excluding the four persistence version fields."""
    return _reviewed_rows_hash(_case_rows(cases))


def _reviewed_rows_hash(rows):
    return case_source_hash([{key: value for key, value in row.items() if key not in _VERSION_FIELDS} for row in rows])


def _current_snapshot(project, stage):
    snapshot = project.current_snapshots.get(stage)
    state = project.stage_state.get(stage)
    if snapshot and state and snapshot.snapshot_id == state.current_snapshot_id and snapshot.project_id == project.project_id and snapshot.stage == stage:
        return snapshot
    return None


def _human_decision(project, stage, snapshot_id):
    state = project.stage_state.get(stage)
    if not state or not snapshot_id or state.current_snapshot_id != snapshot_id:
        return "unavailable", None
    review = _mapping(state.metadata.get("latest_human_review"))
    if review.get("snapshot_id") != snapshot_id or not _text(review.get("review_id")) or review.get("reviewer_user_id") != project.owner_user_id:
        return "unavailable", None
    if review.get("decision") == "request_changes":
        return "changes_requested", review["review_id"]
    if review.get("decision") == "approve" and (state.approved or stage == "test_cases"):
        return "approved", review["review_id"]
    return "unavailable", None


def _recorded_sources(metadata):
    nested = _mapping(metadata.get("source_snapshot_ids"))
    return {stage: nested.get(stage, metadata.get(field)) for stage, field in _SOURCE_FIELDS.items()}


def _source_disagrees(metadata, stage):
    nested = _mapping(metadata.get("source_snapshot_ids"))
    field = _SOURCE_FIELDS[stage]
    return stage in nested and field in metadata and nested[stage] != metadata[field]


def _approved_scope(project):
    try:
        return approved_generation_requirements(project)
    except TypeError, ValidationError:
        return []


def _source_is_stale(project, suite):
    state = project.stage_state.get("test_cases")
    if state and (state.stale or (state.current_snapshot_id and state.current_snapshot_id != suite.snapshot_id)):
        return True
    for stage, recorded in _recorded_sources(suite.metadata).items():
        stage_state = project.stage_state.get(stage)
        if stage_state and stage_state.stale:
            return True
        if recorded is not None and stage_state and stage_state.current_snapshot_id and recorded != stage_state.current_snapshot_id:
            return True
        if stage == "context" and suite.metadata.get("source_context_omitted") is True and stage_state and stage_state.current_snapshot_id:
            return True
    return False


def _approved_plan(project, plan_snapshot_id, requirements, context_id, *, sync_binding=None):
    plan = _current_snapshot(project, "use_cases")
    baseline = _current_snapshot(project, "requirements")
    if (
        not plan
        or not baseline
        or plan.snapshot_id != plan_snapshot_id
        or project.stage_state["use_cases"].stale
        or plan.source_snapshot_id != baseline.snapshot_id
    ):
        return None
    if sync_binding is None and (
        plan.operation != "use_cases.generate"
        or plan.metadata.get("source_requirements_snapshot_id") != baseline.snapshot_id
        or plan.metadata.get("source_context_snapshot_id") != context_id
        or (plan.metadata.get("source_context_omitted") is True) != (context_id is None)
    ):
        return None
    decision, review_id = _human_decision(project, "use_cases", plan.snapshot_id)
    if decision != "approved":
        return None
    groups = [RequirementCoveragePlan.model_validate(group, extra="forbid") for group in plan.payload.get("coverage_plan", [])]
    texts = {row.id: row.text for row in requirements}
    if len(groups) != len(texts) or {group.requirement_id: group.requirement_text for group in groups} != texts:
        return None
    scenarios = {}
    for group in groups:
        if not group.scenarios:
            return None
        for scenario in group.scenarios:
            if (
                scenario.requirement_id != group.requirement_id
                or not _text(scenario.id)
                or scenario.id in scenarios
                or not _text(scenario.title)
                or not _text(scenario.objective)
            ):
                return None
            scenarios[scenario.id] = scenario
    return groups, scenarios, review_id


def _context_binding(project, context, requirements, metadata):
    expected_hash = metadata.get("source_context_hash")
    selected_ids = metadata.get("source_context_artifact_ids")
    omitted = metadata.get("source_context_omitted")
    if context is None:
        return ({"context_omitted": True}, None) if omitted is True and expected_hash is None and selected_ids is None else None
    if omitted is True or not _is_hash(expected_hash):
        return None
    applied = EnrichInput.model_validate({**context.payload, "requirements": requirements})
    if selected_ids is not None:
        if (
            not applied.grounded_context
            or not isinstance(selected_ids, list)
            or any(not _text(source_id) for source_id in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
        ):
            return None
        selection = set(selected_ids)
        grounded = applied.grounded_context
        saved_ids = [source.id for source in grounded.artifact_sources]
        if len(set(saved_ids)) != len(saved_ids) or selected_ids != [source_id for source_id in saved_ids if source_id in selection]:
            return None
        applied = applied.model_copy(
            update={
                "grounded_context": grounded.model_copy(
                    update={
                        "artifact_sources": [source for source in grounded.artifact_sources if source.id in selection],
                        "ui_elements": [element for element in grounded.ui_elements if not element.source_id or element.source_id in selection],
                    }
                )
            }
        )
    elif applied.grounded_context:
        # The published generation records even an empty selector for grounded Context.
        return None
    base_revision = metadata.get("generation_base_project_revision")
    if type(base_revision) is not int or not 0 <= base_revision <= project.current_revision:
        return None
    workbench = applied.model_copy(
        update={
            "diagram_links": applied.diagram_links or None,
            "image_links": applied.image_links or None,
            "project_id": project.project_id,
            "base_project_revision": base_revision,
        }
    )
    for candidate in (applied, workbench):
        content = candidate.model_dump(mode="json")
        if case_source_hash(content) == expected_hash:
            return {
                "context_omitted": False,
                "context_snapshot_id": context.snapshot_id,
                "context_snapshot_hash": case_source_hash(_without_null_fields(context.payload)),
                "applied_context_hash": expected_hash,
                **({"context_artifact_ids": selected_ids} if selected_ids is not None else {}),
            }, content
    return None


def _versioned_cases(rows):
    if not rows or any(
        not all(_text(row[field]) for field in ("id", "artifact_set_id", "artifact_item_id", "artifact_version_id"))
        or type(row["artifact_version_number"]) is not int
        or row["artifact_version_number"] < 1
        for row in rows
    ):
        return False
    return all(len({row[key] for row in rows}) == len(rows) for key in ("id", "artifact_item_id", "artifact_version_id"))


def _case_scope_gaps(cases, scenarios):
    covered = set()
    for row in cases:
        requirements = row["linked_requirement_ids"]
        refs = row["scenario_refs"]
        if (
            not requirements
            or not refs
            or len(requirements) != len(set(requirements))
            or len(refs) != len(set(refs))
            or any(ref not in scenarios for ref in refs)
            or {scenarios[ref].requirement_id for ref in refs} != set(requirements)
            or placeholder_reason(row)
        ):
            return True
        covered.update(refs)
    return covered != scenarios.keys()


def _structural_gaps(payload, cases, scenarios):
    diagnostics = _mapping(payload.get("workflow_diagnostics"))
    return (
        _case_scope_gaps(cases, scenarios)
        or bool(payload["generation_tasks"])
        or diagnostics.get("status") not in {"completed", "partial"}
        or any(diagnostics.get(key) for key in _FAILED_GENERATION)
    )


def _complete_assessment(assessment, cases, requirements, context):
    # Imported on use because the agent writer shares the case hash helper.
    from ..agents.substantive_review import validate_assessment

    recorded_diagnostics = assessment.get("diagnostics")
    if recorded_diagnostics is not None and not isinstance(recorded_diagnostics, dict):
        return False
    diagnostics = _mapping(recorded_diagnostics)
    groups = diagnostics.get("groups")
    if (
        assessment.get("model_status") == "unknown"
        or assessment.get("status") not in {"assessed_complete", "incomplete"}
        or diagnostics.get("failure_reason")
        or diagnostics.get("unverified_group_count", 0) != 0
        or (groups is not None and (not isinstance(groups, list) or not groups or diagnostics.get("unverified_group_count") != 0))
        or any(
            not isinstance(group, dict)
            or group.get("status") not in {"assessed_complete", "incomplete"}
            or group.get("design_status") != "assessed_complete"
            or group.get("failure_reason")
            for group in groups or []
        )
    ):
        return False
    evidence = {key: assessment.get(key) for key in ("input_hash", "obligations", "case_grounding")}
    data = {
        "scope": "whole_requirements",
        "requirements": [row.model_dump(mode="json") for row in requirements],
        "test_cases": cases,
        "context": context,
    }
    try:
        verified = validate_assessment(json.dumps(evidence), data, assessment["input_hash"])
    except ValueError, TypeError, KeyError, AttributeError:
        return False
    return verified["design_status"] == "assessed_complete" and verified["execution_status"] == assessment.get("execution_status")


def _design_status(payload, assessment, precheck, cases, requirements, scenarios, context):
    if precheck["passed"] is False:
        return "incomplete", "structural_review_incomplete"
    if assessment.get("design_status") == "incomplete":
        return "incomplete", "independent_design_gaps"
    if _structural_gaps(payload, cases, scenarios):
        return "incomplete", "structural_review_incomplete"
    if assessment.get("design_status") == "assessed_complete" and _complete_assessment(assessment, cases, requirements, context):
        return "complete", "design_verified"
    return "unknown", "independent_design_unverified"


def build_suite_design_proof(project, payload, metadata, *, plan_snapshot_id):
    """Return server-derived design evidence suitable for stamping with the new suite ID."""
    from ..agents.substantive_review import RUBRIC

    try:
        return _build_proof(project, payload, metadata, plan_snapshot_id, RUBRIC)
    except ValueError, TypeError, KeyError, AttributeError, ValidationError:
        return None


def _build_proof(project, payload, metadata, plan_snapshot_id, rubric):
    baseline = _current_snapshot(project, "requirements")
    context = _current_snapshot(project, "context")
    context_state = project.stage_state.get("context")
    requirements = _approved_scope(project)
    if not baseline or not requirements or (context_state and (context_state.stale or bool(context_state.current_snapshot_id) != bool(context))):
        return None
    expected_sources = {"requirements": baseline.snapshot_id, "context": context.snapshot_id if context else None, "use_cases": plan_snapshot_id}
    nested_sources = _mapping(metadata.get("source_snapshot_ids"))
    if (
        not _text(plan_snapshot_id)
        or not nested_sources.keys() <= _SOURCE_FIELDS.keys()
        or any(nested_sources.get(stage) != source or metadata.get(_SOURCE_FIELDS[stage]) != source for stage, source in expected_sources.items())
    ):
        return None
    sync_binding = None
    if "sync_review_binding" in metadata:
        from .project_sync_review import sync_review_binding_for_proof

        sync_binding = sync_review_binding_for_proof(project, payload, metadata)
        if sync_binding is None:
            return None
    plan = _approved_plan(project, plan_snapshot_id, requirements, expected_sources["context"], sync_binding=sync_binding)
    if not plan:
        return None
    groups, scenarios, review_id = plan
    suite_plan = [RequirementCoveragePlan.model_validate(group, extra="forbid") for group in payload.get("coverage_plan", [])]
    requirement_hashes = {row.id: case_source_hash(row.model_dump(mode="json")) for row in requirements}
    scenario_hashes = {key: case_source_hash(scenario.model_dump(mode="json")) for key, scenario in scenarios.items()}
    if suite_plan != groups or metadata.get("source_requirement_hashes") != requirement_hashes or metadata.get("source_use_case_hashes") != scenario_hashes:
        return None
    context_binding = _context_binding(project, context, requirements, metadata)
    if not context_binding:
        return None
    context_proof, applied_context = context_binding
    if not isinstance(payload.get("test_cases"), list) or not isinstance(payload.get("generation_tasks"), list):
        return None
    cases = _case_rows(payload["test_cases"])
    active = [row for row in cases if row["status"] != "Deprecated"]
    if not active or not _versioned_cases(cases):
        return None
    precheck = _mapping(_mapping(payload.get("generation_evidence")).get("design_precheck"))
    assessment = _mapping(payload.get("substantive_assessment"))
    content_hash = _reviewed_rows_hash(active)
    reviewed_sources = {
        "requirement_hashes": requirement_hashes,
        "context": "absent" if context_proof["context_omitted"] else "applied",
        **({"context_hash": context_proof["applied_context_hash"]} if not context_proof["context_omitted"] else {}),
    }
    if (
        type(precheck.get("passed")) is not bool
        or precheck.get("case_content_hash") != content_hash
        or assessment.get("reviewed_case_content_hash") != content_hash
        or assessment.get("reviewed_source_binding") != reviewed_sources
        or not _is_hash(assessment.get("input_hash"))
        or precheck.get("assessment_input_hash") != assessment["input_hash"]
        or assessment.get("rubric") != rubric
        or assessment.get("scope") != "whole_requirements"
    ):
        return None
    design_status, reason = _design_status(payload, assessment, precheck, active, requirements, scenarios, applied_context)
    parent_snapshot_id = metadata.get("source_test_cases_snapshot_id")
    if parent_snapshot_id is not None and not _text(parent_snapshot_id):
        return None
    return {
        "schema_version": _PROOF_VERSION,
        "requirements_snapshot_id": baseline.snapshot_id,
        "required_requirement_ids": [row.id for row in requirements],
        "requirement_hashes": requirement_hashes,
        "scenario_snapshot_id": plan_snapshot_id,
        "scenario_review_id": review_id,
        "scenario_plan_hash": case_source_hash([group.model_dump(mode="json") for group in groups]),
        **({"reviewed_suite_snapshot_id": parent_snapshot_id} if parent_snapshot_id is not None else {}),
        **context_proof,
        **({"sync_review_binding": sync_binding} if sync_binding is not None else {}),
        "project_decisions_hash": case_source_hash(_without_null_fields(project.project_decisions)),
        "active_case_count": len(active),
        "deprecated_case_count": len(cases) - len(active),
        "case_content_hash": content_hash,
        "saved_cases_hash": case_source_hash(cases),
        "assessment_input_hash": assessment["input_hash"],
        "assessment_hash": case_source_hash(_without_null_fields(assessment)),
        "assessment_rubric": rubric,
        "structural_precheck_passed": precheck["passed"],
        "design_status": design_status,
        "reason_code": reason,
    }


def _scope_dispositions(project):
    baseline = _current_snapshot(project, "requirements")
    if not baseline:
        return [], []
    try:
        rows = [Requirement.model_validate(row) for row in baseline.payload.get("requirements", [])]
    except TypeError, ValidationError:
        return [], []
    active = [row for row in rows if row.lifecycle_status == "active" and row.review_status != "Rejected"]
    deferred = list(dict.fromkeys(row.id for row in active if row.clarification_status == "deferred"))
    excluded = list(dict.fromkeys(row.id for row in active if row.clarification_status == "excluded"))
    return deferred, excluded


def resolve_suite_quality(project, *, raw_suite=None):
    """Project current proof without modifying raw evidence or any legacy approval gate."""
    suite = raw_suite if raw_suite is not None else project.current_snapshots.get("test_cases")
    if not suite or suite.project_id != project.project_id or suite.stage != "test_cases":
        return None
    metadata = suite.metadata
    state = project.stage_state.get("test_cases")
    plan_id = _recorded_sources(metadata)["use_cases"]
    if not _text(plan_id) or _source_disagrees(metadata, "use_cases"):
        plan_id = None
    scenario_decision, scenario_review_id = _human_decision(project, "use_cases", plan_id)
    human_acceptance, _ = _human_decision(project, "test_cases", suite.snapshot_id)
    deferred, excluded = _scope_dispositions(project)
    saved_rows = suite.payload.get("test_cases")
    rows = [row for row in saved_rows if isinstance(row, dict)] if isinstance(saved_rows, list) else []
    active = [row for row in rows if row.get("status") != "Deprecated"]
    try:
        setup_blocked = any(item["status"] == "missing" for row in _case_rows(active) for item in row["execution_prerequisites"])
    except ValueError, TypeError, ValidationError:
        setup_blocked = False
    reasons = []
    source_freshness = "unknown"
    design_status = "unknown"
    declared_setup = "blocked" if setup_blocked else "unknown"
    proof = _mapping(metadata.get("suite_design_proof"))
    if _source_is_stale(project, suite):
        source_freshness = "stale"
        reasons.append("sources_stale")
    elif (
        state
        and state.current_snapshot_id == suite.snapshot_id
        and suite.operation in _PROOF_OPERATIONS
        and "suite_design_proof" in metadata
        and ("sync_review_binding" not in metadata or suite.operation == "test_cases.review")
    ):
        recomputed = build_suite_design_proof(project, suite.payload, metadata, plan_snapshot_id=plan_id)
        parent_id = plan_id if suite.operation == "testcases.generate" else proof.get("reviewed_suite_snapshot_id")
        if (
            recomputed
            and _text(parent_id)
            and parent_id == suite.source_snapshot_id
            and case_source_hash(proof) == case_source_hash({**recomputed, "suite_snapshot_id": suite.snapshot_id})
        ):
            source_freshness = "current"
            design_status = recomputed["design_status"]
            declared_setup = "blocked" if setup_blocked else "not_assessed"
            reasons.append(recomputed["reason_code"])
        else:
            reasons.append("design_proof_unverified")
    else:
        reasons.append("design_proof_missing")
    if setup_blocked:
        reasons.append("declared_setup_missing")
    return SuiteQualityProjection(
        suite_snapshot_id=suite.snapshot_id,
        source_freshness=source_freshness,
        design_status=design_status,
        scenario_decision=scenario_decision,
        scenario_snapshot_id=plan_id,
        scenario_review_id=scenario_review_id,
        human_acceptance=human_acceptance,
        declared_setup=declared_setup,
        required_requirement_ids=[row.id for row in _approved_scope(project)],
        deferred_requirement_ids=deferred,
        excluded_requirement_ids=excluded,
        active_case_count=len(active),
        deprecated_case_count=len(rows) - len(active),
        reason_codes=reasons,
    )
