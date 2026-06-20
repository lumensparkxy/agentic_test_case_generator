"""Use-case planning agent with bounded parallel shard coordination."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google.adk.agents import SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .analysis_agent import build_requirement_analysis_agent, fallback_requirement_analysis, normalize_requirement_analysis
from .test_case_agent import (
    STATE_COVERAGE_PLAN,
    STATE_REQUIREMENT_ANALYSIS,
    _append_unique_message,
    _build_coverage_planner_agent,
    _combined_event_text,
    _get_model_settings_or_none,
    _log_test_case_workflow,
    _new_workflow_diagnostics,
    _prepare_workflow_inputs,
    _record_event_error,
    _record_parser_failure,
    _record_parser_recovery,
)
from .test_case_coverage import _dedupe_preserve, _fallback_coverage_plan, _normalize_coverage_plan
from .test_case_hydration import _hydrate_coverage_plan, _hydrate_requirement_analysis
from .test_case_review import DEFAULT_TEST_CASE_THRESHOLD, _resolve_test_case_workflow_settings
from ..models import GenerateTestCasesInput, Requirement, WorkflowSettings
from ..observability.logging import bind_log_context, reset_log_context
from ..utils.llm_json import parse_coverage_plan_json_detailed, parse_requirement_analysis_json_detailed
from ..utils.workflow_diagnostics import public_workflow_diagnostics

DEFAULT_USE_CASE_WORKER_COUNT = 3
SMALL_INPUT_SEQUENTIAL_LIMIT = 3


@dataclass(frozen=True)
class _UseCaseShard:
    index: int
    requirements: List[Requirement]

    @property
    def shard_id(self) -> str:
        return f"shard-{self.index:02d}"


@dataclass(frozen=True)
class _UseCaseShardResult:
    shard: _UseCaseShard
    requirement_analysis: List[Dict[str, Any]]
    coverage_plan: List[Dict[str, Any]]
    diagnostics: Dict[str, Any]
    used_fallback: bool = False
    failed: bool = False


def _new_use_case_diagnostics(
    *,
    shard_count: int = 0,
    worker_count: int = 0,
    attempt_count: int = 1,
) -> Dict[str, Any]:
    diagnostics = _new_workflow_diagnostics(attempt_count=attempt_count)
    diagnostics.update(
        {
            "shard_count": shard_count,
            "worker_count": worker_count,
            "failed_shard_count": 0,
            "fallback_shard_count": 0,
            "merge_warnings": [],
        }
    )
    return diagnostics


def _plan_use_case_shards(requirements: List[Requirement], *, max_workers: int = DEFAULT_USE_CASE_WORKER_COUNT) -> List[_UseCaseShard]:
    if len(requirements) <= SMALL_INPUT_SEQUENTIAL_LIMIT:
        return [_UseCaseShard(index=1, requirements=list(requirements))]

    worker_count = max(1, min(max_workers, len(requirements)))
    shard_size = (len(requirements) + worker_count - 1) // worker_count
    shards: List[_UseCaseShard] = []
    for index, start in enumerate(range(0, len(requirements), shard_size), start=1):
        shards.append(_UseCaseShard(index=index, requirements=list(requirements[start : start + shard_size])))
    return shards


def _canonicalize_coverage_scenario_ids(raw_plan: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    normalized_plan: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for plan_item in raw_plan:
        requirement_id = str(plan_item.get("requirement_id") or "").strip()
        scenarios: List[Dict[str, Any]] = []
        for index, scenario in enumerate(plan_item.get("scenarios") or [], start=1):
            canonical_id = f"{requirement_id}-SCN-{index:02d}"
            original_id = str(scenario.get("id") or "").strip()
            if original_id != canonical_id:
                label = original_id or "<missing>"
                warnings.append(f"Normalized scenario id {label} to {canonical_id}.")
            scenarios.append({**scenario, "id": canonical_id, "requirement_id": requirement_id})
        normalized_plan.append({**plan_item, "scenarios": scenarios})

    return normalized_plan, _dedupe_preserve(warnings)


def _use_case_workflow_context(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": kwargs.get("request_id"),
        "workflow_run_id": kwargs.get("workflow_run_id"),
        "actor_user_id": kwargs.get("actor_user_id"),
        "operation": kwargs.get("operation") or "orchestrator.use_cases.generate",
    }


async def _run_single_use_case_shard_workflow_async(
    *,
    shard: _UseCaseShard,
    context: Optional[Any],
    requirements_text: str,
    context_text: str,
    model: str,
    human_feedback: Optional[str] = None,
    workflow_settings: Optional[WorkflowSettings] = None,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_settings = _resolve_test_case_workflow_settings(workflow_settings)
    timeout_seconds = resolved_settings["timeout_seconds"]
    diagnostics = _new_use_case_diagnostics(shard_count=1, worker_count=1)

    root_agent = SequentialAgent(
        name=f"UseCasePlanningPipeline{shard.index:02d}",
        sub_agents=[
            build_requirement_analysis_agent(
                model,
                requirements_text,
                context_text,
                output_key=STATE_REQUIREMENT_ANALYSIS,
                human_feedback=human_feedback,
            ),
            _build_coverage_planner_agent(model, requirements_text, context_text, human_feedback=human_feedback),
        ],
        description="Plans requirement analysis and scenario coverage without generating test cases",
    )
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="use_case_planner",
        session_service=session_service,
    )
    user_id = str(actor_user_id or f"use-case-{shard.shard_id}")
    session = await session_service.create_session(
        app_name="use_case_planner",
        user_id=user_id,
        state={
            STATE_COVERAGE_PLAN: "[]",
            STATE_REQUIREMENT_ANALYSIS: "[]",
        },
    )

    current_coverage_plan: List[Dict[str, Any]] = []
    current_requirement_analysis: List[Dict[str, Any]] = []
    message_text = "Generate requirement analysis and a scenario coverage plan for this requirement shard only. Do not generate detailed test cases."

    async def _consume_events() -> None:
        nonlocal current_coverage_plan, current_requirement_analysis

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message_text)]),
        ):
            author = getattr(event, "author", "unknown")
            _record_event_error(diagnostics, author, event)

            if getattr(event, "partial", False):
                continue

            text = _combined_event_text(event)
            if not text:
                continue

            if author == "RequirementAnalysisAgent":
                parsed_requirement_analysis, parse_error = parse_requirement_analysis_json_detailed(text)
                if parsed_requirement_analysis:
                    current_requirement_analysis = normalize_requirement_analysis(parsed_requirement_analysis, shard.requirements)
                    _record_parser_recovery(diagnostics, author, parse_error, text, artifact_label="requirement-analysis")
                else:
                    _record_parser_failure(diagnostics, author, parse_error, text)

            if author == "CoveragePlannerAgent":
                parsed_coverage_plan, parse_error = parse_coverage_plan_json_detailed(text)
                if parsed_coverage_plan:
                    current_coverage_plan = _normalize_coverage_plan(parsed_coverage_plan, shard.requirements)
                    _record_parser_recovery(diagnostics, author, parse_error, text, artifact_label="coverage-plan")
                else:
                    _record_parser_failure(diagnostics, author, parse_error, text)

    try:
        if timeout_seconds is not None:
            await asyncio.wait_for(_consume_events(), timeout=timeout_seconds)
        else:
            await _consume_events()
    except asyncio.TimeoutError:
        diagnostics["status"] = "partial"
        diagnostics["timed_out"] = True
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "timeout"
        _append_unique_message(
            diagnostics["warnings"],
            f"Use-case shard {shard.shard_id} timed out after {timeout_seconds} second(s).",
        )

    updated_session = await session_service.get_session(
        app_name="use_case_planner",
        user_id=user_id,
        session_id=session.id,
    )
    session_state = updated_session.state if updated_session else session.state

    state_requirement_analysis_raw = session_state.get(STATE_REQUIREMENT_ANALYSIS, "[]")
    state_requirement_analysis, state_requirement_analysis_error = parse_requirement_analysis_json_detailed(state_requirement_analysis_raw)
    had_event_requirement_analysis = bool(current_requirement_analysis)
    if state_requirement_analysis:
        current_requirement_analysis = normalize_requirement_analysis(state_requirement_analysis, shard.requirements)
        if not had_event_requirement_analysis:
            _record_parser_recovery(
                diagnostics,
                "SessionStateRequirementAnalysis",
                state_requirement_analysis_error,
                state_requirement_analysis_raw,
                artifact_label="requirement-analysis",
            )
    elif str(state_requirement_analysis_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateRequirementAnalysis", state_requirement_analysis_error, state_requirement_analysis_raw)

    state_coverage_plan_raw = session_state.get(STATE_COVERAGE_PLAN, "[]")
    state_coverage_plan, state_coverage_plan_error = parse_coverage_plan_json_detailed(state_coverage_plan_raw)
    had_event_coverage_plan = bool(current_coverage_plan)
    if state_coverage_plan:
        current_coverage_plan = _normalize_coverage_plan(state_coverage_plan, shard.requirements)
        if not had_event_coverage_plan:
            _record_parser_recovery(
                diagnostics,
                "SessionStateCoveragePlan",
                state_coverage_plan_error,
                state_coverage_plan_raw,
                artifact_label="coverage-plan",
            )
    elif str(state_coverage_plan_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateCoveragePlan", state_coverage_plan_error, state_coverage_plan_raw)

    if not current_requirement_analysis:
        current_requirement_analysis = fallback_requirement_analysis(shard.requirements)
        diagnostics["status"] = "partial"
        diagnostics["used_fallback"] = True
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "fallback_generated_artifacts"
        _append_unique_message(diagnostics["warnings"], f"Use-case shard {shard.shard_id} used fallback requirement analysis.")

    if not current_coverage_plan:
        current_coverage_plan = _fallback_coverage_plan(shard.requirements)
        diagnostics["status"] = "partial"
        diagnostics["used_fallback"] = True
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "fallback_generated_artifacts"
        _append_unique_message(diagnostics["warnings"], f"Use-case shard {shard.shard_id} used fallback coverage planning.")

    return {
        "requirement_analysis": current_requirement_analysis,
        "coverage_plan": current_coverage_plan,
        "workflow_diagnostics": public_workflow_diagnostics(diagnostics),
    }


def _run_single_use_case_shard_workflow_sync(**kwargs: Any) -> Dict[str, Any]:
    try:
        asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return asyncio.run(_run_single_use_case_shard_workflow_async(**kwargs))
    except RuntimeError:
        return asyncio.run(_run_single_use_case_shard_workflow_async(**kwargs))


def _fallback_shard_result(shard: _UseCaseShard, *, reason: str, warning: str, failed: bool = False) -> _UseCaseShardResult:
    diagnostics = _new_use_case_diagnostics(shard_count=1, worker_count=1)
    diagnostics["status"] = "fallback"
    diagnostics["used_fallback"] = True
    diagnostics["failure_reason"] = reason
    diagnostics["fallback_shard_count"] = 1
    diagnostics["failed_shard_count"] = 1 if failed else 0
    _append_unique_message(diagnostics["warnings"], warning)
    return _UseCaseShardResult(
        shard=shard,
        requirement_analysis=fallback_requirement_analysis(shard.requirements),
        coverage_plan=_fallback_coverage_plan(shard.requirements),
        diagnostics=diagnostics,
        used_fallback=True,
        failed=failed,
    )


def _run_shard_with_fallback(
    *,
    shard: _UseCaseShard,
    context: Optional[Any],
    model: str,
    human_feedback: Optional[str],
    workflow_settings: Optional[WorkflowSettings],
    actor_user_id: Optional[str],
) -> _UseCaseShardResult:
    requirements_text, context_text, _template_text = _prepare_workflow_inputs(
        shard.requirements,
        context,
        type("_Template", (), {"name": "Use Case Planning", "format": "structured", "fields": ["requirement_analysis", "coverage_plan"]})(),
    )
    try:
        workflow = _run_single_use_case_shard_workflow_sync(
            shard=shard,
            context=context,
            requirements_text=requirements_text,
            context_text=context_text,
            model=model,
            human_feedback=human_feedback,
            workflow_settings=workflow_settings,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        return _fallback_shard_result(
            shard,
            reason="shard_execution_error",
            warning=f"Use-case shard {shard.shard_id} failed and was replaced with deterministic fallback artifacts: {exc}",
            failed=True,
        )

    diagnostics = dict(workflow.get("workflow_diagnostics") or {})
    requirement_analysis = normalize_requirement_analysis(
        list(workflow.get("requirement_analysis") or fallback_requirement_analysis(shard.requirements)),
        shard.requirements,
    )
    coverage_plan = _normalize_coverage_plan(list(workflow.get("coverage_plan") or _fallback_coverage_plan(shard.requirements)), shard.requirements)
    used_fallback = bool(diagnostics.get("used_fallback"))
    return _UseCaseShardResult(
        shard=shard,
        requirement_analysis=requirement_analysis,
        coverage_plan=coverage_plan,
        diagnostics=diagnostics,
        used_fallback=used_fallback,
        failed=False,
    )


def _merge_shard_diagnostics(diagnostics: Dict[str, Any], shard_results: List[_UseCaseShardResult]) -> None:
    failed_shards = sum(1 for result in shard_results if result.failed)
    fallback_shards = sum(1 for result in shard_results if result.used_fallback)
    diagnostics["failed_shard_count"] = failed_shards
    diagnostics["fallback_shard_count"] = fallback_shards
    diagnostics["used_fallback"] = fallback_shards > 0

    for result in shard_results:
        shard_diagnostics = result.diagnostics or {}
        for key in ("parser_failures", "parser_recoveries", "warnings"):
            for message in shard_diagnostics.get(key) or []:
                _append_unique_message(diagnostics[key], str(message))
        if shard_diagnostics.get("timed_out"):
            diagnostics["timed_out"] = True
        if shard_diagnostics.get("stalled"):
            diagnostics["stalled"] = True
        if shard_diagnostics.get("max_iterations_reached"):
            diagnostics["max_iterations_reached"] = True

    if failed_shards:
        diagnostics["status"] = "partial"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "shard_fallback"
    elif fallback_shards:
        diagnostics["status"] = "partial"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "fallback_generated_artifacts"
    elif diagnostics["parser_failures"] or diagnostics["parser_recoveries"]:
        diagnostics["status"] = "partial"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or ("parser_failure" if diagnostics["parser_failures"] else None)


def _merge_use_case_shards(
    shard_results: List[_UseCaseShardResult],
    requirements: List[Requirement],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    raw_analysis: List[Dict[str, Any]] = []
    raw_coverage_plan: List[Dict[str, Any]] = []
    for result in sorted(shard_results, key=lambda item: item.shard.index):
        raw_analysis.extend(result.requirement_analysis)
        raw_coverage_plan.extend(result.coverage_plan)

    requirement_analysis = normalize_requirement_analysis(raw_analysis, requirements)
    coverage_plan = _normalize_coverage_plan(raw_coverage_plan, requirements)
    coverage_plan, merge_warnings = _canonicalize_coverage_scenario_ids(coverage_plan)
    return requirement_analysis, coverage_plan, merge_warnings


def _compute_use_case_metrics(
    requirement_analysis: List[Dict[str, Any]],
    coverage_plan: List[Dict[str, Any]],
    requirements: List[Requirement],
    merge_warnings: List[str],
) -> Dict[str, Any]:
    requirement_ids = [requirement.id for requirement in requirements]
    analysis_ids = {str(item.get("requirement_id") or "").strip() for item in requirement_analysis}
    plan_ids = {str(item.get("requirement_id") or "").strip() for item in coverage_plan}
    scenario_count = sum(len(item.get("scenarios") or []) for item in coverage_plan)
    happy_path_count = 0
    non_happy_path_count = 0
    must_have_count = 0
    scenario_ids: List[str] = []
    for item in coverage_plan:
        for scenario in item.get("scenarios") or []:
            scenario_ids.append(str(scenario.get("id") or "").strip())
            if scenario.get("scenario_type") == "Happy Path":
                happy_path_count += 1
            else:
                non_happy_path_count += 1
            if bool(scenario.get("must_have", True)):
                must_have_count += 1

    duplicate_scenario_ids = [scenario_id for scenario_id in _dedupe_preserve(scenario_ids) if scenario_ids.count(scenario_id) > 1]
    requirements_total = len(requirement_ids)
    complete_requirements = [requirement_id for requirement_id in requirement_ids if requirement_id in analysis_ids and requirement_id in plan_ids]
    return {
        "requirements_total": requirements_total,
        "requirements_with_analysis": len([requirement_id for requirement_id in requirement_ids if requirement_id in analysis_ids]),
        "requirements_with_coverage_plan": len([requirement_id for requirement_id in requirement_ids if requirement_id in plan_ids]),
        "use_case_plan_coverage_ratio": round(len(complete_requirements) / requirements_total, 2) if requirements_total else 1.0,
        "planned_scenarios_total": scenario_count,
        "happy_path_scenarios_total": happy_path_count,
        "non_happy_path_scenarios_total": non_happy_path_count,
        "must_have_scenarios_total": must_have_count,
        "scenario_id_count": len(scenario_ids),
        "duplicate_scenario_ids": duplicate_scenario_ids,
        "merge_warning_count": len(merge_warnings),
    }


def _heuristic_use_case_review(
    requirement_analysis: List[Dict[str, Any]],
    coverage_plan: List[Dict[str, Any]],
    requirements: List[Requirement],
    threshold: int,
    *,
    merge_warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    requirement_ids = [requirement.id for requirement in requirements]
    analysis_ids = [str(item.get("requirement_id") or "").strip() for item in requirement_analysis]
    plan_ids = [str(item.get("requirement_id") or "").strip() for item in coverage_plan]
    blocking_issues: List[str] = []
    suggestions: List[str] = []

    if len(analysis_ids) != len(requirement_ids) or set(analysis_ids) != set(requirement_ids):
        blocking_issues.append("Use-case response must contain exactly one requirement_analysis item per approved requirement.")
    if len(plan_ids) != len(requirement_ids) or set(plan_ids) != set(requirement_ids):
        blocking_issues.append("Use-case response must contain exactly one coverage_plan item per approved requirement.")

    scenario_ids: List[str] = []
    for requirement_id in requirement_ids:
        plan_item = next((item for item in coverage_plan if str(item.get("requirement_id") or "").strip() == requirement_id), None)
        scenarios = list((plan_item or {}).get("scenarios") or [])
        scenario_types = {str(scenario.get("scenario_type") or "").strip() for scenario in scenarios}
        scenario_ids.extend(str(scenario.get("id") or "").strip() for scenario in scenarios)
        if "Happy Path" not in scenario_types:
            blocking_issues.append(f"{requirement_id} is missing a Happy Path scenario.")
        if not any(scenario_type and scenario_type != "Happy Path" for scenario_type in scenario_types):
            blocking_issues.append(f"{requirement_id} is missing a non-happy-path scenario.")
        if not any(bool(scenario.get("must_have", True)) for scenario in scenarios):
            blocking_issues.append(f"{requirement_id} has no must-have scenario.")

    duplicate_ids = [scenario_id for scenario_id in _dedupe_preserve(scenario_ids) if scenario_ids.count(scenario_id) > 1]
    if duplicate_ids:
        blocking_issues.append(f"Coverage plan contains duplicate scenario IDs: {', '.join(duplicate_ids)}.")

    if merge_warnings:
        suggestions.extend(merge_warnings)

    score = max(0, 100 - (len(blocking_issues) * 20) - min(len(suggestions), 10))
    approved = score >= threshold and not blocking_issues
    summary = (
        "Use-case planning passed deterministic coverage review."
        if approved
        else "Use-case planning requires attention before downstream test-case generation."
    )
    return {
        "approved": approved,
        "score": score,
        "threshold": threshold,
        "summary": summary,
        "blocking_issues": _dedupe_preserve(blocking_issues),
        "suggestions": _dedupe_preserve(suggestions),
        "unmet_criteria": _dedupe_preserve(blocking_issues),
    }


def _run_use_case_workflow_sync_inner(
    *,
    requirements: List[Requirement],
    context: Optional[Any],
    model: str,
    human_feedback: Optional[str],
    workflow_settings: Optional[WorkflowSettings],
    actor_user_id: Optional[str],
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_settings = _resolve_test_case_workflow_settings(workflow_settings)
    threshold = int(resolved_settings["approval_threshold"] or DEFAULT_TEST_CASE_THRESHOLD)
    shards = _plan_use_case_shards(requirements)
    worker_count = 1 if len(shards) == 1 else min(DEFAULT_USE_CASE_WORKER_COUNT, len(shards))
    diagnostics = _new_use_case_diagnostics(shard_count=len(shards), worker_count=worker_count)

    _log_test_case_workflow(
        "use_case_workflow_started",
        requirement_count=len(requirements),
        shard_count=len(shards),
        worker_count=worker_count,
    )

    if len(shards) == 1:
        shard_results = [
            _run_shard_with_fallback(
                shard=shards[0],
                context=context,
                model=model,
                human_feedback=human_feedback,
                workflow_settings=WorkflowSettings(**resolved_settings),
                actor_user_id=actor_user_id,
            )
        ]
    else:
        result_by_index: Dict[int, _UseCaseShardResult] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_by_shard = {
                executor.submit(
                    _run_shard_with_fallback,
                    shard=shard,
                    context=context,
                    model=model,
                    human_feedback=human_feedback,
                    workflow_settings=WorkflowSettings(**resolved_settings),
                    actor_user_id=actor_user_id,
                ): shard
                for shard in shards
            }
            for future in as_completed(future_by_shard):
                shard = future_by_shard[future]
                try:
                    result_by_index[shard.index] = future.result()
                except Exception as exc:
                    result_by_index[shard.index] = _fallback_shard_result(
                        shard,
                        reason="shard_execution_error",
                        warning=f"Use-case shard {shard.shard_id} failed and was replaced with deterministic fallback artifacts: {exc}",
                        failed=True,
                    )
        shard_results = [result_by_index[shard.index] for shard in shards]

    _merge_shard_diagnostics(diagnostics, shard_results)
    requirement_analysis, coverage_plan, merge_warnings = _merge_use_case_shards(shard_results, requirements)
    diagnostics["merge_warnings"] = merge_warnings
    for warning in merge_warnings:
        _append_unique_message(diagnostics["warnings"], warning)

    review = _heuristic_use_case_review(
        requirement_analysis,
        coverage_plan,
        requirements,
        threshold,
        merge_warnings=merge_warnings,
    )
    coverage_metrics = _compute_use_case_metrics(requirement_analysis, coverage_plan, requirements, merge_warnings)

    _log_test_case_workflow(
        "use_case_workflow_completed",
        approved=review["approved"],
        score=review["score"],
        threshold=threshold,
        shard_count=diagnostics["shard_count"],
        fallback_shard_count=diagnostics["fallback_shard_count"],
        failed_shard_count=diagnostics["failed_shard_count"],
        merge_warning_count=len(merge_warnings),
    )

    return {
        "requirement_analysis": requirement_analysis,
        "coverage_plan": coverage_plan,
        "approved": review["approved"],
        "review": review,
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": public_workflow_diagnostics(diagnostics),
    }


def _run_use_case_workflow_sync(**kwargs: Any) -> Dict[str, Any]:
    context_token = bind_log_context(**_use_case_workflow_context(kwargs))
    try:
        return _run_use_case_workflow_sync_inner(**kwargs)
    finally:
        reset_log_context(context_token)


def _build_fallback_use_case_response(payload: GenerateTestCasesInput, *, reason: str, warning: str) -> Dict[str, Any]:
    resolved_settings = _resolve_test_case_workflow_settings(payload.workflow_settings)
    threshold = int(resolved_settings["approval_threshold"] or DEFAULT_TEST_CASE_THRESHOLD)
    diagnostics = _new_use_case_diagnostics(shard_count=1, worker_count=1)
    diagnostics["status"] = "fallback"
    diagnostics["used_fallback"] = True
    diagnostics["failure_reason"] = reason
    diagnostics["fallback_shard_count"] = 1
    _append_unique_message(diagnostics["warnings"], warning)
    requirement_analysis = fallback_requirement_analysis(payload.requirements)
    coverage_plan, merge_warnings = _canonicalize_coverage_scenario_ids(_fallback_coverage_plan(payload.requirements))
    diagnostics["merge_warnings"] = merge_warnings
    for merge_warning in merge_warnings:
        _append_unique_message(diagnostics["warnings"], merge_warning)
    review = _heuristic_use_case_review(
        requirement_analysis,
        coverage_plan,
        payload.requirements,
        threshold,
        merge_warnings=merge_warnings,
    )
    return {
        "requirement_analysis": requirement_analysis,
        "coverage_plan": coverage_plan,
        "approved": review["approved"],
        "review": review,
        "coverage_metrics": _compute_use_case_metrics(requirement_analysis, coverage_plan, payload.requirements, merge_warnings),
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": public_workflow_diagnostics(diagnostics),
    }


def _build_use_case_response(workflow: Dict[str, Any], requirements: List[Requirement]) -> Dict[str, Any]:
    raw_requirement_analysis = normalize_requirement_analysis(
        list(workflow.get("requirement_analysis") or fallback_requirement_analysis(requirements)),
        requirements,
    )
    raw_coverage_plan = _normalize_coverage_plan(list(workflow.get("coverage_plan") or _fallback_coverage_plan(requirements)), requirements)
    raw_coverage_plan, merge_warnings = _canonicalize_coverage_scenario_ids(raw_coverage_plan)
    diagnostics = public_workflow_diagnostics(dict(workflow.get("workflow_diagnostics") or {}))
    existing_merge_warnings = list(diagnostics.get("merge_warnings") or [])
    diagnostics["merge_warnings"] = _dedupe_preserve(existing_merge_warnings + merge_warnings)
    for warning in merge_warnings:
        _append_unique_message(diagnostics.setdefault("warnings", []), warning)
    threshold = int((workflow.get("workflow_settings") or {}).get("approval_threshold") or DEFAULT_TEST_CASE_THRESHOLD)
    review = dict(
        workflow.get("review")
        or _heuristic_use_case_review(
            raw_requirement_analysis,
            raw_coverage_plan,
            requirements,
            threshold,
            merge_warnings=diagnostics["merge_warnings"],
        )
    )
    return {
        "requirement_analysis": _hydrate_requirement_analysis(raw_requirement_analysis, requirements),
        "coverage_plan": _hydrate_coverage_plan(raw_coverage_plan, requirements),
        "approved": bool(workflow.get("approved", review.get("approved", False))),
        "review": review,
        "coverage_metrics": dict(
            workflow.get("coverage_metrics")
            or _compute_use_case_metrics(raw_requirement_analysis, raw_coverage_plan, requirements, diagnostics["merge_warnings"])
        ),
        "workflow_settings": dict(workflow.get("workflow_settings") or {}),
        "workflow_diagnostics": diagnostics,
    }


def generate_use_cases(
    payload: GenerateTestCasesInput,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    settings = _get_model_settings_or_none()

    if settings is None:
        workflow = _build_fallback_use_case_response(
            payload,
            reason="missing_model_credentials",
            warning="Model credentials are unavailable; deterministic use-case fallback was used.",
        )
    else:
        workflow = _run_use_case_workflow_sync(
            requirements=payload.requirements,
            context=payload.context,
            model=settings.model_name,
            human_feedback=payload.feedback if payload.feedback else None,
            workflow_settings=payload.workflow_settings,
            actor_user_id=actor_user_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation=operation,
        )

    return _build_use_case_response(workflow, payload.requirements)
