"""
Test Case Generation Agent - Multi-agent loop using Google ADK.

Implements thresholded validation results, iteration history, and a dedicated
refine-existing-test-cases path so the UI can gate export on explicit approval.
"""

import asyncio
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .adk_runtime import json_generation_config, tool_generation_config
from .analysis_agent import build_requirement_analysis_agent, fallback_requirement_analysis, normalize_requirement_analysis
from .prompting import REAL_WORLD_QA_POLICY, TEST_DESIGN_PROMPT_GUARDRAILS, human_feedback_section
from ..config import GenerationSettings, get_generation_settings, get_settings
from ..models import (
    GenerateTestCasesInput,
    RefineTestCasesInput,
    Requirement,
    ReviewResult,
    TestCase,
    WorkflowSettings,
)
from ..observability.logging import bind_log_context, get_log_context, reset_log_context
from ..observability.metrics import record_agent_fallback
from ..utils.llm_json import (
    parse_coverage_plan_json_detailed,
    parse_requirement_analysis_json_detailed,
    parse_review_json_detailed,
    parse_test_cases_json_detailed,
)
from ..utils.workflow_diagnostics import (
    has_retryable_parser_failure,
    mark_retryable_parser_failure,
    public_workflow_diagnostics,
    retry_reason,
)
from .test_case_coverage import (
    _compute_grounded_context_metrics,
    _compute_planned_scenario_metrics,
    _compute_requirement_analysis_metrics,
    _compute_test_case_coverage_metrics,
    _dedupe_preserve,
    _extract_linked_requirement_ids_from_test_case,
    _extract_scenario_refs_from_test_case,
    _fallback_coverage_plan,
    _normalize_coverage_plan,
    _scenario_tag,
)
from .test_case_fallback import _augment_with_fallback_coverage, _fallback_raw_test_cases
from .test_case_hydration import (
    _hydrate_coverage_plan,
    _hydrate_requirement_analysis,
    _hydrate_test_cases,
    _serialize_test_cases,
)
from .test_case_review import (
    DEFAULT_TEST_CASE_MAX_ITERATIONS,
    DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT,
    DEFAULT_TEST_CASE_THRESHOLD,
    _heuristic_test_case_review,
    _make_history_entry,
    _merge_review_results,
    _normalize_review_result,
    _prefer_review,
    _resolve_test_case_workflow_settings,
    _review_is_stalled,
)

STATE_TEST_CASES = "current_test_cases"
STATE_VALIDATION_FEEDBACK = "validation_feedback"
STATE_COVERAGE_PLAN = "coverage_plan"
STATE_REQUIREMENT_ANALYSIS = "requirement_analysis"
EVIDENCE_PAYLOAD_STRATEGY = "Raw prompts and raw model outputs are not stored; evidence keeps counts, pass status, shard status, and bounded diagnostics."
GENERATION_SOURCE_MODEL = "model"
GENERATION_SOURCE_MODEL_RECOVERED = "model_recovered"
GENERATION_SOURCE_PARALLEL_RETRY = "parallel_retry"
GENERATION_SOURCE_DETERMINISTIC_FULL_FALLBACK = "deterministic_full_fallback"
GENERATION_SOURCE_DETERMINISTIC_COVERAGE_COMPLETION = "deterministic_coverage_completion"


def _get_model_settings_or_none() -> Any | None:
    try:
        return get_settings()
    except RuntimeError as exc:
        if "GEMINI_API_KEY" not in str(exc):
            raise
        return None


def _new_workflow_diagnostics(*, attempt_count: int = 1) -> Dict[str, Any]:
    return {
        "status": "completed",
        "used_fallback": False,
        "failure_reason": None,
        "generation_route": None,
        "timed_out": False,
        "stalled": False,
        "max_iterations_reached": False,
        "parser_failures": [],
        "parser_recoveries": [],
        "recovery_reason": None,
        "warnings": [],
        "best_iteration": None,
        "attempt_count": attempt_count,
        "generation_source_counts": {},
        "scenario_ref_coverage_degraded": False,
        "scenario_ref_missing_case_count": 0,
        "scenario_ref_heuristic_fallback_case_count": 0,
        "missing_requirements_count": 0,
        "missing_must_have_scenario_count": 0,
        "missing_optional_scenario_count": 0,
        "deterministic_total_additions": 0,
        "deterministic_must_have_additions": 0,
        "deterministic_optional_additions": 0,
        "completion_source": None,
    }


def _log_test_case_workflow(event_type: str, **fields: Any) -> None:
    payload = {**get_log_context(), "event": event_type, **fields}
    logging.info("[TestCase Workflow] %s", json.dumps(payload, sort_keys=True, default=str))


def _test_case_workflow_context(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    operation = kwargs.get("operation")
    if not operation:
        operation = "testcases.refine" if kwargs.get("existing_test_cases") is not None else "testcases.generate"
    return {
        "request_id": kwargs.get("request_id"),
        "workflow_run_id": kwargs.get("workflow_run_id"),
        "actor_user_id": kwargs.get("actor_user_id"),
        "operation": operation,
    }


def _append_unique_message(container: List[str], message: str) -> None:
    value = str(message).strip()
    if value and value not in container:
        container.append(value)


def _diagnostic_sample(raw_text: Optional[str], *, limit: int = 280) -> str:
    normalized = " ".join(str(raw_text or "").split())
    if not normalized:
        return ""
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}…"


def _record_parser_failure(
    diagnostics: Dict[str, Any],
    author: str,
    error: Optional[str],
    raw_text: Optional[str] = None,
    *,
    retryable: bool = False,
) -> None:
    if not error:
        return
    diagnostics.setdefault("parser_failures", [])
    message = f"{author}: {error}"
    sample = _diagnostic_sample(raw_text)
    if sample:
        message = f"{message} | sample: {sample}"
    _append_unique_message(diagnostics["parser_failures"], message)
    diagnostics["status"] = "partial"
    diagnostics["failure_reason"] = diagnostics["failure_reason"] or "parser_failure"
    if retryable:
        mark_retryable_parser_failure(diagnostics, message)
    _log_test_case_workflow(
        "parser_failure",
        author=author,
        error=error,
        retryable=retryable,
        sample=sample or None,
        parser_failure_count=len(diagnostics["parser_failures"]),
        status=diagnostics["status"],
    )


def _record_parser_recovery(
    diagnostics: Dict[str, Any],
    author: str,
    error: Optional[str],
    raw_text: Optional[str] = None,
    *,
    artifact_label: str = "JSON",
) -> None:
    if not error:
        return
    diagnostics.setdefault("parser_recoveries", [])
    diagnostics.setdefault("warnings", [])
    message = f"{author}: {error}"
    sample = _diagnostic_sample(raw_text)
    if sample:
        message = f"{message} | sample: {sample}"
    _append_unique_message(diagnostics["parser_recoveries"], message)
    if diagnostics["status"] == "completed":
        diagnostics["status"] = "partial"
    _log_test_case_workflow(
        "parser_recovery",
        author=author,
        artifact_label=artifact_label,
        error=error,
        sample=sample or None,
        parser_recovery_count=len(diagnostics["parser_recoveries"]),
        status=diagnostics["status"],
    )


def _count_label(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _join_human_counts(parts: List[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _coverage_augmentation_warning(
    *,
    original_test_cases: List[Dict[str, Any]],
    augmented_test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
    diagnostics: Dict[str, Any],
    deterministic_counts: Optional[Dict[str, Any]] = None,
) -> str:
    deterministic_counts = deterministic_counts or _coverage_gap_counts(
        original_test_cases=original_test_cases,
        augmented_test_cases=augmented_test_cases,
        requirements=requirements,
        coverage_plan=coverage_plan,
    )
    added_total = int(deterministic_counts.get("deterministic_total_additions") or deterministic_counts.get("deterministic_additions_total") or 0)
    must_have_missing = int(deterministic_counts.get("missing_must_have_scenario_count") or 0)
    optional_missing = int(deterministic_counts.get("missing_optional_scenario_count") or 0)
    requirements_missing = int(deterministic_counts.get("missing_requirements_count") or 0)
    must_have_additions = int(deterministic_counts.get("deterministic_must_have_additions") or 0)
    optional_additions = int(deterministic_counts.get("deterministic_optional_additions") or 0)

    missing_parts: List[str] = []
    if requirements_missing:
        missing_parts.append(_count_label(requirements_missing, "requirement"))
    if must_have_missing:
        missing_parts.append(_count_label(must_have_missing, "must-have scenario"))
    if optional_missing:
        missing_parts.append(_count_label(optional_missing, "optional/planned scenario"))

    addition_parts: List[str] = []
    if must_have_additions:
        addition_parts.append(f"{_count_label(must_have_additions, 'must-have deterministic case')}")
    if optional_additions:
        addition_parts.append(f"{_count_label(optional_additions, 'optional deterministic case')}")
    if not addition_parts:
        addition_parts.append(_count_label(added_total, "deterministic coverage case"))

    if must_have_missing == 0 and optional_missing > 0 and requirements_missing == 0:
        missing_label = f"all must-have scenarios were covered, but {_count_label(optional_missing, 'optional/planned scenario')} remained"
    else:
        missing_label = f"{_join_human_counts(missing_parts)} remained uncovered" if missing_parts else "planned coverage gaps remained"
    source_label = "Recovered partial model output" if diagnostics.get("parser_recoveries") else "Model output"
    return (
        f"{source_label} needed deterministic coverage completion because {missing_label}; "
        f"added {_join_human_counts(addition_parts)} ({_count_label(added_total, 'total deterministic coverage case')})."
    )


def _model_dump_payload(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _model_dump_payload(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_model_dump_payload(item) for item in value if item is not None]
    return value


def _generation_settings_payload(
    *,
    workflow_settings: Optional[Dict[str, Any]] = None,
    generation_settings: Optional[GenerationSettings] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if workflow_settings:
        payload["workflow_settings"] = _model_dump_payload(workflow_settings)
    if generation_settings is not None:
        payload["generation_settings"] = _model_dump_payload(generation_settings)
    return payload


def _prompt_metadata(
    *,
    context: Optional[Any],
    template: Any,
    human_feedback: Optional[str],
    raw_prompt_stored: bool = False,
) -> Dict[str, Any]:
    return {
        "context_provided": context is not None,
        "feedback_provided": bool(human_feedback),
        "template_name": getattr(template, "name", None),
        "template_format": getattr(template, "format", None),
        "template_field_count": len(getattr(template, "fields", []) or []),
        "raw_prompt_stored": raw_prompt_stored,
    }


def _parser_status(diagnostics: Optional[Dict[str, Any]]) -> str:
    diagnostics = diagnostics or {}
    if diagnostics.get("parser_failures"):
        return "failed"
    if diagnostics.get("parser_recoveries"):
        return "recovered"
    return "clean"


def _review_status(review: Optional[Dict[str, Any]], *, fallback: bool = False) -> str:
    if fallback:
        return "fallback"
    if not review:
        return "not_run"
    return "approved" if bool(review.get("approved")) else "rejected"


def _raw_output_summary(*, model_case_count: int, fallback_case_count: int = 0) -> Dict[str, Any]:
    return {
        "raw_content_stored": False,
        "model_case_count": max(0, int(model_case_count or 0)),
        "fallback_case_count": max(0, int(fallback_case_count or 0)),
    }


def _model_generation_source(diagnostics: Optional[Dict[str, Any]]) -> str:
    diagnostics = diagnostics or {}
    return GENERATION_SOURCE_MODEL_RECOVERED if diagnostics.get("parser_recoveries") else GENERATION_SOURCE_MODEL


def _with_test_case_provenance(
    test_cases: List[Dict[str, Any]],
    *,
    generation_source: str,
    generation_pass_id: Optional[str],
    source_shard_id: Optional[str] = None,
    coverage_completion_reason: Optional[str] = None,
    preserve_existing: bool = False,
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for raw_test_case in test_cases or []:
        test_case = dict(raw_test_case)
        original_id = str(test_case.get("id") or "").strip()
        if original_id and (not preserve_existing or not test_case.get("source_case_id")):
            test_case["source_case_id"] = original_id
        if not preserve_existing or not test_case.get("generation_source"):
            test_case["generation_source"] = generation_source
        if generation_pass_id and (not preserve_existing or not test_case.get("generation_pass_id")):
            test_case["generation_pass_id"] = generation_pass_id
        if source_shard_id and (not preserve_existing or not test_case.get("source_shard_id")):
            test_case["source_shard_id"] = source_shard_id
        if coverage_completion_reason and (not preserve_existing or not test_case.get("coverage_completion_reason")):
            test_case["coverage_completion_reason"] = coverage_completion_reason
        enriched.append(test_case)
    return enriched


def _generation_source_counts(test_cases: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for test_case in test_cases or []:
        source = str(test_case.get("generation_source") or "").strip()
        if source:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _update_generation_source_counts(diagnostics: Dict[str, Any], test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    diagnostics["generation_source_counts"] = _generation_source_counts(test_cases)
    return diagnostics


def _update_scenario_ref_diagnostics(diagnostics: Dict[str, Any], coverage_metrics: Dict[str, Any]) -> Dict[str, Any]:
    degraded = bool(coverage_metrics.get("scenario_ref_coverage_degraded"))
    missing_count = int(coverage_metrics.get("scenario_ref_missing_case_count") or 0)
    fallback_count = int(coverage_metrics.get("scenario_ref_heuristic_fallback_case_count") or 0)
    diagnostics["scenario_ref_coverage_degraded"] = degraded
    diagnostics["scenario_ref_missing_case_count"] = missing_count
    diagnostics["scenario_ref_heuristic_fallback_case_count"] = fallback_count
    if degraded:
        diagnostics.setdefault("warnings", [])
        _append_unique_message(
            diagnostics["warnings"],
            (f"Coverage review used heuristic scenario-type inference because {fallback_count} test case(s) were missing exact planned scenario_refs."),
        )
    return diagnostics


def _update_completion_diagnostics(diagnostics: Dict[str, Any], deterministic_counts: Dict[str, Any]) -> Dict[str, Any]:
    total_additions = int(deterministic_counts.get("deterministic_total_additions") or deterministic_counts.get("deterministic_additions_total") or 0)
    diagnostics["missing_requirements_count"] = int(deterministic_counts.get("missing_requirements_count") or 0)
    diagnostics["missing_must_have_scenario_count"] = int(deterministic_counts.get("missing_must_have_scenario_count") or 0)
    diagnostics["missing_optional_scenario_count"] = int(deterministic_counts.get("missing_optional_scenario_count") or 0)
    diagnostics["deterministic_total_additions"] = total_additions
    diagnostics["deterministic_must_have_additions"] = int(deterministic_counts.get("deterministic_must_have_additions") or 0)
    diagnostics["deterministic_optional_additions"] = int(deterministic_counts.get("deterministic_optional_additions") or 0)
    diagnostics["completion_source"] = deterministic_counts.get("completion_source") or diagnostics.get("completion_source")
    return diagnostics


def _last_generation_pass_id(evidence: Optional[Dict[str, Any]], *, exclude_pass_types: Optional[set[str]] = None) -> Optional[str]:
    exclude_pass_types = exclude_pass_types or set()
    for pass_evidence in reversed(list((evidence or {}).get("passes") or [])):
        if pass_evidence.get("pass_type") in exclude_pass_types:
            continue
        pass_id = str(pass_evidence.get("pass_id") or "").strip()
        if pass_id:
            return pass_id
    return None


def _coverage_gap_counts(
    *,
    original_test_cases: List[Dict[str, Any]],
    augmented_test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
) -> Dict[str, Any]:
    added_total = max(0, len(augmented_test_cases or []) - len(original_test_cases or []))
    coverage_metrics = _compute_test_case_coverage_metrics(original_test_cases, requirements)
    scenario_metrics = _compute_planned_scenario_metrics(coverage_plan, original_test_cases, requirements)
    missing_requirements = len(coverage_metrics.get("requirements_without_tests") or [])
    missing_must_have = len(scenario_metrics.get("missing_must_have_scenarios") or [])
    missing_planned = len(scenario_metrics.get("missing_scenarios") or [])
    missing_optional = max(0, missing_planned - missing_must_have)
    must_have_additions = min(added_total, missing_must_have)
    optional_additions = min(max(0, added_total - must_have_additions), missing_optional)
    return {
        "deterministic_additions_total": added_total,
        "deterministic_total_additions": added_total,
        "deterministic_must_have_additions": must_have_additions,
        "deterministic_optional_additions": optional_additions,
        "missing_requirements_count": missing_requirements,
        "missing_must_have_scenario_count": missing_must_have,
        "missing_optional_scenario_count": missing_optional,
        "completion_source": "coverage_completion",
    }


def _deterministic_full_fallback_counts(
    *,
    fallback_test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
) -> Dict[str, Any]:
    added_total = len(fallback_test_cases or [])
    coverage_metrics = _compute_test_case_coverage_metrics([], requirements)
    scenario_metrics = _compute_planned_scenario_metrics(coverage_plan, [], requirements)
    missing_requirements = len(coverage_metrics.get("requirements_without_tests") or [])
    missing_must_have = len(scenario_metrics.get("missing_must_have_scenarios") or [])
    missing_planned = len(scenario_metrics.get("missing_scenarios") or [])
    missing_optional = max(0, missing_planned - missing_must_have)
    must_have_additions = min(added_total, missing_must_have)
    optional_additions = min(max(0, added_total - must_have_additions), missing_optional)
    return {
        "deterministic_additions_total": added_total,
        "deterministic_total_additions": added_total,
        "deterministic_must_have_additions": must_have_additions,
        "deterministic_optional_additions": optional_additions,
        "missing_requirements_count": missing_requirements,
        "missing_must_have_scenario_count": missing_must_have,
        "missing_optional_scenario_count": missing_optional,
        "completion_source": "full_fallback",
    }


def _new_generation_evidence(
    *,
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
    model_name: Optional[str],
    operation: Optional[str],
    request_id: Optional[str],
    workflow_run_id: Optional[str],
    workflow_settings: Optional[Dict[str, Any]] = None,
    generation_settings: Optional[GenerationSettings] = None,
) -> Dict[str, Any]:
    return {
        "evidence_id": str(uuid.uuid4()),
        "request_id": request_id,
        "workflow_run_id": workflow_run_id,
        "operation": operation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_name": model_name,
        "generation_settings": _generation_settings_payload(
            workflow_settings=workflow_settings,
            generation_settings=generation_settings,
        ),
        "requirement_count": len(requirements or []),
        "coverage_plan_count": len(coverage_plan or []),
        "planned_scenario_count": _scenario_count(coverage_plan),
        "model_case_count_before_review": 0,
        "model_case_count_after_merge": 0,
        "final_test_case_count": 0,
        "deterministic_additions_total": 0,
        "deterministic_total_additions": 0,
        "deterministic_must_have_additions": 0,
        "deterministic_optional_additions": 0,
        "missing_requirements_count": 0,
        "missing_must_have_scenario_count": 0,
        "missing_optional_scenario_count": 0,
        "completion_source": None,
        "parser_failure_count": 0,
        "parser_recovery_count": 0,
        "final_status": None,
        "recovery_reason": None,
        "warning_count": 0,
        "payload_strategy": EVIDENCE_PAYLOAD_STRATEGY,
        "passes": [],
    }


def _generation_pass_evidence(
    *,
    pass_type: str,
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
    model_name: Optional[str],
    diagnostics: Optional[Dict[str, Any]],
    review: Optional[Dict[str, Any]] = None,
    model_case_count: int = 0,
    merged_case_count: Optional[int] = None,
    fallback_case_count: int = 0,
    deterministic_counts: Optional[Dict[str, int]] = None,
    prompt_metadata: Optional[Dict[str, Any]] = None,
    shards: Optional[List[Dict[str, Any]]] = None,
    pass_id: Optional[str] = None,
) -> Dict[str, Any]:
    diagnostics = diagnostics or {}
    deterministic_counts = deterministic_counts or {}
    full_fallback = pass_type == "deterministic_full_fallback"
    used_fallback = bool(diagnostics.get("used_fallback")) or full_fallback
    return {
        "pass_id": pass_id or str(uuid.uuid4()),
        "pass_type": pass_type,
        "model_name": model_name,
        "requirement_count": len(requirements or []),
        "coverage_plan_count": len(coverage_plan or []),
        "planned_scenario_count": _scenario_count(coverage_plan),
        "prompt_metadata": prompt_metadata or {},
        "raw_output_summary": _raw_output_summary(
            model_case_count=0 if full_fallback else model_case_count,
            fallback_case_count=fallback_case_count,
        ),
        "model_case_count_before_review": 0 if full_fallback else max(0, int(model_case_count or 0)),
        "model_case_count_after_review": 0 if full_fallback else max(0, int(model_case_count or 0)),
        "merged_case_count": max(0, int(merged_case_count if merged_case_count is not None else model_case_count or 0)),
        "deterministic_additions_total": int(deterministic_counts.get("deterministic_additions_total") or 0),
        "deterministic_total_additions": int(
            deterministic_counts.get("deterministic_total_additions") or deterministic_counts.get("deterministic_additions_total") or 0
        ),
        "deterministic_must_have_additions": int(deterministic_counts.get("deterministic_must_have_additions") or 0),
        "deterministic_optional_additions": int(deterministic_counts.get("deterministic_optional_additions") or 0),
        "missing_requirements_count": int(deterministic_counts.get("missing_requirements_count") or 0),
        "missing_must_have_scenario_count": int(deterministic_counts.get("missing_must_have_scenario_count") or 0),
        "missing_optional_scenario_count": int(deterministic_counts.get("missing_optional_scenario_count") or 0),
        "completion_source": deterministic_counts.get("completion_source"),
        "parser_failure_count": len(diagnostics.get("parser_failures") or []),
        "parser_recovery_count": len(diagnostics.get("parser_recoveries") or []),
        "review_status": _review_status(review, fallback=full_fallback),
        "review_score": review.get("score") if review else None,
        "review_threshold": review.get("threshold") if review else None,
        "approved": bool(review.get("approved")) if review else False,
        "used_fallback": used_fallback,
        "failure_reason": diagnostics.get("failure_reason"),
        "shards": shards or [],
    }


def _shard_evidence(
    *,
    shard: Any,
    test_cases: List[Dict[str, Any]],
    diagnostics: Optional[Dict[str, Any]],
    used_fallback: bool,
    failed: bool,
) -> Dict[str, Any]:
    diagnostics = diagnostics or {}
    return {
        "shard_id": getattr(shard, "shard_id", f"test-case-shard-{getattr(shard, 'index', 0):02d}"),
        "requirement_count": len(getattr(shard, "requirements", []) or []),
        "planned_scenario_count": _scenario_count(getattr(shard, "coverage_plan", []) or []),
        "raw_output_count": 0 if used_fallback else len(test_cases or []),
        "fallback_case_count": len(test_cases or []) if used_fallback else 0,
        "parser_status": "not_run"
        if used_fallback and not diagnostics.get("parser_failures") and not diagnostics.get("parser_recoveries")
        else _parser_status(diagnostics),
        "review_status": "fallback" if used_fallback else "not_run",
        "used_fallback": bool(used_fallback),
        "failed": bool(failed),
        "failure_reason": diagnostics.get("failure_reason"),
        "parser_failure_count": len(diagnostics.get("parser_failures") or []),
        "parser_recovery_count": len(diagnostics.get("parser_recoveries") or []),
        "warning_count": len(diagnostics.get("warnings") or []),
    }


def _append_generation_pass(evidence: Dict[str, Any], pass_evidence: Dict[str, Any]) -> Dict[str, Any]:
    evidence = dict(evidence or {})
    evidence["passes"] = list(evidence.get("passes") or []) + [pass_evidence]
    return evidence


def _retag_generation_passes(evidence: Dict[str, Any], *, pass_type: str) -> Dict[str, Any]:
    evidence = dict(evidence or {})
    evidence["passes"] = [{**dict(item), "pass_type": pass_type} for item in evidence.get("passes") or []]
    return evidence


def _merge_generation_evidence(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    evidence = dict(primary or secondary or {})
    evidence["passes"] = list((primary or {}).get("passes") or []) + list((secondary or {}).get("passes") or [])
    return evidence


def _finalize_generation_evidence(
    *,
    evidence: Optional[Dict[str, Any]],
    workflow: Dict[str, Any],
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
    final_test_cases: List[Dict[str, Any]],
    model_name: Optional[str],
    operation: Optional[str],
    request_id: Optional[str],
    workflow_run_id: Optional[str],
    workflow_settings: Optional[Dict[str, Any]] = None,
    generation_settings: Optional[GenerationSettings] = None,
) -> Dict[str, Any]:
    diagnostics = dict(workflow.get("workflow_diagnostics") or {})
    evidence = evidence or _new_generation_evidence(
        requirements=requirements,
        coverage_plan=coverage_plan,
        model_name=model_name,
        operation=operation,
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        workflow_settings=workflow_settings,
        generation_settings=generation_settings,
    )
    evidence = dict(evidence)
    evidence["model_name"] = evidence.get("model_name") or model_name
    evidence["operation"] = evidence.get("operation") or operation
    evidence["request_id"] = evidence.get("request_id") or request_id
    evidence["workflow_run_id"] = evidence.get("workflow_run_id") or workflow_run_id
    evidence["generation_settings"] = {
        **dict(evidence.get("generation_settings") or {}),
        **_generation_settings_payload(
            workflow_settings=workflow_settings,
            generation_settings=generation_settings,
        ),
    }
    evidence["requirement_count"] = len(requirements or [])
    evidence["coverage_plan_count"] = len(coverage_plan or [])
    evidence["planned_scenario_count"] = _scenario_count(coverage_plan)
    evidence["final_test_case_count"] = len(final_test_cases or [])
    evidence["parser_failure_count"] = len(diagnostics.get("parser_failures") or [])
    evidence["parser_recovery_count"] = len(diagnostics.get("parser_recoveries") or [])
    evidence["final_status"] = diagnostics.get("status")
    evidence["recovery_reason"] = diagnostics.get("recovery_reason")
    evidence["warning_count"] = len(diagnostics.get("warnings") or [])
    passes = list(evidence.get("passes") or [])
    evidence["model_case_count_before_review"] = next(
        (int(item.get("model_case_count_before_review") or 0) for item in passes if not item.get("used_fallback")),
        0,
    )
    evidence["model_case_count_after_merge"] = max([int(item.get("merged_case_count") or 0) for item in passes if not item.get("used_fallback")] or [0])
    evidence["deterministic_additions_total"] = sum(int(item.get("deterministic_additions_total") or 0) for item in passes)
    evidence["deterministic_total_additions"] = sum(
        int(item.get("deterministic_total_additions") or item.get("deterministic_additions_total") or 0) for item in passes
    )
    evidence["deterministic_must_have_additions"] = sum(int(item.get("deterministic_must_have_additions") or 0) for item in passes)
    evidence["deterministic_optional_additions"] = sum(int(item.get("deterministic_optional_additions") or 0) for item in passes)
    evidence["missing_requirements_count"] = sum(int(item.get("missing_requirements_count") or 0) for item in passes)
    evidence["missing_must_have_scenario_count"] = sum(int(item.get("missing_must_have_scenario_count") or 0) for item in passes)
    evidence["missing_optional_scenario_count"] = sum(int(item.get("missing_optional_scenario_count") or 0) for item in passes)
    evidence["completion_source"] = next((item.get("completion_source") for item in passes if item.get("completion_source")), None)
    evidence["payload_strategy"] = evidence.get("payload_strategy") or EVIDENCE_PAYLOAD_STRATEGY
    return evidence


def _combined_event_text(event: Any) -> str:
    if not event.content or not event.content.parts:
        return ""
    text_parts = []
    for part in event.content.parts:
        text = getattr(part, "text", None)
        if text:
            text_parts.append(str(text))
    return "\n".join(text_parts).strip()


def _record_event_error(diagnostics: Dict[str, Any], author: str, event: Any) -> None:
    error_code = getattr(event, "error_code", None)
    error_message = getattr(event, "error_message", None)
    if not error_code and not error_message:
        return

    diagnostics["status"] = "partial"
    diagnostics["failure_reason"] = diagnostics["failure_reason"] or "model_error"
    warning = f"{author}: model event error"
    if error_code:
        warning = f"{warning} ({error_code})"
    if error_message:
        warning = f"{warning}: {_diagnostic_sample(str(error_message))}"
    _append_unique_message(diagnostics["warnings"], warning)
    _log_test_case_workflow(
        "event_error",
        author=author,
        error_code=error_code,
        error_message=error_message,
        status=diagnostics["status"],
    )


def exit_loop(tool_context: ToolContext) -> dict:
    logging.info("[exit_loop] Test cases approved - exiting validation loop")
    tool_context.actions.escalate = True
    tool_context.actions.skip_summarization = True
    return {"status": "approved", "message": "Test cases approved"}


def _build_coverage_planner_agent(
    model: str,
    requirements_text: str,
    context_text: str,
    human_feedback: Optional[str] = None,
) -> Agent:
    feedback_section = human_feedback_section("Human Feedback to Consider", human_feedback)

    return Agent(
        name="CoveragePlannerAgent",
        model=model,
        include_contents="none",
        generate_content_config=json_generation_config(max_output_tokens=24000),
        instruction=f"""You are a Senior QA Strategist creating a scenario coverage plan before detailed test cases are written.

    {TEST_DESIGN_PROMPT_GUARDRAILS}
    {REAL_WORLD_QA_POLICY}

**Requirements:**
{requirements_text}

**Context:**
{context_text}

**Requirement Analysis:**
```
{{{STATE_REQUIREMENT_ANALYSIS}}}
```
{feedback_section}
**Rules:**
1. Produce 2-4 scenarios per requirement.
2. Always include a 'Happy Path' scenario for every requirement.
3. Include at least one non-happy-path scenario per requirement.
4. Use the requirement analysis to cover business rules, constraints, permissions, risks, and transitions when present.
5. Add Authorization scenarios when role permissions are present, Boundary/Validation scenarios when field constraints are present, State Transition scenarios when workflow states are present, and Integration/Error Handling scenarios when external systems are present.
6. Use ONLY these scenario types: Happy Path, Negative, Boundary, Validation, Authorization, State Transition, Integration, Error Handling, Data Variation.
7. Mark essential scenarios with must_have=true, especially scenarios that cover Critical/High risks, authorization, data integrity, or required validations.
8. Make every scenario objective specific enough that a tester can derive expected data, action, and assertion.
9. Output ONLY a JSON object shaped like:
{{
    "coverage_plan": [
        {{
            "requirement_id": "REQ-001",
            "requirement_text": "The system shall ...",
            "scenarios": [
                {{
                    "id": "REQ-001-SCN-01",
                    "scenario_type": "Happy Path",
                    "title": "Primary flow succeeds",
                    "objective": "Explain what must be validated.",
                    "priority": "High",
                    "must_have": true
                }}
            ]
        }}
    ]
}}
""",
        description="Plans scenario coverage for each requirement before test-case generation",
        output_key=STATE_COVERAGE_PLAN,
    )


def _build_review_loop(
    model: str,
    threshold: int,
    max_iterations: int,
    requirements_text: str,
    human_feedback: Optional[str] = None,
) -> LoopAgent:
    feedback_section = human_feedback_section("Human Feedback That Must Be Honored", human_feedback)

    validator_agent = Agent(
        name="TestCaseValidatorAgent",
        model=model,
        include_contents="none",
        generate_content_config=json_generation_config(max_output_tokens=4096),
        output_schema=ReviewResult,
        instruction=f"""You are a QA Lead reviewing test cases for quality, completeness, and traceability.

    {TEST_DESIGN_PROMPT_GUARDRAILS}
    {REAL_WORLD_QA_POLICY}

**Current Test Cases:**
```
{{{STATE_TEST_CASES}}}
```

    **Requirement Analysis:**
    ```
    {{{STATE_REQUIREMENT_ANALYSIS}}}
    ```

    **Coverage Plan:**
    ```
    {{{STATE_COVERAGE_PLAN}}}
    ```

**Requirements:**
{requirements_text}
{feedback_section}
**Quality Checklist:**
1. Each test case has a clear title and meaningful description.
2. Steps are executable and expected results are specific.
3. Steps are sequential, actor-aware, and contain an action plus an observable expected result.
4. Structured linked_requirement_ids cover every requirement; tags may mirror them for backward compatibility.
5. Every must-have scenario from the coverage plan is represented by exact `scenario_refs` on at least one test case.
6. Requirement analysis details (rules, constraints, permissions, transitions, and risks) are reflected when present.
7. Tags include a scenario marker formatted like scenario:happy-path or scenario:negative.
8. Priority, type, status, and automation status are valid.
9. Test data, preconditions, and overall expected_result are present when needed.
10. Browser/documentation cases use exact grounded headings, link names, and hrefs when grounded context provides them.
11. Cases are realistic enough for manual execution and future Playwright automation: no vague steps, TBD values, or unsupported feature invention.
12. Human feedback has been addressed without treating feedback as an instruction to weaken quality gates.

**Response Rules:**
- Return ONLY a JSON object with this exact shape:
{{
  "approved": true,
  "score": 94,
  "threshold": {threshold},
  "summary": "Brief explanation.",
  "blocking_issues": [],
  "suggestions": [],
  "unmet_criteria": []
}}
- Set approved=true ONLY when score >= {threshold} and blocking_issues is empty.
""",
        description="Validates generated test cases against the approval threshold",
        output_key=STATE_VALIDATION_FEEDBACK,
    )

    refiner_agent = Agent(
        name="TestCaseRefinerAgent",
        model=model,
        include_contents="none",
        generate_content_config=tool_generation_config(max_output_tokens=16000, temperature=0.15),
        instruction=f"""You are a QA Engineer refining test cases.

    {TEST_DESIGN_PROMPT_GUARDRAILS}

**Current Test Cases:**
```
{{{STATE_TEST_CASES}}}
```

    **Requirement Analysis:**
    ```
    {{{STATE_REQUIREMENT_ANALYSIS}}}
    ```

    **Coverage Plan:**
    ```
    {{{STATE_COVERAGE_PLAN}}}
    ```

**Structured Validation Result:**
{{{STATE_VALIDATION_FEEDBACK}}}
{feedback_section}
**Requirements:**
{requirements_text}

**Your Task:**
1. If the validation JSON indicates approved=true, score >= threshold, and blocking_issues is empty, call 'exit_loop' immediately.
2. Otherwise, improve the test cases to address all validation issues and human feedback.
3. Preserve traceability fields and scenario_refs unless the validation result proves they are wrong.
4. Replace vague or placeholder actions with concrete setup/action/assertion steps grounded in requirements/context.
5. Output ONLY a JSON object with the shape {{"test_cases": [...]}}.

Either call exit_loop OR output the refined JSON object. Never do both. Never add commentary.
""",
        description="Refines generated test cases until the approval threshold is reached",
        tools=[exit_loop],
        output_key=STATE_TEST_CASES,
    )

    return LoopAgent(
        name="ValidationLoop",
        sub_agents=[validator_agent, refiner_agent],
        max_iterations=max(1, max_iterations),
    )


def _build_test_case_generator_agent(
    model: str,
    requirements_text: str,
    context_text: str,
    template_text: str,
    human_feedback: Optional[str] = None,
) -> Agent:
    feedback_section = human_feedback_section("Human Feedback to Address", human_feedback)

    return Agent(
        name="TestCaseGeneratorAgent",
        model=model,
        include_contents="default",
        generate_content_config=json_generation_config(max_output_tokens=20000, temperature=0.15),
        instruction=f"""You are a Senior QA Engineer specializing in detailed, execution-ready test design.

    {TEST_DESIGN_PROMPT_GUARDRAILS}
    {REAL_WORLD_QA_POLICY}

**Requirements to Test:**
{requirements_text}

**Context:**
{context_text}

**Template Configuration:**
{template_text}

**Requirement Analysis To Honor:**
{{{STATE_REQUIREMENT_ANALYSIS}}}

**Coverage Plan To Implement:**
{{{STATE_COVERAGE_PLAN}}}
{feedback_section}
**Rules:**
1. Generate at least one executable test case for every must-have scenario in the coverage plan.
2. Prefer one test case per planned scenario; combine scenarios only when they share the same actor, setup, and expected outcome and `scenario_refs` lists every covered planned scenario ID.
3. Reflect business rules, field constraints, role permissions, state transitions, and risks from the requirement analysis whenever they apply.
4. Set `linked_requirement_ids` to a JSON array containing every requirement ID covered by the test case.
5. Also include those requirement IDs in `tags` for backward compatibility, plus one scenario tag using the format scenario:<kebab-case-scenario-type>.
6. Set `scenario_refs` to the exact coverage-plan scenario ID(s) implemented by the test case; do not omit it when the coverage plan contains scenario IDs.
7. When grounded context is provided, include `source_refs` with the relevant artifact IDs used by the test case.
8. Include detailed steps, expected results, realistic priorities, and execution metadata.
9. Keep each test case centered on one primary scenario from the coverage plan.
10. The `steps` field MUST be a JSON array of step objects shaped like {{"step": 1, "action": "...", "expected": "...", "test_data": null}}.
11. For browser or documentation workflows, assertions MUST prefer exact grounded headings, visible text, accessible link names, and hrefs from the context instead of inferred marketing phrases or synthetic labels.
12. Navigation steps MUST use real accessible link text from grounded context or direct href URLs; never invent labels such as "link/button for ...".
13. Never return `steps` as a single string, markdown list, or paragraph.
14. Make steps real-world executable: name the actor/role, setup data, UI/API action, validation point, and observable outcome.
15. Use concrete but non-sensitive test data. If exact data is unknown, put explicit assumptions in preconditions or test_data; never use TBD/placeholder text.
16. Include negative, boundary, authorization, and error-handling coverage when the coverage plan or requirement analysis calls for it; do not overproduce only happy paths.
17. Prefer business-readable test data such as `qa.manager@example.test`, `INV-1001`, or `2026-05-10`; do not use real personal data or secrets.
18. Output ONLY a JSON object shaped like {{"test_cases": [...]}}.
""",
        description="Generates initial test cases from approved requirements",
        output_key=STATE_TEST_CASES,
    )


def _build_generation_pipeline(
    model: str,
    requirements_text: str,
    context_text: str,
    template_text: str,
    threshold: int,
    max_iterations: int,
    human_feedback: Optional[str] = None,
) -> Agent:
    generator_agent = _build_test_case_generator_agent(
        model,
        requirements_text,
        context_text,
        template_text,
        human_feedback=human_feedback,
    )

    return SequentialAgent(
        name="TestCaseGenerationPipeline",
        sub_agents=[
            build_requirement_analysis_agent(
                model,
                requirements_text,
                context_text,
                output_key=STATE_REQUIREMENT_ANALYSIS,
                human_feedback=human_feedback,
            ),
            _build_coverage_planner_agent(model, requirements_text, context_text, human_feedback=human_feedback),
            generator_agent,
            _build_review_loop(model, threshold, max_iterations, requirements_text, human_feedback=human_feedback),
        ],
        description="Generates and iteratively validates test cases from requirements",
    )


def _build_refinement_pipeline(
    model: str,
    requirements_text: str,
    context_text: str,
    template_text: str,
    threshold: int,
    max_iterations: int,
    human_feedback: str,
) -> Agent:
    refinement_agent = Agent(
        name="TestCaseRefinementAgent",
        model=model,
        include_contents="default",
        generate_content_config=json_generation_config(max_output_tokens=20000, temperature=0.15),
        instruction=f"""You are a Senior QA Engineer refining an existing test suite.

    {TEST_DESIGN_PROMPT_GUARDRAILS}
    {REAL_WORLD_QA_POLICY}

Use the existing test cases, requirements, context, template, and human feedback in the user message to produce an improved JSON object shaped like {{"test_cases": [...]}}.

Requirements reference:
{requirements_text}

Context:
{context_text}

Template:
{template_text}

Requirement analysis:
{{{STATE_REQUIREMENT_ANALYSIS}}}

Coverage plan:
{{{STATE_COVERAGE_PLAN}}}

Rules:
1. Preserve good test cases and improve weak ones.
2. Add, merge, split, or remove cases as needed.
3. Keep structured `linked_requirement_ids` intact or improve them; also mirror linked requirement IDs in `tags` for backward compatibility.
4. Ensure each test case includes a scenario tag formatted like scenario:happy-path.
5. Preserve or improve exact `scenario_refs` from the coverage plan; combined cases must list every covered planned scenario ID.
6. Preserve or improve any grounded-context `source_refs` when grounded context is available.
7. The `steps` field MUST remain a JSON array of objects with `step`, `action`, `expected`, and optional `test_data`.
8. For browser or documentation workflows, replace inferred assertions or synthetic click labels with exact grounded headings, accessible link names, and hrefs from the context whenever available.
9. Never return `steps` as a plain string, markdown list, or free-form paragraph.
10. Remove generic actions like "navigate to the feature area" when a more concrete UI/API action can be inferred.
11. Add missing negative, boundary, authorization, state-transition, or integration cases when feedback or coverage gaps require them.
12. Output ONLY the JSON object.
""",
        description="Applies human feedback to an existing test-case set before re-validation",
        output_key=STATE_TEST_CASES,
    )

    return SequentialAgent(
        name="TestCaseRefinementPipeline",
        sub_agents=[
            build_requirement_analysis_agent(
                model,
                requirements_text,
                context_text,
                output_key=STATE_REQUIREMENT_ANALYSIS,
                human_feedback=human_feedback,
            ),
            _build_coverage_planner_agent(model, requirements_text, context_text, human_feedback=human_feedback),
            refinement_agent,
            _build_review_loop(model, threshold, max_iterations, requirements_text, human_feedback=human_feedback),
        ],
        description="Refines an existing test-case set and re-validates it against the approval threshold",
    )


async def _run_test_case_workflow_async(
    *,
    requirements: List[Requirement],
    context: Optional[Any],
    requirements_text: str,
    context_text: str,
    template_text: str,
    model: str,
    human_feedback: Optional[str] = None,
    existing_test_cases: Optional[List[Dict[str, Any]]] = None,
    workflow_settings: Optional[WorkflowSettings] = None,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_settings = _resolve_test_case_workflow_settings(workflow_settings)
    threshold = int(resolved_settings["approval_threshold"] or DEFAULT_TEST_CASE_THRESHOLD)
    max_iterations = int(resolved_settings["max_iterations"] or DEFAULT_TEST_CASE_MAX_ITERATIONS)
    timeout_seconds = resolved_settings["timeout_seconds"]
    stall_iteration_limit = int(resolved_settings["stall_iteration_limit"] or DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT)
    diagnostics = _new_workflow_diagnostics()
    is_refinement = existing_test_cases is not None

    if is_refinement:
        root_agent = _build_refinement_pipeline(
            model,
            requirements_text,
            context_text,
            template_text,
            threshold,
            max_iterations,
            human_feedback or "No human feedback provided.",
        )
        message_text = f"""Refine these existing test cases using the human feedback.

Existing test cases JSON:
{{"test_cases": {existing_test_cases or []}}}

Requirements:
{requirements_text}

Context:
{context_text}

Template:
{template_text}

Human feedback:
{human_feedback or "No human feedback provided."}
"""
    else:
        root_agent = _build_generation_pipeline(
            model,
            requirements_text,
            context_text,
            template_text,
            threshold,
            max_iterations,
            human_feedback=human_feedback,
        )
        message_text = "Generate and validate comprehensive test cases from the approved requirements."

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="test_case_generator",
        session_service=session_service,
    )

    user_id = str(actor_user_id or f"user_{uuid.uuid4().hex[:8]}")
    session = await session_service.create_session(
        app_name="test_case_generator",
        user_id=user_id,
        state={
            STATE_TEST_CASES: "[]",
            STATE_VALIDATION_FEEDBACK: "",
            STATE_COVERAGE_PLAN: "[]",
            STATE_REQUIREMENT_ANALYSIS: "[]",
        },
    )

    current_test_cases: List[Dict[str, Any]] = []
    current_coverage_plan: List[Dict[str, Any]] = []
    current_requirement_analysis: List[Dict[str, Any]] = []
    iteration_history: List[Dict[str, Any]] = []
    model_review: Optional[Dict[str, Any]] = None
    previous_review: Optional[Dict[str, Any]] = None
    repeated_review_count = 0
    best_candidate: Optional[Dict[str, Any]] = None

    _log_test_case_workflow(
        "session_started",
        session_id=session.id,
        user_id=user_id,
        is_refinement=is_refinement,
        settings=resolved_settings,
        requirement_count=len(requirements),
    )

    async def _consume_events() -> None:
        nonlocal current_test_cases, current_coverage_plan, current_requirement_analysis
        nonlocal model_review, previous_review, repeated_review_count, best_candidate

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message_text)]),
        ):
            author = getattr(event, "author", "unknown")
            _log_test_case_workflow("event_received", session_id=session.id, author=author)
            _record_event_error(diagnostics, author, event)

            if getattr(event, "partial", False):
                continue

            text = _combined_event_text(event)
            if not text:
                continue

            if author in {"TestCaseGeneratorAgent", "TestCaseRefinementAgent", "TestCaseRefinerAgent"}:
                parsed_test_cases, parse_error = parse_test_cases_json_detailed(text)
                if parsed_test_cases:
                    current_test_cases = parsed_test_cases
                    _record_parser_recovery(diagnostics, author, parse_error, text, artifact_label="test-case")
                else:
                    _record_parser_failure(diagnostics, author, parse_error, text)

            if author == "CoveragePlannerAgent":
                parsed_coverage_plan, parse_error = parse_coverage_plan_json_detailed(text)
                if parsed_coverage_plan:
                    current_coverage_plan = _normalize_coverage_plan(parsed_coverage_plan, requirements)
                    _record_parser_recovery(diagnostics, author, parse_error, text, artifact_label="coverage-plan")
                else:
                    _record_parser_failure(diagnostics, author, parse_error, text)

            if author == "RequirementAnalysisAgent":
                parsed_requirement_analysis, parse_error = parse_requirement_analysis_json_detailed(text)
                if parsed_requirement_analysis:
                    current_requirement_analysis = normalize_requirement_analysis(parsed_requirement_analysis, requirements)
                    _record_parser_recovery(diagnostics, author, parse_error, text, artifact_label="requirement-analysis")
                else:
                    _record_parser_failure(diagnostics, author, parse_error, text)

            if author == "TestCaseValidatorAgent":
                parsed_review, parse_error = parse_review_json_detailed(text, default_threshold=threshold)
                if not parsed_review:
                    _record_parser_failure(diagnostics, author, parse_error, text, retryable=True)
                    continue

                model_review = _normalize_review_result(parsed_review, threshold, "Test case validation completed.")
                iteration_number = len(iteration_history) + 1
                iteration_history.append(
                    _make_history_entry(
                        iteration=iteration_number,
                        actor=author,
                        review=model_review,
                        test_cases=current_test_cases,
                    )
                )

                candidate_review = _merge_review_results(
                    model_review,
                    _heuristic_test_case_review(
                        current_test_cases,
                        requirements,
                        threshold,
                        coverage_plan=current_coverage_plan or _fallback_coverage_plan(requirements),
                        requirement_analysis=current_requirement_analysis or fallback_requirement_analysis(requirements),
                        context=context,
                    ),
                )
                if current_test_cases and (not best_candidate or _prefer_review(candidate_review, best_candidate["review"])):
                    best_candidate = {
                        "test_cases": list(current_test_cases),
                        "review": candidate_review,
                        "iteration": iteration_number,
                    }
                    diagnostics["best_iteration"] = iteration_number

                score_delta = model_review["score"] - (previous_review["score"] if previous_review else 0)
                _log_test_case_workflow(
                    "review_iteration",
                    session_id=session.id,
                    iteration=iteration_number,
                    author=author,
                    score=model_review["score"],
                    threshold=model_review["threshold"],
                    approved=model_review["approved"],
                    score_delta=score_delta,
                    blocking_issue_count=len(model_review["blocking_issues"]),
                    suggestion_count=len(model_review["suggestions"]),
                    test_case_count=len(current_test_cases),
                )

                if _review_is_stalled(previous_review, model_review):
                    repeated_review_count += 1
                else:
                    repeated_review_count = 1
                previous_review = model_review

                if repeated_review_count >= stall_iteration_limit:
                    diagnostics["status"] = "partial"
                    diagnostics["stalled"] = True
                    diagnostics["failure_reason"] = diagnostics["failure_reason"] or "stalled"
                    _append_unique_message(
                        diagnostics["warnings"],
                        f"Test-case validation stalled after {repeated_review_count} repeated review cycles.",
                    )
                    _log_test_case_workflow(
                        "review_stalled",
                        session_id=session.id,
                        repeated_review_count=repeated_review_count,
                        stall_iteration_limit=stall_iteration_limit,
                        last_score=model_review["score"],
                    )
                    return

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
            f"Test-case workflow timed out after {timeout_seconds} second(s).",
        )
        _log_test_case_workflow(
            "workflow_timeout",
            session_id=session.id,
            timeout_seconds=timeout_seconds,
            completed_iterations=len(iteration_history),
        )

    updated_session = await session_service.get_session(
        app_name="test_case_generator",
        user_id=user_id,
        session_id=session.id,
    )
    session_state = updated_session.state if updated_session else session.state

    state_test_cases_raw = session_state.get(STATE_TEST_CASES, "[]")
    state_test_cases, state_test_cases_error = parse_test_cases_json_detailed(state_test_cases_raw)
    had_event_test_cases = bool(current_test_cases)
    if state_test_cases:
        current_test_cases = state_test_cases
        if not had_event_test_cases:
            _record_parser_recovery(
                diagnostics,
                "SessionStateTestCases",
                state_test_cases_error,
                state_test_cases_raw,
                artifact_label="test-case",
            )
    elif str(state_test_cases_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateTestCases", state_test_cases_error, state_test_cases_raw)

    state_coverage_plan_raw = session_state.get(STATE_COVERAGE_PLAN, "[]")
    state_coverage_plan, state_coverage_plan_error = parse_coverage_plan_json_detailed(state_coverage_plan_raw)
    had_event_coverage_plan = bool(current_coverage_plan)
    if state_coverage_plan:
        current_coverage_plan = _normalize_coverage_plan(state_coverage_plan, requirements)
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

    state_requirement_analysis_raw = session_state.get(STATE_REQUIREMENT_ANALYSIS, "[]")
    state_requirement_analysis, state_requirement_analysis_error = parse_requirement_analysis_json_detailed(state_requirement_analysis_raw)
    had_event_requirement_analysis = bool(current_requirement_analysis)
    if state_requirement_analysis:
        current_requirement_analysis = normalize_requirement_analysis(state_requirement_analysis, requirements)
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

    if not current_requirement_analysis:
        current_requirement_analysis = fallback_requirement_analysis(requirements)

    if not current_coverage_plan:
        current_coverage_plan = _fallback_coverage_plan(requirements)

    state_review_raw = session_state.get(STATE_VALIDATION_FEEDBACK, "")
    state_review, state_review_error = parse_review_json_detailed(state_review_raw, default_threshold=threshold)
    if state_review:
        model_review = _normalize_review_result(state_review, threshold, "Test case validation completed.")
    elif str(state_review_raw).strip():
        _record_parser_failure(
            diagnostics,
            "SessionStateValidationReview",
            state_review_error,
            state_review_raw,
            retryable=True,
        )

    heuristic_review = _heuristic_test_case_review(
        current_test_cases,
        requirements,
        threshold,
        coverage_plan=current_coverage_plan,
        requirement_analysis=current_requirement_analysis,
        context=context,
    )
    final_review = _merge_review_results(model_review, heuristic_review)

    if best_candidate and (not current_test_cases or _prefer_review(best_candidate["review"], final_review)):
        current_test_cases = list(best_candidate["test_cases"])
        final_review = dict(best_candidate["review"])
        diagnostics["best_iteration"] = best_candidate["iteration"]
        _append_unique_message(
            diagnostics["warnings"],
            f"Retained the best-scoring test-case draft from iteration {best_candidate['iteration']}",
        )
        _log_test_case_workflow(
            "best_artifact_retained",
            session_id=session.id,
            selected_iteration=best_candidate["iteration"],
            selected_score=final_review["score"],
            test_case_count=len(current_test_cases),
        )

    coverage_metrics = _compute_test_case_coverage_metrics(current_test_cases, requirements)
    coverage_metrics.update(_compute_planned_scenario_metrics(current_coverage_plan, current_test_cases, requirements))
    coverage_metrics.update(_compute_requirement_analysis_metrics(current_requirement_analysis, current_test_cases, requirements))
    coverage_metrics.update(_compute_grounded_context_metrics(current_test_cases, context))

    if len(iteration_history) >= max_iterations and not final_review["approved"] and not diagnostics["stalled"]:
        diagnostics["status"] = "partial"
        diagnostics["max_iterations_reached"] = True
        _append_unique_message(
            diagnostics["warnings"],
            f"Test-case workflow reached the max iteration limit ({max_iterations}).",
        )

    if not current_test_cases:
        diagnostics["status"] = "failed"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or ("empty_refinement" if is_refinement else "empty_generation")
    elif not final_review["approved"] and not diagnostics["failure_reason"]:
        diagnostics["failure_reason"] = "quality_rejection"

    if iteration_history and diagnostics.get("best_iteration") in {None, iteration_history[-1]["iteration"]}:
        iteration_history[-1] = _make_history_entry(
            iteration=iteration_history[-1]["iteration"],
            actor=iteration_history[-1]["actor"],
            review=final_review,
            test_cases=current_test_cases,
        )
    elif iteration_history:
        selection_entry = _make_history_entry(
            iteration=len(iteration_history) + 1,
            actor="WorkflowSelection",
            review=final_review,
            test_cases=current_test_cases,
        )
        selection_entry["summary"] = (f"Retained best-scoring test cases from iteration {diagnostics['best_iteration']}. {final_review['summary']}").strip()
        iteration_history.append(selection_entry)
    else:
        iteration_history.append(
            _make_history_entry(
                iteration=1,
                actor="HeuristicValidation",
                review=final_review,
                test_cases=current_test_cases,
            )
        )

    _log_test_case_workflow(
        "workflow_completed",
        session_id=session.id,
        approved=final_review["approved"],
        score=final_review["score"],
        threshold=final_review["threshold"],
        test_case_count=len(current_test_cases),
        iteration_count=len(iteration_history),
        diagnostics=diagnostics,
    )
    return {
        "test_cases": current_test_cases,
        "requirement_analysis": current_requirement_analysis,
        "coverage_plan": current_coverage_plan,
        "approved": final_review["approved"],
        "review": final_review,
        "iteration_history": iteration_history,
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": diagnostics,
    }


def _run_workflow_sync(**kwargs: Any) -> Dict[str, Any]:
    context_token = bind_log_context(**_test_case_workflow_context(kwargs))
    try:
        return _run_workflow_sync_inner(**kwargs)
    finally:
        reset_log_context(context_token)


def _run_workflow_sync_inner(**kwargs: Any) -> Dict[str, Any]:
    resolved_settings = _resolve_test_case_workflow_settings(kwargs.get("workflow_settings"))
    attempt_total = int(resolved_settings["retry_attempts"] or 0) + 1
    last_error: Optional[Exception] = None

    for attempt in range(1, attempt_total + 1):
        run_kwargs = dict(kwargs)
        run_kwargs["workflow_settings"] = WorkflowSettings(**resolved_settings)

        try:
            try:
                asyncio.get_running_loop()
                import nest_asyncio

                nest_asyncio.apply()
                result = asyncio.run(_run_test_case_workflow_async(**run_kwargs))
            except RuntimeError:
                result = asyncio.run(_run_test_case_workflow_async(**run_kwargs))

            diagnostics = dict(result.get("workflow_diagnostics") or _new_workflow_diagnostics())
            diagnostics["attempt_count"] = attempt
            result["workflow_diagnostics"] = diagnostics
            result.setdefault("workflow_settings", dict(resolved_settings))

            if diagnostics.get("timed_out") and attempt < attempt_total:
                _log_test_case_workflow(
                    "workflow_retry",
                    attempt=attempt,
                    attempt_total=attempt_total,
                    retry_reason="timeout",
                )
                continue

            if has_retryable_parser_failure(diagnostics) and attempt < attempt_total:
                _log_test_case_workflow(
                    "workflow_retry",
                    attempt=attempt,
                    attempt_total=attempt_total,
                    retry_reason=retry_reason(diagnostics),
                )
                continue

            result["workflow_diagnostics"] = public_workflow_diagnostics(diagnostics)
            return result
        except Exception as exc:
            last_error = exc
            _log_test_case_workflow(
                "workflow_attempt_error",
                attempt=attempt,
                attempt_total=attempt_total,
                error=str(exc),
            )
            if attempt >= attempt_total:
                break

    requirements = kwargs.get("requirements") or []
    fallback_plan = _fallback_coverage_plan(requirements)
    fallback_analysis = fallback_requirement_analysis(requirements)
    diagnostics = _new_workflow_diagnostics(attempt_count=attempt_total)
    diagnostics["status"] = "failed"
    diagnostics["failure_reason"] = "execution_error"
    if last_error:
        _append_unique_message(diagnostics["warnings"], f"Workflow execution error: {last_error}")
    _log_test_case_workflow(
        "workflow_failed",
        attempt_total=attempt_total,
        error=str(last_error) if last_error else None,
        diagnostics=diagnostics,
    )
    return {
        "test_cases": [],
        "requirement_analysis": fallback_analysis,
        "coverage_plan": fallback_plan,
        "approved": False,
        "review": _heuristic_test_case_review(
            [],
            requirements,
            int(resolved_settings["approval_threshold"] or DEFAULT_TEST_CASE_THRESHOLD),
            coverage_plan=fallback_plan,
            requirement_analysis=fallback_analysis,
            context=kwargs.get("context"),
        ),
        "iteration_history": [],
        "coverage_metrics": {
            **_compute_test_case_coverage_metrics([], requirements),
            **_compute_planned_scenario_metrics(fallback_plan, [], requirements),
            **_compute_requirement_analysis_metrics(fallback_analysis, [], requirements),
            **_compute_grounded_context_metrics([], kwargs.get("context")),
        },
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": diagnostics,
    }


@dataclass(frozen=True)
class _ParallelTestCaseShard:
    index: int
    requirements: List[Requirement]
    requirement_analysis: List[Dict[str, Any]]
    coverage_plan: List[Dict[str, Any]]

    @property
    def shard_id(self) -> str:
        return f"test-case-shard-{self.index:02d}"


@dataclass(frozen=True)
class _ParallelTestCaseShardResult:
    shard: _ParallelTestCaseShard
    test_cases: List[Dict[str, Any]]
    diagnostics: Dict[str, Any]
    used_fallback: bool = False
    failed: bool = False
    evidence: Optional[Dict[str, Any]] = None


def _serialize_contract_items(items: List[Any]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            serialized.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            serialized.append(dict(item))
    return serialized


def _scenario_count(coverage_plan: List[Dict[str, Any]]) -> int:
    return sum(len(item.get("scenarios") or []) for item in coverage_plan or [])


def _should_use_parallel_test_case_generation(
    requirement_analysis: List[Dict[str, Any]],
    coverage_plan: List[Dict[str, Any]],
    generation_settings: GenerationSettings,
) -> bool:
    if not generation_settings.parallel_test_case_generation_enabled:
        return False
    if not requirement_analysis or not coverage_plan:
        return False
    if len(coverage_plan) <= 1:
        return False
    return _scenario_count(coverage_plan) >= generation_settings.parallel_test_case_min_scenarios


def _plan_parallel_test_case_shards(
    requirements: List[Requirement],
    requirement_analysis: List[Dict[str, Any]],
    coverage_plan: List[Dict[str, Any]],
    *,
    target_scenarios_per_shard: int,
    max_shards: int,
) -> List[_ParallelTestCaseShard]:
    requirement_by_id = {requirement.id: requirement for requirement in requirements}
    analysis_by_id = {str(item.get("requirement_id") or "").strip(): item for item in requirement_analysis or []}
    plan_items = [item for item in coverage_plan or [] if str(item.get("requirement_id") or "").strip() in requirement_by_id]
    if not plan_items:
        return []

    target_scenarios = max(1, int(target_scenarios_per_shard or 1))
    shard_limit = max(1, min(int(max_shards or 1), len(plan_items)))
    shard_plans: List[List[Dict[str, Any]]] = []
    current_plan: List[Dict[str, Any]] = []
    current_scenario_count = 0

    for plan_item in plan_items:
        scenario_count = max(1, len(plan_item.get("scenarios") or []))
        would_exceed_target = current_plan and current_scenario_count + scenario_count > target_scenarios
        can_open_shard = len(shard_plans) + 1 < shard_limit
        if would_exceed_target and can_open_shard:
            shard_plans.append(current_plan)
            current_plan = []
            current_scenario_count = 0
        current_plan.append(plan_item)
        current_scenario_count += scenario_count

    if current_plan:
        shard_plans.append(current_plan)

    shards: List[_ParallelTestCaseShard] = []
    for index, shard_plan in enumerate(shard_plans, start=1):
        shard_requirements = [requirement_by_id[str(item.get("requirement_id") or "").strip()] for item in shard_plan]
        shards.append(
            _ParallelTestCaseShard(
                index=index,
                requirements=shard_requirements,
                requirement_analysis=[
                    analysis_by_id.get(requirement.id) or fallback_requirement_analysis([requirement])[0] for requirement in shard_requirements
                ],
                coverage_plan=shard_plan,
            )
        )
    return shards


async def _run_parallel_test_case_shard_workflow_async(
    *,
    shard: _ParallelTestCaseShard,
    context: Optional[Any],
    requirements_text: str,
    context_text: str,
    template_text: str,
    model: str,
    human_feedback: Optional[str],
    workflow_settings: Optional[WorkflowSettings],
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    resolved_settings = _resolve_test_case_workflow_settings(workflow_settings)
    timeout_seconds = resolved_settings["timeout_seconds"]
    diagnostics = _new_workflow_diagnostics()
    diagnostics.update(
        {
            "shard_count": 1,
            "worker_count": 1,
            "failed_shard_count": 0,
            "fallback_shard_count": 0,
            "merge_warnings": [],
        }
    )

    root_agent = _build_test_case_generator_agent(
        model,
        requirements_text,
        context_text,
        template_text,
        human_feedback=human_feedback,
    )
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="parallel_test_case_generator",
        session_service=session_service,
    )
    user_id = str(actor_user_id or f"user_{uuid.uuid4().hex[:8]}")
    session = await session_service.create_session(
        app_name="parallel_test_case_generator",
        user_id=user_id,
        state={
            STATE_TEST_CASES: "[]",
            STATE_REQUIREMENT_ANALYSIS: json.dumps(shard.requirement_analysis),
            STATE_COVERAGE_PLAN: json.dumps(shard.coverage_plan),
        },
    )
    current_test_cases: List[Dict[str, Any]] = []

    async def _consume_events() -> None:
        nonlocal current_test_cases

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text="Generate draft test cases for this shard only. Do not review, persist, bill, or write files.")],
            ),
        ):
            author = getattr(event, "author", "unknown")
            _record_event_error(diagnostics, author, event)
            if getattr(event, "partial", False):
                continue
            text = _combined_event_text(event)
            if not text:
                continue
            if author == "TestCaseGeneratorAgent":
                parsed_test_cases, parse_error = parse_test_cases_json_detailed(text)
                if parsed_test_cases:
                    current_test_cases = parsed_test_cases
                    _record_parser_recovery(diagnostics, author, parse_error, text, artifact_label="test-case")
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
            f"Parallel test-case shard {shard.shard_id} timed out after {timeout_seconds} second(s).",
        )

    updated_session = await session_service.get_session(
        app_name="parallel_test_case_generator",
        user_id=user_id,
        session_id=session.id,
    )
    session_state = updated_session.state if updated_session else session.state
    state_test_cases_raw = session_state.get(STATE_TEST_CASES, "[]")
    state_test_cases, state_test_cases_error = parse_test_cases_json_detailed(state_test_cases_raw)
    had_event_test_cases = bool(current_test_cases)
    if state_test_cases:
        current_test_cases = state_test_cases
        if not had_event_test_cases:
            _record_parser_recovery(
                diagnostics,
                "SessionStateTestCases",
                state_test_cases_error,
                state_test_cases_raw,
                artifact_label="test-case",
            )
    elif str(state_test_cases_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateTestCases", state_test_cases_error, state_test_cases_raw)

    if not current_test_cases:
        diagnostics["status"] = "partial"
        diagnostics["used_fallback"] = True
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "fallback_generated_artifacts"
        current_test_cases = _fallback_raw_test_cases(shard.requirements, context, coverage_plan=shard.coverage_plan)
        _append_unique_message(
            diagnostics["warnings"],
            f"Parallel test-case shard {shard.shard_id} used deterministic fallback test cases.",
        )

    return {
        "test_cases": current_test_cases,
        "workflow_diagnostics": public_workflow_diagnostics(diagnostics),
        "generation_evidence": _shard_evidence(
            shard=shard,
            test_cases=current_test_cases,
            diagnostics=diagnostics,
            used_fallback=bool(diagnostics.get("used_fallback")),
            failed=False,
        ),
    }


def _run_parallel_test_case_shard_workflow_sync(**kwargs: Any) -> Dict[str, Any]:
    try:
        asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return asyncio.run(_run_parallel_test_case_shard_workflow_async(**kwargs))
    except RuntimeError:
        return asyncio.run(_run_parallel_test_case_shard_workflow_async(**kwargs))


def _fallback_parallel_test_case_shard(
    shard: _ParallelTestCaseShard,
    context: Optional[Any],
    *,
    reason: str,
    warning: str,
    failed: bool = False,
) -> _ParallelTestCaseShardResult:
    diagnostics = _new_workflow_diagnostics()
    diagnostics.update(
        {
            "status": "fallback",
            "used_fallback": True,
            "failure_reason": reason,
            "shard_count": 1,
            "worker_count": 1,
            "failed_shard_count": 1 if failed else 0,
            "fallback_shard_count": 1,
            "merge_warnings": [],
        }
    )
    _append_unique_message(diagnostics["warnings"], warning)
    fallback_test_cases = _fallback_raw_test_cases(shard.requirements, context, coverage_plan=shard.coverage_plan)
    return _ParallelTestCaseShardResult(
        shard=shard,
        test_cases=fallback_test_cases,
        diagnostics=diagnostics,
        used_fallback=True,
        failed=failed,
        evidence=_shard_evidence(
            shard=shard,
            test_cases=fallback_test_cases,
            diagnostics=diagnostics,
            used_fallback=True,
            failed=failed,
        ),
    )


def _run_parallel_test_case_shard_with_fallback(
    *,
    shard: _ParallelTestCaseShard,
    context: Optional[Any],
    template: Any,
    model: str,
    human_feedback: Optional[str],
    workflow_settings: Optional[WorkflowSettings],
    actor_user_id: Optional[str],
) -> _ParallelTestCaseShardResult:
    requirements_text, context_text, template_text = _prepare_workflow_inputs(shard.requirements, context, template)
    try:
        workflow = _run_parallel_test_case_shard_workflow_sync(
            shard=shard,
            context=context,
            requirements_text=requirements_text,
            context_text=context_text,
            template_text=template_text,
            model=model,
            human_feedback=human_feedback,
            workflow_settings=workflow_settings,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        return _fallback_parallel_test_case_shard(
            shard,
            context,
            reason="shard_execution_error",
            warning=f"Parallel test-case shard {shard.shard_id} failed and was replaced with deterministic fallback cases: {exc}",
            failed=True,
        )

    diagnostics = dict(workflow.get("workflow_diagnostics") or {})
    return _ParallelTestCaseShardResult(
        shard=shard,
        test_cases=list(workflow.get("test_cases") or []),
        diagnostics=diagnostics,
        used_fallback=bool(diagnostics.get("used_fallback")),
        failed=False,
        evidence=dict(workflow.get("generation_evidence") or {}),
    )


def _scenario_lookup(coverage_plan: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    order = 0
    for plan_item in coverage_plan or []:
        requirement_id = str(plan_item.get("requirement_id") or "").strip()
        for scenario in plan_item.get("scenarios") or []:
            scenario_id = str(scenario.get("id") or "").strip()
            if not scenario_id:
                continue
            lookup[scenario_id] = {
                "order": order,
                "requirement_id": requirement_id,
                "scenario_type": scenario.get("scenario_type") or "Happy Path",
            }
            order += 1
    return lookup


def _merge_parallel_test_cases(
    shard_results: List[_ParallelTestCaseShardResult],
    requirements: List[Requirement],
    coverage_plan: List[Dict[str, Any]],
    generation_mode: str,
    generation_pass_id: str,
) -> tuple[List[Dict[str, Any]], List[str]]:
    requirement_id_set = {requirement.id for requirement in requirements}
    scenario_by_id = _scenario_lookup(coverage_plan)
    ordered_scenario_ids = [scenario_id for scenario_id, details in sorted(scenario_by_id.items(), key=lambda item: item[1]["order"])]
    assigned_scenarios: set[str] = set()
    raw_cases: List[Dict[str, Any]] = []

    for result in sorted(shard_results, key=lambda item: item.shard.index):
        if result.used_fallback:
            generation_source = GENERATION_SOURCE_DETERMINISTIC_FULL_FALLBACK
        elif generation_mode == "parallel_retry":
            generation_source = GENERATION_SOURCE_PARALLEL_RETRY
        else:
            generation_source = _model_generation_source(result.diagnostics)
        raw_cases.extend(
            _with_test_case_provenance(
                list(result.test_cases or []),
                generation_source=generation_source,
                generation_pass_id=generation_pass_id,
                source_shard_id=result.shard.shard_id,
            )
        )

    def sort_key(item: tuple[int, Dict[str, Any]]) -> tuple[int, int]:
        index, test_case = item
        scenario_refs = [reference for reference in _extract_scenario_refs_from_test_case(test_case) if reference in scenario_by_id]
        if scenario_refs:
            return min(scenario_by_id[reference]["order"] for reference in scenario_refs), index
        return len(ordered_scenario_ids) + index, index

    merge_warnings: List[str] = []
    id_remapped = False
    traceability_repaired = False
    merged: List[Dict[str, Any]] = []

    for _, raw_case in sorted(enumerate(raw_cases), key=sort_key):
        test_case = dict(raw_case)
        original_id = str(test_case.get("id") or "").strip()
        stable_id = f"TC-{len(merged) + 1:03d}"
        if original_id != stable_id:
            id_remapped = True
        test_case["id"] = stable_id

        scenario_refs = _dedupe_preserve([reference for reference in _extract_scenario_refs_from_test_case(test_case) if reference in scenario_by_id])
        linked_requirement_ids = _dedupe_preserve(
            [
                requirement_id
                for requirement_id in _extract_linked_requirement_ids_from_test_case(test_case, requirement_id_set)
                if requirement_id in requirement_id_set
            ]
        )
        if not scenario_refs:
            candidate_ids = linked_requirement_ids or [requirement.id for requirement in requirements]
            for scenario_id in ordered_scenario_ids:
                details = scenario_by_id[scenario_id]
                if scenario_id in assigned_scenarios or details["requirement_id"] not in candidate_ids:
                    continue
                scenario_refs = [scenario_id]
                break
        if scenario_refs and not linked_requirement_ids:
            linked_requirement_ids = _dedupe_preserve([scenario_by_id[scenario_id]["requirement_id"] for scenario_id in scenario_refs])
            traceability_repaired = True
        if not linked_requirement_ids:
            linked_requirement_ids = [requirements[0].id] if requirements else []
            traceability_repaired = True

        assigned_scenarios.update(scenario_refs)
        scenario_tags = [_scenario_tag(str(scenario_by_id[scenario_id]["scenario_type"])) for scenario_id in scenario_refs if scenario_id in scenario_by_id]
        tags = _dedupe_preserve([str(tag) for tag in (test_case.get("tags") or []) if str(tag).strip()] + linked_requirement_ids + scenario_tags)
        test_case["linked_requirement_ids"] = linked_requirement_ids
        test_case["scenario_refs"] = scenario_refs
        test_case["tags"] = tags
        merged.append(test_case)

    if id_remapped:
        merge_warnings.append("Remapped worker-generated test-case IDs to stable global TC-* IDs.")
    if traceability_repaired:
        merge_warnings.append("Repaired missing worker traceability from coverage-plan scenario references.")
    return merged, merge_warnings


def _merge_parallel_diagnostics(
    diagnostics: Dict[str, Any],
    shard_results: List[_ParallelTestCaseShardResult],
    merge_warnings: List[str],
) -> None:
    failed_shards = sum(1 for result in shard_results if result.failed)
    fallback_shards = sum(1 for result in shard_results if result.used_fallback)
    diagnostics["failed_shard_count"] = failed_shards
    diagnostics["fallback_shard_count"] = fallback_shards
    diagnostics["used_fallback"] = fallback_shards > 0
    diagnostics["merge_warnings"] = _dedupe_preserve(list(diagnostics.get("merge_warnings") or []) + merge_warnings)
    for warning in merge_warnings:
        _append_unique_message(diagnostics["warnings"], warning)

    for result in shard_results:
        shard_diagnostics = result.diagnostics or {}
        for key in ("parser_failures", "parser_recoveries", "warnings"):
            for message in shard_diagnostics.get(key) or []:
                _append_unique_message(diagnostics[key], str(message))
        if shard_diagnostics.get("timed_out"):
            diagnostics["timed_out"] = True

    if failed_shards:
        diagnostics["status"] = "partial"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "shard_fallback"
    elif fallback_shards:
        diagnostics["status"] = "partial"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or "fallback_generated_artifacts"
    elif diagnostics["parser_failures"] or diagnostics["parser_recoveries"]:
        diagnostics["status"] = "partial"
        diagnostics["failure_reason"] = diagnostics["failure_reason"] or ("parser_failure" if diagnostics["parser_failures"] else None)


def _run_parallel_test_case_generation_sync(
    *,
    requirements: List[Requirement],
    context: Optional[Any],
    template: Any,
    model: str,
    requirement_analysis: List[Dict[str, Any]],
    coverage_plan: List[Dict[str, Any]],
    human_feedback: Optional[str],
    workflow_settings: Optional[WorkflowSettings],
    generation_settings: GenerationSettings,
    actor_user_id: Optional[str],
    request_id: Optional[str],
    workflow_run_id: Optional[str],
    operation: Optional[str],
    generation_mode: str = "parallel_direct",
) -> Dict[str, Any]:
    context_token = bind_log_context(
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        actor_user_id=actor_user_id,
        operation=operation or "testcases.generate",
    )
    try:
        resolved_settings = _resolve_test_case_workflow_settings(workflow_settings)
        threshold = int(resolved_settings["approval_threshold"] or DEFAULT_TEST_CASE_THRESHOLD)
        shards = _plan_parallel_test_case_shards(
            requirements,
            requirement_analysis,
            coverage_plan,
            target_scenarios_per_shard=generation_settings.parallel_test_case_target_scenarios_per_shard,
            max_shards=generation_settings.parallel_test_case_max_shards,
        )
        worker_count = min(generation_settings.parallel_test_case_max_workers, len(shards)) if shards else 0
        diagnostics = _new_workflow_diagnostics()
        diagnostics.update(
            {
                "generation_route": "parallel_retry" if generation_mode == "parallel_retry" else "direct_parallel",
                "shard_count": len(shards),
                "worker_count": worker_count,
                "failed_shard_count": 0,
                "fallback_shard_count": 0,
                "merge_warnings": [],
            }
        )
        _log_test_case_workflow(
            "parallel_generation_started",
            requirement_count=len(requirements),
            planned_scenario_count=_scenario_count(coverage_plan),
            shard_count=len(shards),
            worker_count=worker_count,
        )

        generation_pass_id = str(uuid.uuid4())
        result_by_index: Dict[int, _ParallelTestCaseShardResult] = {}
        with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
            future_by_shard = {
                executor.submit(
                    _run_parallel_test_case_shard_with_fallback,
                    shard=shard,
                    context=context,
                    template=template,
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
                    result_by_index[shard.index] = _fallback_parallel_test_case_shard(
                        shard,
                        context,
                        reason="shard_execution_error",
                        warning=f"Parallel test-case shard {shard.shard_id} failed and was replaced with deterministic fallback cases: {exc}",
                        failed=True,
                    )

        shard_results = [result_by_index[shard.index] for shard in shards]
        merged_test_cases, merge_warnings = _merge_parallel_test_cases(
            shard_results,
            requirements,
            coverage_plan,
            generation_mode,
            generation_pass_id,
        )
        _merge_parallel_diagnostics(diagnostics, shard_results, merge_warnings)
        _update_generation_source_counts(diagnostics, merged_test_cases)
        review = _heuristic_test_case_review(
            merged_test_cases,
            requirements,
            threshold,
            coverage_plan=coverage_plan,
            requirement_analysis=requirement_analysis,
            context=context,
        )
        iteration_history = [
            _make_history_entry(
                iteration=1,
                actor="ParallelGenerationMerge",
                review=review,
                test_cases=merged_test_cases,
            )
        ]
        coverage_metrics = _compute_test_case_coverage_metrics(merged_test_cases, requirements)
        coverage_metrics.update(_compute_planned_scenario_metrics(coverage_plan, merged_test_cases, requirements))
        coverage_metrics.update(_compute_requirement_analysis_metrics(requirement_analysis, merged_test_cases, requirements))
        coverage_metrics.update(_compute_grounded_context_metrics(merged_test_cases, context))
        _log_test_case_workflow(
            "parallel_generation_completed",
            approved=review["approved"],
            score=review["score"],
            threshold=threshold,
            test_case_count=len(merged_test_cases),
            shard_count=diagnostics["shard_count"],
            fallback_shard_count=diagnostics["fallback_shard_count"],
            failed_shard_count=diagnostics["failed_shard_count"],
        )
        shard_evidence = [
            result.evidence
            or _shard_evidence(
                shard=result.shard,
                test_cases=result.test_cases,
                diagnostics=result.diagnostics,
                used_fallback=result.used_fallback,
                failed=result.failed,
            )
            for result in shard_results
        ]
        generation_evidence = _new_generation_evidence(
            requirements=requirements,
            coverage_plan=coverage_plan,
            model_name=model,
            operation=operation,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            workflow_settings=resolved_settings,
            generation_settings=generation_settings,
        )
        generation_evidence = _append_generation_pass(
            generation_evidence,
            _generation_pass_evidence(
                pass_type=generation_mode,
                requirements=requirements,
                coverage_plan=coverage_plan,
                model_name=model,
                diagnostics=diagnostics,
                review=review,
                model_case_count=sum(0 if result.used_fallback else len(result.test_cases) for result in shard_results),
                merged_case_count=len(merged_test_cases),
                fallback_case_count=sum(len(result.test_cases) for result in shard_results if result.used_fallback),
                prompt_metadata=_prompt_metadata(
                    context=context,
                    template=template,
                    human_feedback=human_feedback,
                ),
                shards=shard_evidence,
                pass_id=generation_pass_id,
            ),
        )
        return {
            "test_cases": merged_test_cases,
            "requirement_analysis": requirement_analysis,
            "coverage_plan": coverage_plan,
            "approved": review["approved"],
            "review": review,
            "iteration_history": iteration_history,
            "coverage_metrics": coverage_metrics,
            "workflow_settings": resolved_settings,
            "workflow_diagnostics": public_workflow_diagnostics(diagnostics),
            "generation_evidence": generation_evidence,
        }
    finally:
        reset_log_context(context_token)


def _format_requirement_for_prompt(requirement: Requirement) -> str:
    parts = [f"- {requirement.id}: {requirement.text}"]
    context_bits: List[str] = []
    if requirement.source_path:
        context_bits.append(f"source_path={requirement.source_path}")
    elif requirement.source_issue_key:
        context_bits.append(f"source={requirement.source_issue_key}")
    if requirement.source_issue_type:
        context_bits.append(f"source_type={requirement.source_issue_type}")
    if requirement.source_hierarchy:
        context_bits.append(f"hierarchy={' > '.join(requirement.source_hierarchy)}")
    if requirement.source_excerpt:
        context_bits.append(f"source_excerpt={requirement.source_excerpt[:240]}")
    if context_bits:
        parts.append(f"  Context: {' | '.join(context_bits)}")
    return "\n".join(parts)


def _prepare_workflow_inputs(requirements: List[Requirement], context: Optional[Any], template: Any) -> tuple[str, str, str]:
    requirements_text = "\n".join([_format_requirement_for_prompt(req) for req in requirements])

    context_parts = []
    if context:
        if context.app_link:
            context_parts.append(f"Application URL: {context.app_link}")
        if context.prototype_link:
            context_parts.append(f"Prototype URL: {context.prototype_link}")
        if context.diagram_links:
            context_parts.append(f"Diagrams: {', '.join(str(link) for link in context.diagram_links)}")
        if context.image_links:
            context_parts.append(f"Images: {', '.join(str(link) for link in context.image_links)}")
        if context.notes:
            context_parts.append(f"Notes: {context.notes}")
        if context.grounded_context:
            grounded_context = context.grounded_context
            if grounded_context.summary:
                context_parts.append(f"Grounded context summary: {grounded_context.summary}")
            if grounded_context.artifact_sources:
                sources = ", ".join(f"{source.id} ({source.label}, {source.status})" for source in grounded_context.artifact_sources[:8])
                context_parts.append(f"Grounded artifact sources: {sources}")
            if grounded_context.ui_elements:
                ui_elements = ", ".join(
                    (f'{element.element_type}: exact text "{element.name}"' + (f" -> {element.href}" if getattr(element, "href", None) else ""))
                    for element in grounded_context.ui_elements[:12]
                )
                context_parts.append(f"Grounded UI elements: {ui_elements}")
            if grounded_context.api_surfaces:
                api_surfaces = ", ".join(f"{surface.method or 'API'} {surface.path or surface.name}" for surface in grounded_context.api_surfaces[:8])
                context_parts.append(f"Grounded API surfaces: {api_surfaces}")
            if grounded_context.workflows:
                workflows = ", ".join(f"{workflow.name} [{'; '.join(workflow.transitions[:4])}]" for workflow in grounded_context.workflows[:4])
                context_parts.append(f"Grounded workflows: {workflows}")
    context_text = "\n".join(context_parts) if context_parts else "No additional context provided."

    template_text = f"Name: {template.name}, Format: {template.format}, Fields: {', '.join(template.fields)}"
    return requirements_text, context_text, template_text


def _build_response(test_cases: List[TestCase], workflow: Dict[str, Any], requirements: List[Requirement], context: Optional[Any]) -> Dict[str, Any]:
    serialized = _serialize_test_cases(test_cases)
    raw_requirement_analysis = list(workflow.get("requirement_analysis") or fallback_requirement_analysis(requirements))
    raw_coverage_plan = list(workflow.get("coverage_plan") or _fallback_coverage_plan(requirements))
    normalized_coverage_plan = _normalize_coverage_plan(raw_coverage_plan, requirements)
    resolved_settings = dict(workflow.get("workflow_settings") or {})
    threshold = int(resolved_settings.get("approval_threshold") or DEFAULT_TEST_CASE_THRESHOLD)
    default_coverage_metrics = _compute_test_case_coverage_metrics(serialized, requirements)
    default_coverage_metrics.update(_compute_planned_scenario_metrics(normalized_coverage_plan, serialized, requirements))
    default_coverage_metrics.update(_compute_requirement_analysis_metrics(raw_requirement_analysis, serialized, requirements))
    default_coverage_metrics.update(_compute_grounded_context_metrics(serialized, context))
    coverage_metrics = dict(workflow.get("coverage_metrics") or default_coverage_metrics)
    review = dict(
        workflow.get("review")
        or _heuristic_test_case_review(
            serialized,
            requirements,
            threshold,
            coverage_plan=normalized_coverage_plan,
            requirement_analysis=raw_requirement_analysis,
            context=context,
        )
    )
    approved = bool(workflow.get("approved", False))
    coverage_plan = _hydrate_coverage_plan(normalized_coverage_plan, requirements)
    requirement_analysis = _hydrate_requirement_analysis(raw_requirement_analysis, requirements)
    workflow_diagnostics = _update_generation_source_counts(dict(workflow.get("workflow_diagnostics") or {}), serialized)
    workflow_diagnostics = _update_scenario_ref_diagnostics(workflow_diagnostics, coverage_metrics)

    return {
        "test_cases": test_cases,
        "approved": approved,
        "review": review,
        "iteration_history": list(workflow.get("iteration_history") or []),
        "coverage_plan": coverage_plan,
        "requirement_analysis": requirement_analysis,
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": public_workflow_diagnostics(workflow_diagnostics),
        "generation_evidence": dict(workflow.get("generation_evidence") or {}),
    }


def _maybe_retry_with_parallel_generation(
    *,
    workflow: Dict[str, Any],
    payload: GenerateTestCasesInput,
    settings: Any,
    generation_settings: GenerationSettings,
    requirement_analysis: List[Dict[str, Any]],
    coverage_plan: List[Dict[str, Any]],
    actor_user_id: Optional[str],
    request_id: Optional[str],
    workflow_run_id: Optional[str],
    operation: Optional[str],
) -> Dict[str, Any]:
    """Recover model-authored coverage when a single-shot pass under-produces.

    The sequential pipeline generates every test case in one model call, which
    under-delivers for large requirement sets and forces heavy deterministic
    backfill. When the internally generated coverage plan is large enough to
    qualify for bounded parallel generation, retry with the existing sharded
    generator so the model itself produces the missing coverage before
    deterministic augmentation runs. Returns the original workflow when a retry
    is not applicable or does not improve the result.
    """
    if not _should_use_parallel_test_case_generation(requirement_analysis, coverage_plan, generation_settings):
        return workflow

    raw_test_cases = list(workflow.get("test_cases") or [])
    planned_scenarios = _scenario_count(coverage_plan)
    if planned_scenarios <= len(raw_test_cases):
        return workflow

    # Only reshard when there are more requirements than shards, i.e. when a
    # single model call must cover a slice large enough to risk truncation.
    # Small requirement sets are handled well by a single pass plus cheap
    # deterministic coverage augmentation, so avoid the extra model passes there.
    if len(payload.requirements) <= generation_settings.parallel_test_case_max_workers:
        return workflow

    _log_test_case_workflow(
        "parallel_generation_retry",
        requirement_count=len(payload.requirements),
        planned_scenario_count=planned_scenarios,
        produced_test_case_count=len(raw_test_cases),
    )

    try:
        parallel_workflow = _run_parallel_test_case_generation_sync(
            requirements=payload.requirements,
            context=payload.context,
            template=payload.template,
            model=settings.model_name,
            requirement_analysis=requirement_analysis,
            coverage_plan=coverage_plan,
            human_feedback=payload.feedback if payload.feedback else None,
            workflow_settings=payload.workflow_settings,
            generation_settings=generation_settings,
            actor_user_id=actor_user_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation=operation,
            generation_mode="parallel_retry",
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logging.exception("[TestCase Workflow] Parallel regeneration retry failed: %s", exc)
        return workflow

    parallel_test_cases = list(parallel_workflow.get("test_cases") or [])
    if not parallel_test_cases:
        return workflow

    current_review = dict(workflow.get("review") or {})
    parallel_review = dict(parallel_workflow.get("review") or {})
    if len(parallel_test_cases) <= len(raw_test_cases) and not _prefer_review(parallel_review, current_review):
        return workflow

    parallel_diagnostics = {**_new_workflow_diagnostics(), **dict(parallel_workflow.get("workflow_diagnostics") or {})}
    parallel_diagnostics["generation_route"] = "parallel_retry"
    parallel_diagnostics["recovery_reason"] = "parallel_generation_retry"
    if parallel_diagnostics.get("status") == "completed":
        parallel_diagnostics["status"] = "partial"
    _append_unique_message(
        parallel_diagnostics["warnings"],
        (
            f"Single-pass generation produced {len(raw_test_cases)} case(s) for {planned_scenarios} planned scenario(s); "
            f"regenerated with parallel shards to recover model coverage ({len(parallel_test_cases)} case(s))."
        ),
    )
    if len(parallel_test_cases) < len(raw_test_cases):
        _append_unique_message(
            parallel_diagnostics["warnings"],
            (
                "Accepted lower-count parallel retry because review quality improved "
                f"from {current_review.get('score', 0)}/{current_review.get('threshold', DEFAULT_TEST_CASE_THRESHOLD)} "
                f"to {parallel_review.get('score', 0)}/{parallel_review.get('threshold', DEFAULT_TEST_CASE_THRESHOLD)}; "
                f"case count delta {len(parallel_test_cases) - len(raw_test_cases)}."
            ),
        )
    parallel_workflow["workflow_diagnostics"] = parallel_diagnostics
    parallel_workflow["generation_evidence"] = _merge_generation_evidence(
        dict(workflow.get("generation_evidence") or {}),
        _retag_generation_passes(dict(parallel_workflow.get("generation_evidence") or {}), pass_type="parallel_retry"),
    )
    return parallel_workflow


def generate_test_cases(
    payload: GenerateTestCasesInput,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    settings = _get_model_settings_or_none()
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)
    precomputed_requirement_analysis = (
        normalize_requirement_analysis(_serialize_contract_items(payload.requirement_analysis), payload.requirements) if payload.requirement_analysis else []
    )
    precomputed_coverage_plan = (
        _normalize_coverage_plan(_serialize_contract_items(payload.coverage_plan), payload.requirements) if payload.coverage_plan else []
    )
    generation_settings = get_generation_settings()

    if settings is None:
        workflow = {
            "test_cases": [],
            "requirement_analysis": precomputed_requirement_analysis or fallback_requirement_analysis(payload.requirements),
            "coverage_plan": precomputed_coverage_plan or _fallback_coverage_plan(payload.requirements),
            "approved": False,
            "review": None,
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": _resolve_test_case_workflow_settings(payload.workflow_settings),
            "workflow_diagnostics": {
                **_new_workflow_diagnostics(),
                "status": "fallback",
                "used_fallback": True,
                "failure_reason": "missing_model_credentials",
                "generation_route": "deterministic_full_fallback",
                "warnings": ["Model credentials are unavailable; deterministic fallback was used."],
            },
        }
    elif _should_use_parallel_test_case_generation(
        precomputed_requirement_analysis,
        precomputed_coverage_plan,
        generation_settings,
    ):
        workflow = _run_parallel_test_case_generation_sync(
            requirements=payload.requirements,
            context=payload.context,
            template=payload.template,
            model=settings.model_name,
            requirement_analysis=precomputed_requirement_analysis,
            coverage_plan=precomputed_coverage_plan,
            human_feedback=payload.feedback if payload.feedback else None,
            workflow_settings=payload.workflow_settings,
            generation_settings=generation_settings,
            actor_user_id=actor_user_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation=operation,
        )
    else:
        workflow = _run_workflow_sync(
            requirements=payload.requirements,
            context=payload.context,
            requirements_text=requirements_text,
            context_text=context_text,
            template_text=template_text,
            model=settings.model_name,
            human_feedback=payload.feedback if payload.feedback else None,
            existing_test_cases=None,
            workflow_settings=payload.workflow_settings,
            actor_user_id=actor_user_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation=operation,
        )

    raw_test_cases = workflow.get("test_cases", [])
    requirement_analysis = list(workflow.get("requirement_analysis") or fallback_requirement_analysis(payload.requirements))
    coverage_plan = list(workflow.get("coverage_plan") or _fallback_coverage_plan(payload.requirements))
    resolved_settings = dict(workflow.get("workflow_settings") or _resolve_test_case_workflow_settings(payload.workflow_settings))
    threshold = int(resolved_settings.get("approval_threshold") or DEFAULT_TEST_CASE_THRESHOLD)
    if settings is not None and not workflow.get("generation_evidence"):
        diagnostics = {**_new_workflow_diagnostics(), **dict(workflow.get("workflow_diagnostics") or {})}
        diagnostics["generation_route"] = diagnostics.get("generation_route") or "sequential"
        sequential_generation_pass_id = str(uuid.uuid4()) if raw_test_cases else None
        if raw_test_cases:
            raw_test_cases = _with_test_case_provenance(
                list(raw_test_cases),
                generation_source=_model_generation_source(diagnostics),
                generation_pass_id=sequential_generation_pass_id,
            )
            workflow["test_cases"] = raw_test_cases
            workflow["workflow_diagnostics"] = _update_generation_source_counts(diagnostics, raw_test_cases)
        workflow["generation_evidence"] = _append_generation_pass(
            _new_generation_evidence(
                requirements=payload.requirements,
                coverage_plan=coverage_plan,
                model_name=settings.model_name,
                operation=operation or "testcases.generate",
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                workflow_settings=resolved_settings,
                generation_settings=generation_settings,
            ),
            _generation_pass_evidence(
                pass_type="sequential",
                requirements=payload.requirements,
                coverage_plan=coverage_plan,
                model_name=settings.model_name,
                diagnostics=diagnostics,
                review=dict(workflow.get("review") or {}),
                model_case_count=len(raw_test_cases or []),
                merged_case_count=len(raw_test_cases or []),
                prompt_metadata=_prompt_metadata(
                    context=payload.context,
                    template=payload.template,
                    human_feedback=payload.feedback,
                ),
                pass_id=sequential_generation_pass_id,
            ),
        )
    if not raw_test_cases:
        logging.warning("[TestCase Workflow] No test cases from pipeline, using deterministic fallback")
        record_agent_fallback(workflow=operation or "testcases.generate", reason="fallback_generated_artifacts")
        fallback_generation_pass_id = str(uuid.uuid4())
        raw_test_cases = _fallback_raw_test_cases(payload.requirements, payload.context, coverage_plan=coverage_plan)
        raw_test_cases = _with_test_case_provenance(
            raw_test_cases,
            generation_source=GENERATION_SOURCE_DETERMINISTIC_FULL_FALLBACK,
            generation_pass_id=fallback_generation_pass_id,
        )
        fallback_review = _heuristic_test_case_review(
            raw_test_cases,
            payload.requirements,
            threshold,
            coverage_plan=coverage_plan,
            requirement_analysis=requirement_analysis,
            context=payload.context,
        )
        workflow_diagnostics = {**_new_workflow_diagnostics(), **dict(workflow.get("workflow_diagnostics") or {})}
        missing_model_credentials = workflow_diagnostics.get("failure_reason") == "missing_model_credentials"
        if not missing_model_credentials:
            fallback_review["approved"] = False
            fallback_review["summary"] = "Test-case fallback produced a draft suite that still requires review approval."
            fallback_review["blocking_issues"] = _dedupe_preserve(
                fallback_review["blocking_issues"] + ["Deterministic fallback was used instead of a completed generation/validation loop."]
            )
        workflow_diagnostics["status"] = "fallback"
        workflow_diagnostics["used_fallback"] = True
        workflow_diagnostics["failure_reason"] = workflow_diagnostics.get("failure_reason") or "fallback_generated_artifacts"
        workflow_diagnostics["generation_route"] = "deterministic_full_fallback"
        _append_unique_message(
            workflow_diagnostics["warnings"],
            "Test-case fallback produced deterministic draft artifacts because the generation workflow returned no test cases.",
        )
        deterministic_counts = _deterministic_full_fallback_counts(
            fallback_test_cases=raw_test_cases,
            requirements=payload.requirements,
            coverage_plan=coverage_plan,
        )
        _update_completion_diagnostics(workflow_diagnostics, deterministic_counts)
        _update_generation_source_counts(workflow_diagnostics, raw_test_cases)
        generation_evidence = _append_generation_pass(
            dict(
                workflow.get("generation_evidence")
                or _new_generation_evidence(
                    requirements=payload.requirements,
                    coverage_plan=coverage_plan,
                    model_name=settings.model_name if settings is not None else None,
                    operation=operation or "testcases.generate",
                    request_id=request_id,
                    workflow_run_id=workflow_run_id,
                    workflow_settings=resolved_settings,
                    generation_settings=generation_settings,
                )
            ),
            _generation_pass_evidence(
                pass_type="deterministic_full_fallback",
                requirements=payload.requirements,
                coverage_plan=coverage_plan,
                model_name=settings.model_name if settings is not None else None,
                diagnostics=workflow_diagnostics,
                review=fallback_review,
                model_case_count=0,
                merged_case_count=len(raw_test_cases),
                fallback_case_count=len(raw_test_cases),
                deterministic_counts=deterministic_counts,
                prompt_metadata=_prompt_metadata(
                    context=payload.context,
                    template=payload.template,
                    human_feedback=payload.feedback,
                ),
                pass_id=fallback_generation_pass_id,
            ),
        )
        workflow = {
            "test_cases": raw_test_cases,
            "requirement_analysis": requirement_analysis,
            "coverage_plan": coverage_plan,
            "review": fallback_review,
            "approved": bool(fallback_review["approved"]),
            "iteration_history": [
                _make_history_entry(
                    iteration=1,
                    actor="FallbackValidation",
                    review=fallback_review,
                    test_cases=raw_test_cases,
                )
            ],
            "coverage_metrics": {
                **_compute_test_case_coverage_metrics(raw_test_cases, payload.requirements),
                **_compute_planned_scenario_metrics(coverage_plan, raw_test_cases, payload.requirements),
                **_compute_requirement_analysis_metrics(requirement_analysis, raw_test_cases, payload.requirements),
                **_compute_grounded_context_metrics(raw_test_cases, payload.context),
            },
            "workflow_settings": resolved_settings,
            "workflow_diagnostics": workflow_diagnostics,
            "generation_evidence": generation_evidence,
        }
    elif not bool(workflow.get("approved", False)):
        workflow = _maybe_retry_with_parallel_generation(
            workflow=workflow,
            payload=payload,
            settings=settings,
            generation_settings=generation_settings,
            requirement_analysis=requirement_analysis,
            coverage_plan=coverage_plan,
            actor_user_id=actor_user_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation=operation,
        )
        raw_test_cases = list(workflow.get("test_cases") or raw_test_cases)
        requirement_analysis = list(workflow.get("requirement_analysis") or requirement_analysis)
        coverage_plan = list(workflow.get("coverage_plan") or coverage_plan)
        resolved_settings = dict(workflow.get("workflow_settings") or resolved_settings)
        threshold = int(resolved_settings.get("approval_threshold") or DEFAULT_TEST_CASE_THRESHOLD)
        recovery_test_cases = _augment_with_fallback_coverage(
            raw_test_cases,
            payload.requirements,
            payload.context,
            coverage_plan,
        )
        if len(recovery_test_cases) > len(raw_test_cases):
            original_test_case_count = len(raw_test_cases)
            recovery_review = _heuristic_test_case_review(
                recovery_test_cases,
                payload.requirements,
                threshold,
                coverage_plan=coverage_plan,
                requirement_analysis=requirement_analysis,
                context=payload.context,
            )
            current_review = dict(workflow.get("review") or {})
            if _prefer_review(recovery_review, current_review):
                workflow_diagnostics = {**_new_workflow_diagnostics(), **dict(workflow.get("workflow_diagnostics") or {})}
                completion_generation_pass_id = str(uuid.uuid4())
                source_generation_pass_id = _last_generation_pass_id(
                    dict(workflow.get("generation_evidence") or {}),
                    exclude_pass_types={GENERATION_SOURCE_DETERMINISTIC_COVERAGE_COMPLETION},
                ) or str(uuid.uuid4())
                recovery_test_cases = _with_test_case_provenance(
                    recovery_test_cases[:original_test_case_count],
                    generation_source=_model_generation_source(workflow_diagnostics),
                    generation_pass_id=source_generation_pass_id,
                    preserve_existing=True,
                ) + _with_test_case_provenance(
                    recovery_test_cases[original_test_case_count:],
                    generation_source=GENERATION_SOURCE_DETERMINISTIC_COVERAGE_COMPLETION,
                    generation_pass_id=completion_generation_pass_id,
                    coverage_completion_reason="coverage_augmentation",
                )
                deterministic_counts = _coverage_gap_counts(
                    original_test_cases=raw_test_cases,
                    augmented_test_cases=recovery_test_cases,
                    requirements=payload.requirements,
                    coverage_plan=coverage_plan,
                )
                recovery_warning = _coverage_augmentation_warning(
                    original_test_cases=raw_test_cases,
                    augmented_test_cases=recovery_test_cases,
                    requirements=payload.requirements,
                    coverage_plan=coverage_plan,
                    diagnostics=workflow_diagnostics,
                    deterministic_counts=deterministic_counts,
                )
                raw_test_cases = recovery_test_cases
                workflow_diagnostics["status"] = "partial"
                workflow_diagnostics["used_fallback"] = False
                workflow_diagnostics["recovery_reason"] = "coverage_augmentation"
                if workflow_diagnostics.get("failure_reason") in {None, "quality_rejection"}:
                    workflow_diagnostics["failure_reason"] = None
                _append_unique_message(
                    workflow_diagnostics["warnings"],
                    recovery_warning,
                )
                _update_completion_diagnostics(workflow_diagnostics, deterministic_counts)
                _update_generation_source_counts(workflow_diagnostics, recovery_test_cases)
                iteration_history = list(workflow.get("iteration_history") or [])
                iteration_history.append(
                    _make_history_entry(
                        iteration=len(iteration_history) + 1,
                        actor="FallbackCoverageRecovery",
                        review=recovery_review,
                        test_cases=recovery_test_cases,
                    )
                )
                generation_evidence = _append_generation_pass(
                    dict(
                        workflow.get("generation_evidence")
                        or _new_generation_evidence(
                            requirements=payload.requirements,
                            coverage_plan=coverage_plan,
                            model_name=settings.model_name,
                            operation=operation or "testcases.generate",
                            request_id=request_id,
                            workflow_run_id=workflow_run_id,
                            workflow_settings=resolved_settings,
                            generation_settings=generation_settings,
                        )
                    ),
                    _generation_pass_evidence(
                        pass_type="deterministic_coverage_completion",
                        requirements=payload.requirements,
                        coverage_plan=coverage_plan,
                        model_name=settings.model_name,
                        diagnostics=workflow_diagnostics,
                        review=recovery_review,
                        model_case_count=0,
                        merged_case_count=len(recovery_test_cases),
                        fallback_case_count=max(0, len(recovery_test_cases) - original_test_case_count),
                        deterministic_counts=deterministic_counts,
                        prompt_metadata={
                            "raw_prompt_stored": False,
                            "source": "deterministic coverage completion",
                        },
                        pass_id=completion_generation_pass_id,
                    ),
                )
                workflow = {
                    "test_cases": recovery_test_cases,
                    "requirement_analysis": requirement_analysis,
                    "coverage_plan": coverage_plan,
                    "review": recovery_review,
                    "approved": recovery_review["approved"],
                    "iteration_history": iteration_history,
                    "coverage_metrics": {
                        **_compute_test_case_coverage_metrics(recovery_test_cases, payload.requirements),
                        **_compute_planned_scenario_metrics(coverage_plan, recovery_test_cases, payload.requirements),
                        **_compute_requirement_analysis_metrics(requirement_analysis, recovery_test_cases, payload.requirements),
                        **_compute_grounded_context_metrics(recovery_test_cases, payload.context),
                    },
                    "workflow_settings": resolved_settings,
                    "workflow_diagnostics": workflow_diagnostics,
                    "generation_evidence": generation_evidence,
                }

    workflow["generation_evidence"] = _finalize_generation_evidence(
        evidence=dict(workflow.get("generation_evidence") or {}),
        workflow=workflow,
        requirements=payload.requirements,
        coverage_plan=coverage_plan,
        final_test_cases=list(workflow.get("test_cases") or raw_test_cases or []),
        model_name=settings.model_name if settings is not None else None,
        operation=operation or "testcases.generate",
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        workflow_settings=resolved_settings,
        generation_settings=generation_settings,
    )
    test_cases = _hydrate_test_cases(raw_test_cases)
    return _build_response(test_cases, workflow, payload.requirements, payload.context)


def refine_test_cases(
    payload: RefineTestCasesInput,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)

    existing_test_cases = _serialize_test_cases(payload.test_cases)
    settings = _get_model_settings_or_none()
    if settings is None:
        workflow = {
            "test_cases": [],
            "requirement_analysis": fallback_requirement_analysis(payload.requirements),
            "coverage_plan": _fallback_coverage_plan(payload.requirements),
            "approved": False,
            "review": None,
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": _resolve_test_case_workflow_settings(payload.workflow_settings),
            "workflow_diagnostics": {
                **_new_workflow_diagnostics(),
                "status": "fallback",
                "used_fallback": True,
                "failure_reason": "missing_model_credentials",
                "warnings": ["Model credentials are unavailable; previous test cases were restored."],
            },
        }
    else:
        workflow = _run_workflow_sync(
            requirements=payload.requirements,
            context=payload.context,
            requirements_text=requirements_text,
            context_text=context_text,
            template_text=template_text,
            model=settings.model_name,
            human_feedback=payload.feedback,
            existing_test_cases=existing_test_cases,
            workflow_settings=payload.workflow_settings,
            actor_user_id=actor_user_id,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation=operation,
        )

    raw_test_cases = workflow.get("test_cases", []) or existing_test_cases
    requirement_analysis = list(workflow.get("requirement_analysis") or fallback_requirement_analysis(payload.requirements))
    coverage_plan = list(workflow.get("coverage_plan") or _fallback_coverage_plan(payload.requirements))
    resolved_settings = dict(workflow.get("workflow_settings") or _resolve_test_case_workflow_settings(payload.workflow_settings))
    threshold = int(resolved_settings.get("approval_threshold") or DEFAULT_TEST_CASE_THRESHOLD)
    if not workflow.get("test_cases"):
        logging.warning("[TestCase Workflow] Refinement returned no test cases, restoring previous set")
        record_agent_fallback(workflow=operation or "testcases.refine", reason="restored_previous_test_cases")
        fallback_review = _heuristic_test_case_review(
            raw_test_cases,
            payload.requirements,
            threshold,
            coverage_plan=coverage_plan,
            requirement_analysis=requirement_analysis,
            context=payload.context,
        )
        fallback_review["approved"] = False
        fallback_review["summary"] = "Test case refinement returned no updated output. Previous test cases were restored and require further review."
        fallback_review["blocking_issues"] = _dedupe_preserve(fallback_review["blocking_issues"] + ["Refinement loop did not return an updated test-case set."])
        workflow_diagnostics = {**_new_workflow_diagnostics(), **dict(workflow.get("workflow_diagnostics") or {})}
        workflow_diagnostics["status"] = "fallback"
        workflow_diagnostics["used_fallback"] = True
        workflow_diagnostics["failure_reason"] = workflow_diagnostics.get("failure_reason") or "fallback_generated_artifacts"
        _append_unique_message(
            workflow_diagnostics["warnings"],
            "Test-case refinement fallback restored the previous suite because the refinement workflow returned no updated artifacts.",
        )
        workflow = {
            "test_cases": raw_test_cases,
            "requirement_analysis": requirement_analysis,
            "coverage_plan": coverage_plan,
            "review": fallback_review,
            "approved": False,
            "iteration_history": [
                _make_history_entry(
                    iteration=1,
                    actor="FallbackValidation",
                    review=fallback_review,
                    test_cases=raw_test_cases,
                )
            ],
            "coverage_metrics": {
                **_compute_test_case_coverage_metrics(raw_test_cases, payload.requirements),
                **_compute_planned_scenario_metrics(coverage_plan, raw_test_cases, payload.requirements),
                **_compute_requirement_analysis_metrics(requirement_analysis, raw_test_cases, payload.requirements),
                **_compute_grounded_context_metrics(raw_test_cases, payload.context),
            },
            "workflow_settings": resolved_settings,
            "workflow_diagnostics": workflow_diagnostics,
        }

    test_cases = _hydrate_test_cases(raw_test_cases)
    return _build_response(test_cases, workflow, payload.requirements, payload.context)
