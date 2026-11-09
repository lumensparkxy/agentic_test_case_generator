"""Bounded repair for one case-cited gap in an independently assessed saved suite."""

from dataclasses import dataclass
import json
import logging
import os
import re

from fastapi import HTTPException
from google.cloud.firestore_v1 import FieldFilter
from pydantic import ValidationError

from ..agents.adk_runtime import bounded_model_requests
from ..agents.substantive_review import RUBRIC, bind_reviewed_inputs, review_input, validate_assessment
from ..agents.test_case_agent import refine_test_cases
from ..agents.test_case_review import _resolve_test_case_workflow_settings
from ..config import DEFAULT_MODEL_NAME, get_generation_settings, model_scope
from ..contracts.grounding import EnrichInput
from ..contracts.projects import QaProjectStageSnapshot
from ..contracts.requirements import Requirement, RequirementCoveragePlan, WorkflowSettings
from ..contracts.selected_case_repair import SelectedCaseRepairResult, SelectedCaseRepairStatus, SelectedCaseRepairTarget
from ..contracts.test_cases import GenerateTestCasesResponse, RefineTestCasesInput, TestCase, TestCaseTemplate
from .approval_scenario_worker import approval_test_cases_enabled
from .audit_service import complete_workflow_run, start_workflow_run
from .billing_service import enforce_billing_access
from .firestore_repository import get_required_firestore_client
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope
from .project_draft_admission import (
    REPAIR_OPERATION,
    claim_draft_attempt,
    draft_admission_client,
    read_selected_repair_admission,
    read_selected_repair_result,
    release_draft_attempt,
)
from .project_inputs import behavioural_version
from .quality_feedback import digest as finding_digest
from .requirement_scope import approved_generation_requirements
from .scoped_refinement import _REPAIR_METADATA, merge_scoped_refinement
from .suite_quality import _context_binding, resolve_suite_quality
from .test_case_quality import case_source_hash, placeholder_reason
from .workflow_project_service import QA_PROJECTS_COLLECTION, get_project, get_project_stage_snapshot, get_project_summary


_LOG = logging.getLogger(__name__)
_BILLING_KEY = "testcases.refine"
_MAX_REQUESTS = 8
_DEADLINE_SECONDS = 180
_SETTINGS = WorkflowSettings(max_iterations=1, timeout_seconds=90, retry_attempts=0)
_TEMPLATE = TestCaseTemplate(name="Selected case repair", format="json", fields=[])
_OBSERVATION = re.compile(r"^(?:observe|inspect|read|verify|compare|confirm|check)\s+\S", re.I)
_MUTATION = re.compile(
    r"(?:\b(?:then|and|before|after|to|by)\s+|[;.!?]\s+)"
    r"(?:click|submit|delete|save|set|update|type|select|cancel|create|enter|change|add|edit|remove|approve|press|send|open|navigate|run|execute)\b",
    re.I,
)
_WEAK_EXPECTED = re.compile(r"^(?:works|passes|correct|as expected|no errors?|success(?:ful)?|behavior (?:is correct|works)|the test passes)[.!]?$", re.I)


@dataclass(frozen=True)
class _RepairSource:
    suite: QaProjectStageSnapshot
    baseline: GenerateTestCasesResponse
    case: TestCase
    requirement: Requirement
    plan: RequirementCoveragePlan
    context: EnrichInput | None
    snapshot_ids: dict[str, str | None]
    source_inputs: dict
    targets: list[SelectedCaseRepairTarget]


def _receipt(project_id, actor, request_id, *, request_fingerprint=None):
    client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
    snapshot = read_selected_repair_result(client, project_id=project_id, actor_id=actor.sub, request_id=request_id, request_fingerprint=request_fingerprint)
    if not snapshot:
        return None
    metadata = snapshot["metadata"]
    binding = metadata["selected_repair"]
    rows = snapshot["payload"]["test_cases"]
    case = next(row for row in rows if row["id"] == binding["case_id"])
    return SelectedCaseRepairResult(
        request_id=snapshot["request_id"],
        source_snapshot_id=metadata["source_test_cases_snapshot_id"],
        result_snapshot_id=snapshot["snapshot_id"],
        project_revision=snapshot["project_revision"],
        case_id=case["id"],
        before_case_version_id=binding["case_version_id"],
        after_case_version_id=case["artifact_version_id"],
        preserved_case_count=len(rows) - 1,
    )


def _defect_recorded(client, project_id, suite, case):
    collection = client.collection(QA_PROJECTS_COLLECTION).document(project_id).collection("quality_findings")
    exact_assertions = finding_digest(
        [{key: row.get(key) for key in ("id", "steps", "expected_result")} for row in suite.payload["test_cases"] if row["id"] == case.id]
    )
    return any(
        row.get("category") == "application_defect"
        and row.get("status") not in {"verified", "excluded"}
        and (row.get("source_test_case_snapshot_id") == suite.snapshot_id or row.get("case_assertions_hash") == exact_assertions)
        for document in collection.where(filter=FieldFilter("test_case_ids", "array_contains", case.id)).stream()
        if (row := document.to_dict())
    )


def _matching_targets(suite, case, requirement, scenario_id, assessment):
    diagnostics = assessment.get("diagnostics") or {}
    grounding = [row for row in assessment.get("case_grounding", []) if row.get("test_case_id") == case.id]
    if (
        assessment.get("rubric") != RUBRIC
        or assessment.get("model_status") == "unknown"
        or assessment.get("scope") != "whole_requirements"
        or assessment.get("status") != "incomplete"
        or assessment.get("design_status") != "incomplete"
        or not isinstance(diagnostics, dict)
        or diagnostics.get("failure_reason")
        or diagnostics.get("unverified_group_count", 0) != 0
        or len(grounding) != 1
        or any(item.get("required") is not False for item in grounding[0].get("prerequisites", []))
    ):
        return []
    steps = {step.step: step for step in case.steps}
    if len(steps) != len(case.steps):
        return []
    targets = {}
    for item in assessment.get("obligations", []):
        if item.get("status") != "unmet" or item.get("requirement_id") != requirement.id:
            continue
        quote, obligation, reason = (item.get(key) for key in ("source_quote", "obligation", "reason"))
        evidence = item.get("evidence")
        if (
            not all(isinstance(value, str) and value.strip() for value in (quote, obligation, reason))
            or not any(quote in (text or "") for text in (requirement.text, requirement.source_excerpt))
            or not isinstance(evidence, list)
            or not evidence
        ):
            continue
        citations = []
        for citation in evidence:
            number = citation.get("step")
            if type(number) is not int or number not in steps or citation.get("test_case_id") != case.id:
                break
            step = steps[number]
            if citation.get("action") != step.action or citation.get("expected") != step.expected:
                break
            citations.append(number)
        if len(citations) != len(evidence):
            continue
        target_id = "repair_" + case_source_hash([suite.snapshot_id, assessment["input_hash"], case.id, case.artifact_version_id, item])
        targets[target_id] = SelectedCaseRepairTarget(
            target_id=target_id,
            case_id=case.id,
            case_version_id=case.artifact_version_id,
            requirement_id=requirement.id,
            scenario_id=scenario_id,
            obligation=obligation,
            source_quote=quote,
            reason=reason,
            step_numbers=sorted(set(citations)),
        )
    return list(targets.values())[:20]


def _repair_source(client, project, actor, snapshot_id, case_id):
    state = project.stage_state.get("test_cases")
    if project.status != "active" or not state or state.stale or state.current_snapshot_id != snapshot_id:
        return None
    suite = get_project_stage_snapshot(project.project_id, snapshot_id, actor=actor)
    quality = resolve_suite_quality(project, raw_suite=suite)
    if (
        quality is None
        or quality.source_freshness != "current"
        or quality.design_status != "incomplete"
        or quality.scenario_decision != "approved"
        or quality.human_acceptance == "changes_requested"
    ):
        return None
    try:
        baseline = GenerateTestCasesResponse.model_validate(suite.payload)
        cases = [case for case in baseline.test_cases if case.id == case_id]
        if len(cases) != 1 or len({case.id for case in baseline.test_cases}) != len(baseline.test_cases):
            return None
        case = cases[0]
        if (
            case.status == "Deprecated"
            or placeholder_reason(case)
            or not case.artifact_version_id
            or not case.artifact_version_number
            or any(item.status == "missing" for item in case.execution_prerequisites)
            or len(case.linked_requirement_ids) != 1
            or len(case.scenario_refs) != 1
        ):
            return None
        requirements = approved_generation_requirements(project)
        required = next((row for row in requirements if row.id == case.linked_requirement_ids[0]), None)
        plan_snapshot = project.current_snapshots["use_cases"]
        plans = [group for group in baseline.coverage_plan if required and group.requirement_id == required.id]
        scenarios = [row for group in plans for row in group.scenarios if row.id == case.scenario_refs[0]]
        if len(plans) != 1 or len(scenarios) != 1:
            return None
        context_snapshot = project.current_snapshots.get("context")
        context_binding = _context_binding(project, context_snapshot, requirements, suite.metadata)
        if not context_binding:
            return None
        applied_context = context_binding[1]
        context = EnrichInput.model_validate(applied_context).model_copy(update={"requirements": [required]}) if applied_context else None
        assessment = baseline.substantive_assessment or {}
        targets = _matching_targets(suite, case, required, scenarios[0].id, assessment)
        if not targets or _defect_recorded(client, project.project_id, suite, case):
            return None
        return _RepairSource(
            suite=suite,
            baseline=baseline,
            case=case,
            requirement=required,
            plan=plans[0].model_copy(update={"scenarios": scenarios}),
            context=context,
            snapshot_ids={
                "requirements": project.current_snapshots["requirements"].snapshot_id,
                "context": context_snapshot.snapshot_id if context_snapshot else None,
                "use_cases": plan_snapshot.snapshot_id,
                "test_cases": suite.snapshot_id,
            },
            source_inputs={
                "source_coverage_plan": suite.payload["coverage_plan"],
                "source_requirement_analysis": suite.payload.get("requirement_analysis") or [],
            },
            targets=targets,
        )
    except TypeError, ValueError, KeyError, AttributeError:
        return None


def _candidate_rejected():
    return HTTPException(
        422, {"code": "selected_repair_unverified", "message": "The proposed observation could not be verified. The saved suite was preserved."}
    )


def _selected_obligation(target):
    return target.model_dump(include={"requirement_id", "obligation", "source_quote"})


def _verified_observation(response, source, target):
    prior = source.case
    if response.workflow_diagnostics.used_fallback or response.generation_tasks or len(response.test_cases) != 1:
        raise _candidate_rejected()
    candidate = response.test_cases[0]
    unchanged_fields = _REPAIR_METADATA | {"steps"}
    if (
        candidate.status == "Deprecated"
        or candidate.generation_source in {"deterministic_full_fallback", "deterministic_coverage_completion"}
        or candidate.model_dump(exclude=unchanged_fields) != prior.model_dump(exclude=unchanged_fields)
        or len(candidate.steps) != len(prior.steps) + 1
        or candidate.steps[:-1] != prior.steps
    ):
        raise _candidate_rejected()
    added = candidate.steps[-1]
    action, expected = added.action.strip(), added.expected.strip()
    if (
        added.step != max(step.step for step in prior.steps) + 1
        or not _OBSERVATION.match(action)
        or _MUTATION.search(action)
        or not expected
        or len(expected.split()) < 3
        or _WEAK_EXPECTED.fullmatch(expected)
        or added.test_data is not None
        or any((step.action, step.expected) == (added.action, added.expected) for step in prior.steps)
        or placeholder_reason({"steps": [added.model_dump(mode="json")]})
    ):
        raise _candidate_rejected()

    assessment = response.substantive_assessment or {}
    data, input_hash, _ = review_input(
        [candidate], [source.requirement], source.context, scope_plan=[source.plan], selected_obligation=_selected_obligation(target)
    )
    diagnostics = assessment.get("diagnostics") or {}
    if (
        assessment.get("rubric") != RUBRIC
        or assessment.get("scope") != "selected_scenarios"
        or assessment.get("status") != "assessed_complete"
        or assessment.get("design_status") != "assessed_complete"
        or assessment.get("model_status") == "unknown"
        or assessment.get("input_hash") != input_hash
        or not isinstance(diagnostics, dict)
        or diagnostics.get("failure_reason")
        or diagnostics.get("unverified_group_count", 0) != 0
    ):
        raise _candidate_rejected()
    try:
        evidence = {key: assessment[key] for key in ("input_hash", "obligations", "case_grounding")}
        checked = bind_reviewed_inputs(validate_assessment(json.dumps(evidence), data, input_hash), data)
        matching = [
            row
            for row in checked["obligations"]
            if row["requirement_id"] == target.requirement_id and row["obligation"] == target.obligation and row["source_quote"] == target.source_quote
        ]
        closed = (
            len(matching) == 1
            and matching[0]["status"] == "covered"
            and any(citation["test_case_id"] == prior.id and citation["step"] == added.step for citation in matching[0]["evidence"])
        )
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise _candidate_rejected() from exc
    if (
        not closed
        or checked["design_status"] != "assessed_complete"
        or checked["status"] != "assessed_complete"
        or checked["reviewed_case_content_hash"] != assessment.get("reviewed_case_content_hash")
        or checked["reviewed_source_binding"] != assessment.get("reviewed_source_binding")
    ):
        raise _candidate_rejected()
    # Publish original case facts and provenance with just the added step.
    response.test_cases = [prior.model_copy(update={"steps": [*prior.steps, added]})]
    repaired = merge_scoped_refinement(response, source.baseline, [prior.id], source.source_inputs)
    repaired.generation_evidence.design_precheck = None  # The scoped reviewer did not assess the combined suite.
    return repaired, {"target": target.model_dump(mode="json"), "added_step": added.step, "assessment_input_hash": input_hash, "rubric": RUBRIC}


def _model_input(project_id, revision, source, target):
    payload = RefineTestCasesInput(
        project_id=project_id,
        base_project_revision=revision,
        selected_test_case_ids=[source.case.id],
        requirements=[source.requirement],
        context=source.context,
        test_cases=[source.case],
        workflow_settings=_SETTINGS,
        template=_TEMPLATE,
        feedback=(
            "Preserve every case field, action, expected result, step number, setup and literal exactly. "
            "Append one read-only observation, without new data, mutations, credentials or assumptions. "
            "Address this exact obligation and let the independent reviewer cite the new step. "
            f"Obligation: {target.obligation}\nExact source quote: {target.source_quote}"
        ),
    )
    inputs = {
        "source_coverage_plan": [source.plan.model_dump(mode="json")],
        "selected_obligation": _selected_obligation(target),
        "source_requirement_analysis": [
            row for row in source.source_inputs["source_requirement_analysis"] if row.get("requirement_id") == source.requirement.id
        ],
    }
    return payload, inputs


def selected_case_repair_status(project_id, actor, *, snapshot_id=None, case_id=None, request_id=None):
    if (snapshot_id is None) != (case_id is None):
        raise HTTPException(422, "Supply the saved snapshot and case ID together.")
    get_project_summary(project_id, actor=actor)
    result = _receipt(project_id, actor, request_id) if request_id else None
    if result and snapshot_id and (result.source_snapshot_id, result.case_id) != (snapshot_id, case_id):
        result = None
    client = draft_admission_client(read_only=True)
    if client is None:
        return SelectedCaseRepairStatus(result=result)
    admission = read_selected_repair_admission(client, project_id, actor.sub)
    enabled = approval_test_cases_enabled()
    source = None
    if enabled and snapshot_id:
        project = get_project(project_id, actor=actor)
        source = _repair_source(client, project, actor, snapshot_id, case_id)
    return SelectedCaseRepairStatus(enabled=enabled, **admission, targets=source.targets if source else [], result=result)


def repair_selected_case(project_id, payload, actor, request_id):
    from ..routers.testcases import _publish_bound_suite

    fingerprint = case_source_hash(payload.model_dump(mode="json"))
    if (result := _receipt(project_id, actor, request_id, request_fingerprint=fingerprint)) is not None:
        return result
    client = draft_admission_client()
    if client is None or not approval_test_cases_enabled():
        raise HTTPException(409, {"code": "selected_repair_disabled", "message": "Selected-case repair is unavailable in this environment."})
    project = get_project(project_id, actor=actor)
    if project.current_revision != payload.base_project_revision:
        raise HTTPException(409, {"code": "project_draft_sources_changed", "message": "Reload the project before repairing a case."})
    source = _repair_source(client, project, actor, payload.snapshot_id, payload.case_id)
    target = next((item for item in source.targets if item.target_id == payload.target_id), None) if source else None
    if not target or target.case_version_id != payload.case_version_id:
        raise HTTPException(409, {"code": "selected_repair_target_changed", "message": "This case version or verified obligation is unavailable."})
    model = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
    model_payload, model_inputs = _model_input(project_id, payload.base_project_revision, source, target)
    settings_fingerprint = case_source_hash(
        [
            "selected-case-repair-v1",
            model,
            behavioural_version(),
            _resolve_test_case_workflow_settings(_SETTINGS),
            get_generation_settings().model_dump(mode="json"),
            _TEMPLATE.model_dump(mode="json"),
            RUBRIC,
            _MAX_REQUESTS,
            _DEADLINE_SECONDS,
        ]
    )
    billing_context = enforce_billing_access(current_user=actor, billing_key=_BILLING_KEY)
    binding = {
        "target_id": target.target_id,
        "case_id": source.case.id,
        "case_version_id": source.case.artifact_version_id,
        "case_version_number": source.case.artifact_version_number,
        "request_fingerprint": fingerprint,
    }
    claim = claim_draft_attempt(
        client,
        project_id=project_id,
        actor_id=actor.sub,
        request_id=request_id,
        operation=REPAIR_OPERATION,
        source_snapshot_ids=source.snapshot_ids,
        base_revision=payload.base_project_revision,
        max_requests=_MAX_REQUESTS,
        deadline_seconds=_DEADLINE_SECONDS,
        settings_fingerprint=settings_fingerprint,
        selected_repair=binding,
    )
    if claim.result_snapshot_id:
        return _confirmed_result(project_id, actor, request_id, fingerprint)
    run_id = None
    try:
        manifest = prepare_run(
            "test_cases", actor, project_id, request_id, requirement_ids=[source.requirement.id], memory_bypass=True, input_fingerprint=fingerprint
        )
        with guidance_scope(manifest):
            run_id = start_workflow_run(
                operation=REPAIR_OPERATION, actor=actor, request_id=request_id, metadata={"source_snapshot_id": payload.snapshot_id, "case_id": source.case.id}
            )
            with bounded_model_requests(claim.reserve), model_scope(model):
                raw = refine_test_cases(
                    model_payload, actor_user_id=actor.sub, request_id=request_id, workflow_run_id=run_id, operation=REPAIR_OPERATION, **model_inputs
                )
                try:
                    repaired, evidence = _verified_observation(GenerateTestCasesResponse.model_validate(raw), source, target)
                except ValidationError as exc:
                    raise _candidate_rejected() from exc
            publication_payload = model_payload.model_copy(update={"test_cases": source.baseline.test_cases})
            _publish_bound_suite(
                publication_payload,
                repaired,
                operation=REPAIR_OPERATION,
                billing_key=_BILLING_KEY,
                actor=actor,
                request_id=request_id,
                fingerprint=fingerprint,
                run_id=run_id,
                billing_context=billing_context,
                base_revision=payload.base_project_revision,
                source_snapshot_id=source.snapshot_ids["use_cases"],
                refinement_source=source.suite,
                changed_case_ids={source.case.id},
                repair_metadata={"selected_repair": binding, "selected_repair_evidence": evidence},
                result_metadata={"test_cases_modified_count": 1, "test_cases_total": len(repaired.test_cases), "approved": False},
                stage_publication_guard=lambda transaction, _client, snapshot: claim.prepare_publication(transaction, snapshot),
            )
    except Exception:
        try:
            if claim.read_result():
                return _confirmed_result(project_id, actor, request_id, fingerprint)
            stopped = claim.stop()
            if stopped and run_id:
                complete_workflow_run(run_id, status="failed", metadata={"reason": "selected_repair_unconfirmed"})
        except Exception as exc:
            _LOG.warning("Could not reconcile selected-case repair (%s)", type(exc).__name__)
        raise
    return _confirmed_result(project_id, actor, request_id, fingerprint)


def _confirmed_result(project_id, actor, request_id, fingerprint):
    result = _receipt(project_id, actor, request_id, request_fingerprint=fingerprint)
    if result is None:
        raise HTTPException(503, {"code": "selected_repair_unconfirmed", "message": "Check this repair request's saved status before retrying."})
    return result


def release_selected_case_repair(project_id, actor, operation_id, fence):
    client = draft_admission_client()
    if client is None:
        raise HTTPException(409, {"code": "selected_repair_disabled", "message": "Selected-case repair recovery is unavailable in this environment."})
    return release_draft_attempt(client, project_id, actor.sub, operation_id, fence, selected_repair=True)
