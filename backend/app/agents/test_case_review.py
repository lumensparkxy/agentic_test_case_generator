"""Review, scoring, and workflow settings helpers for test-case generation."""

import re
from typing import Any, Dict, List, Optional

from .analysis_agent import fallback_requirement_analysis
from .test_case_coverage import (
    _compute_grounded_context_metrics,
    _compute_planned_scenario_metrics,
    _compute_requirement_analysis_metrics,
    _compute_test_case_coverage_metrics,
    _dedupe_preserve,
    _fallback_coverage_plan,
)
from ..models import Requirement, WorkflowSettings
from plain_english_test_framework.semantic_assertions import is_ambiguous_semantic_visible_text

DEFAULT_TEST_CASE_THRESHOLD = 90
DEFAULT_TEST_CASE_MAX_ITERATIONS = 4
DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT = 2
DEFAULT_TEST_CASE_RETRY_ATTEMPTS = 1

_QUOTED_TEXT_PATTERN = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_VISIBILITY_ASSERTION_PATTERN = re.compile(r"\b(displays?|displayed|visible|shown|present|appears|rendered)\b", re.IGNORECASE)
_SELECTOR_TEXT_PATTERN = re.compile(
    r"^(?:#|\.|\[|css=|xpath=|//|/html\b|[a-z][\w-]*(?:[#.\[]|\[[^\]]+\])|data-testid=|testid=)",
    re.IGNORECASE,
)
_IMPLEMENTATION_ONLY_VISIBLE_TEXT_PATTERN = re.compile(
    r"\b(scopemismatch|typemismatch|syntaxerror|typeerror|referenceerror|exception|stack trace|traceback|stderr|stdout|exit code|cli|command failed)\b",
    re.IGNORECASE,
)
_SYNTHETIC_BROWSER_LABEL_PATTERN = re.compile(
    r"\b(?:link|button|tab|menu item)\s+(?:for|to)\b|(?:link/button|button/link)\s+(?:for|to)\b",
    re.IGNORECASE,
)


def _normalize_review_result(review: Optional[Dict[str, Any]], threshold: int, default_summary: str) -> Dict[str, Any]:
    payload = review or {}
    blocking_issues = _dedupe_preserve(list(payload.get("blocking_issues") or []))
    suggestions = _dedupe_preserve(list(payload.get("suggestions") or []))
    unmet_criteria = _dedupe_preserve(list(payload.get("unmet_criteria") or []))

    try:
        score = int(payload.get("score", 0))
    except (TypeError, ValueError):
        score = 0

    try:
        configured_threshold = int(payload.get("threshold", threshold))
    except (TypeError, ValueError):
        configured_threshold = threshold

    summary = str(payload.get("summary") or default_summary).strip() or default_summary
    score = max(0, min(100, score))
    configured_threshold = max(0, configured_threshold)
    approved = bool(payload.get("approved", False)) and score >= configured_threshold and not blocking_issues

    return {
        "approved": approved,
        "score": score,
        "threshold": configured_threshold,
        "summary": summary,
        "blocking_issues": blocking_issues,
        "suggestions": suggestions,
        "unmet_criteria": unmet_criteria,
    }


def _heuristic_test_case_review(
    test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
    threshold: int,
    coverage_plan: Optional[List[Dict[str, Any]]] = None,
    requirement_analysis: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Any] = None,
) -> Dict[str, Any]:
    metrics = _compute_test_case_coverage_metrics(test_cases, requirements)
    scenario_metrics = _compute_planned_scenario_metrics(
        coverage_plan or _fallback_coverage_plan(requirements),
        test_cases,
        requirements,
    )
    analysis_metrics = _compute_requirement_analysis_metrics(
        requirement_analysis or fallback_requirement_analysis(requirements),
        test_cases,
        requirements,
    )
    grounded_context_metrics = _compute_grounded_context_metrics(test_cases, context)
    blocking_issues: List[str] = []
    suggestions: List[str] = []
    unmet_criteria: List[str] = []
    score = 100

    if metrics["total_test_cases"] == 0:
        blocking_issues.append("No test cases were generated.")
        unmet_criteria.append("Generate at least one executable test case.")
        score = 0
    else:
        if metrics["requirements_without_tests"]:
            blocking_issues.append(f"Requirements without traceable test cases: {', '.join(metrics['requirements_without_tests'])}.")
            unmet_criteria.append("Every requirement must be covered by at least one tagged test case.")
            score -= len(metrics["requirements_without_tests"]) * 8

        missing_descriptions = metrics["total_test_cases"] - metrics["cases_with_descriptions"]
        if missing_descriptions > 0:
            blocking_issues.append(f"{missing_descriptions} test case(s) are missing descriptions.")
            unmet_criteria.append("Every test case needs a meaningful description.")
            score -= missing_descriptions * 5

        missing_expected_results = metrics["total_test_cases"] - metrics["cases_with_expected_results"]
        if missing_expected_results > 0:
            blocking_issues.append(f"{missing_expected_results} test case(s) are missing expected results.")
            unmet_criteria.append("Every test case needs an explicit expected result.")
            score -= missing_expected_results * 6

        if metrics["average_steps_per_case"] < 2:
            suggestions.append("Increase step detail so each case has at least two actionable steps on average.")
            score -= 5

        if metrics["cases_with_preconditions"] < metrics["total_test_cases"]:
            suggestions.append("Add preconditions to more test cases to improve execution readiness.")
            score -= 3

        if scenario_metrics["missing_must_have_scenarios"]:
            preview = ", ".join(scenario_metrics["missing_must_have_scenarios"][:3])
            blocking_issues.append(
                f"Missing must-have planned scenarios: {preview}" + ("." if len(scenario_metrics["missing_must_have_scenarios"]) <= 3 else ", ...")
            )
            unmet_criteria.append("Every must-have scenario in the coverage plan needs at least one corresponding test case.")
            score -= len(scenario_metrics["missing_must_have_scenarios"]) * 7
        elif scenario_metrics["missing_scenarios"]:
            preview = ", ".join(scenario_metrics["missing_scenarios"][:3])
            suggestions.append(f"Add more planned scenario coverage: {preview}" + ("." if len(scenario_metrics["missing_scenarios"]) <= 3 else ", ..."))
            score -= min(12, len(scenario_metrics["missing_scenarios"]) * 2)

        if scenario_metrics.get("scenario_ref_coverage_degraded"):
            fallback_count = int(scenario_metrics.get("scenario_ref_heuristic_fallback_case_count") or 0)
            unknown_count = len(scenario_metrics.get("unknown_scenario_refs") or [])
            issue_parts = []
            if fallback_count:
                issue_parts.append(f"{fallback_count} test case(s) missing planned scenario_refs")
            if unknown_count:
                issue_parts.append(f"{unknown_count} unknown scenario_ref value(s)")
            details = " and ".join(issue_parts) if issue_parts else "missing exact planned scenario_refs"
            suggestions.append(
                f"Coverage review used heuristic scenario-type inference because {details}; emit exact scenario_refs for every planned scenario a case covers."
            )
            unmet_criteria.append("Generated planned-scenario suites should use exact scenario_refs instead of heuristic scenario-type inference.")
            score -= min(10, max(1, fallback_count) * 2 + unknown_count)

        if analysis_metrics["high_risk_items_without_tests"]:
            preview = ", ".join(analysis_metrics["high_risk_items_without_tests"][:3])
            blocking_issues.append(
                "High-risk requirement analysis items without coverage: "
                f"{preview}" + ("." if len(analysis_metrics["high_risk_items_without_tests"]) <= 3 else ", ...")
            )
            unmet_criteria.append("High or critical risks from requirement analysis need corresponding test coverage.")
            score -= len(analysis_metrics["high_risk_items_without_tests"]) * 6

        if analysis_metrics["rules_without_tests"]:
            preview = ", ".join(analysis_metrics["rules_without_tests"][:3])
            suggestions.append(f"Add requirement-rule coverage for: {preview}" + ("." if len(analysis_metrics["rules_without_tests"]) <= 3 else ", ..."))
            score -= min(10, len(analysis_metrics["rules_without_tests"]) * 2)

        if analysis_metrics["constraints_without_tests"]:
            preview = ", ".join(analysis_metrics["constraints_without_tests"][:3])
            suggestions.append(
                f"Add validation or boundary coverage for: {preview}" + ("." if len(analysis_metrics["constraints_without_tests"]) <= 3 else ", ...")
            )
            score -= min(10, len(analysis_metrics["constraints_without_tests"]) * 2)

        if analysis_metrics["role_permissions_without_tests"]:
            preview = ", ".join(analysis_metrics["role_permissions_without_tests"][:3])
            suggestions.append(
                f"Add authorization coverage for: {preview}" + ("." if len(analysis_metrics["role_permissions_without_tests"]) <= 3 else ", ...")
            )
            score -= min(8, len(analysis_metrics["role_permissions_without_tests"]) * 2)

        if analysis_metrics["transitions_without_tests"]:
            preview = ", ".join(analysis_metrics["transitions_without_tests"][:3])
            suggestions.append(f"Add state-transition coverage for: {preview}" + ("." if len(analysis_metrics["transitions_without_tests"]) <= 3 else ", ..."))
            score -= min(10, len(analysis_metrics["transitions_without_tests"]) * 2)

        if grounded_context_metrics["grounded_artifact_count"] > 0:
            if grounded_context_metrics["source_backed_test_cases"] == 0:
                suggestions.append("Grounded context is available, but no test cases cite artifact source references yet.")
                score -= 5
            elif grounded_context_metrics["artifact_reference_coverage_ratio"] < 0.5:
                preview = ", ".join(grounded_context_metrics["unreferenced_artifacts"][:3])
                suggestions.append(
                    "Add broader grounded-context references for artifacts such as: "
                    f"{preview}" + ("." if len(grounded_context_metrics["unreferenced_artifacts"]) <= 3 else ", ...")
                )
                score -= 3

        browser_assertion_review = _review_browser_assertion_grounding(test_cases, context)
        if browser_assertion_review["blocking_issues"]:
            blocking_issues.extend(browser_assertion_review["blocking_issues"])
            unmet_criteria.extend(browser_assertion_review["unmet_criteria"])
            suggestions.extend(browser_assertion_review["suggestions"])
            score -= browser_assertion_review["score_penalty"]

    score = max(0, min(100, score))
    approved = score >= threshold and not blocking_issues
    summary = "Test cases meet the current quality threshold." if approved else "Test cases still need refinement before export is unlocked."

    return {
        "approved": approved,
        "score": score,
        "threshold": threshold,
        "summary": summary,
        "blocking_issues": blocking_issues,
        "suggestions": suggestions,
        "unmet_criteria": unmet_criteria,
    }


def _review_browser_assertion_grounding(test_cases: List[Dict[str, Any]], context: Optional[Any] = None) -> Dict[str, Any]:
    findings: List[str] = []

    for test_case in test_cases:
        case_id = str(test_case.get("id") or test_case.get("title") or "unnamed test case").strip()
        for step_number, action, expected in _iter_review_step_text(test_case):
            step_label = f"{case_id} step {step_number}" if step_number else f"{case_id} step"
            step_text = " ".join(part for part in (action, expected) if part)

            for value in _visible_quoted_values(step_text):
                normalized = _normalize_assertion_text(value)
                if is_ambiguous_semantic_visible_text(normalized):
                    findings.append(f"{step_label}: quoted value `{value}` names a UI role or locator concept instead of exact visible copy.")
                elif _looks_like_selector_text(normalized):
                    findings.append(f"{step_label}: raw selector `{value}` is used as visible page copy.")
                elif _looks_like_implementation_only_visible_text(normalized):
                    findings.append(f"{step_label}: implementation/runtime concept `{value}` is used as browser visible text.")

            if _SYNTHETIC_BROWSER_LABEL_PATTERN.search(step_text):
                findings.append(f"{step_label}: synthetic browser control label is used instead of exact accessible text.")

    findings = _dedupe_preserve(findings)
    if not findings:
        return {"blocking_issues": [], "suggestions": [], "unmet_criteria": [], "score_penalty": 0}

    sample_findings = "; ".join(findings[:4])
    if len(findings) > 4:
        sample_findings += "; ..."

    suggestions = [
        "Replace generic browser assertions with exact accessible names such as "
        'heading "Dashboard" is visible, link "Docs" is visible, direct href validation, or mark the behavior unsupported/manual.'
    ]
    grounded_suggestion = _grounded_browser_assertion_suggestion(context)
    if grounded_suggestion:
        suggestions.append(grounded_suggestion)

    return {
        "blocking_issues": [f"Generic browser assertions are not grounded enough for approval: {sample_findings}"],
        "suggestions": suggestions,
        "unmet_criteria": ["Browser/documentation test cases must use grounded accessible names, real hrefs, or explicit unsupported/manual status."],
        "score_penalty": min(35, 12 + len(findings) * 6),
    }


def _iter_review_step_text(test_case: Dict[str, Any]) -> List[tuple[Any, str, str]]:
    steps = test_case.get("steps") or []
    if isinstance(steps, str):
        return [(1, steps, "")]
    if not isinstance(steps, list):
        return []

    normalized_steps: List[tuple[Any, str, str]] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            normalized_steps.append(
                (
                    step.get("step") or index,
                    str(step.get("action") or "").strip(),
                    str(step.get("expected") or "").strip(),
                )
            )
        else:
            normalized_steps.append((index, str(step or "").strip(), ""))
    return normalized_steps


def _visible_quoted_values(text: str) -> List[str]:
    if not text or not _VISIBILITY_ASSERTION_PATTERN.search(text):
        return []
    values: List[str] = []
    for match in _QUOTED_TEXT_PATTERN.finditer(text):
        value = next((group for group in match.groups() if group), "")
        if value:
            values.append(value.strip())
    return values


def _normalize_assertion_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().strip(" .")


def _looks_like_selector_text(value: str) -> bool:
    return bool(_SELECTOR_TEXT_PATTERN.search(value))


def _looks_like_implementation_only_visible_text(value: str) -> bool:
    return bool(_IMPLEMENTATION_ONLY_VISIBLE_TEXT_PATTERN.search(value))


def _grounded_browser_assertion_suggestion(context: Optional[Any]) -> str | None:
    grounded_context = getattr(context, "grounded_context", None) if context else None
    ui_elements = list(getattr(grounded_context, "ui_elements", []) or [])
    if not ui_elements:
        return None

    def rank(element: Any) -> int:
        element_type = str(getattr(element, "element_type", "") or "").lower()
        if element_type == "heading":
            return 0
        if element_type in {"navigation", "button"}:
            return 1
        return 2

    for element in sorted(ui_elements, key=rank):
        name = str(getattr(element, "name", "") or "").strip()
        if not name:
            continue
        element_type = str(getattr(element, "element_type", "") or "element").lower()
        if element_type == "navigation":
            element_type = "link"
        return f'Grounded context includes `{element_type} "{name}"`; use that exact accessible name when it is the intended assertion.'

    return None


def _merge_review_results(model_review: Optional[Dict[str, Any]], heuristic_review: Dict[str, Any]) -> Dict[str, Any]:
    if not model_review:
        return heuristic_review

    normalized_model = _normalize_review_result(model_review, heuristic_review["threshold"], heuristic_review["summary"])
    combined_blocking = _dedupe_preserve(normalized_model["blocking_issues"] + heuristic_review["blocking_issues"])
    combined_suggestions = _dedupe_preserve(normalized_model["suggestions"] + heuristic_review["suggestions"])
    combined_unmet = _dedupe_preserve(normalized_model["unmet_criteria"] + heuristic_review["unmet_criteria"])
    score = min(normalized_model["score"], heuristic_review["score"])
    threshold = max(normalized_model["threshold"], heuristic_review["threshold"])
    approved = normalized_model["approved"] and heuristic_review["approved"] and not combined_blocking and score >= threshold
    if approved:
        summary = " ".join(_dedupe_preserve([normalized_model["summary"], heuristic_review["summary"]]))
    elif normalized_model["approved"] != heuristic_review["approved"]:
        summary = heuristic_review["summary"] or normalized_model["summary"]
    else:
        summary = " ".join(_dedupe_preserve([heuristic_review["summary"], normalized_model["summary"]]))

    return {
        "approved": approved,
        "score": score,
        "threshold": threshold,
        "summary": summary,
        "blocking_issues": combined_blocking,
        "suggestions": combined_suggestions,
        "unmet_criteria": combined_unmet,
    }


def _make_history_entry(iteration: int, actor: str, review: Dict[str, Any], test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "iteration": iteration,
        "actor": actor,
        "approved": review["approved"],
        "score": review["score"],
        "threshold": review["threshold"],
        "summary": review["summary"],
        "artifact_count": len(test_cases),
        "artifact_ids": [str(item.get("id", "")) for item in test_cases[:8] if item.get("id")],
        "blocking_issues": list(review["blocking_issues"]),
        "suggestions": list(review["suggestions"]),
    }


def _resolve_test_case_workflow_settings(workflow_settings: Optional[WorkflowSettings]) -> Dict[str, Optional[int]]:
    settings = workflow_settings or WorkflowSettings()

    resolved_threshold = int(settings.approval_threshold if settings.approval_threshold is not None else DEFAULT_TEST_CASE_THRESHOLD)
    resolved_max_iterations = int(settings.max_iterations if settings.max_iterations is not None else DEFAULT_TEST_CASE_MAX_ITERATIONS)
    resolved_timeout_seconds = int(settings.timeout_seconds) if settings.timeout_seconds is not None else None
    resolved_stall_iteration_limit = int(
        settings.stall_iteration_limit if settings.stall_iteration_limit is not None else DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT
    )
    resolved_retry_attempts = int(settings.retry_attempts if settings.retry_attempts is not None else DEFAULT_TEST_CASE_RETRY_ATTEMPTS)

    return {
        "approval_threshold": max(0, min(100, resolved_threshold)),
        "max_iterations": max(1, resolved_max_iterations),
        "timeout_seconds": max(1, resolved_timeout_seconds) if resolved_timeout_seconds is not None else None,
        "stall_iteration_limit": max(1, resolved_stall_iteration_limit),
        "retry_attempts": max(0, resolved_retry_attempts),
    }


def _review_is_stalled(previous_review: Optional[Dict[str, Any]], current_review: Dict[str, Any]) -> bool:
    if not previous_review:
        return False

    return (
        current_review["score"] <= previous_review["score"]
        and current_review["blocking_issues"] == previous_review["blocking_issues"]
        and current_review["unmet_criteria"] == previous_review["unmet_criteria"]
    )


def _prefer_review(candidate: Dict[str, Any], incumbent: Optional[Dict[str, Any]]) -> bool:
    if not incumbent:
        return True
    if candidate["approved"] != incumbent["approved"]:
        return candidate["approved"]
    if candidate["score"] != incumbent["score"]:
        return candidate["score"] > incumbent["score"]
    if len(candidate["blocking_issues"]) != len(incumbent["blocking_issues"]):
        return len(candidate["blocking_issues"]) < len(incumbent["blocking_issues"])
    if len(candidate["unmet_criteria"]) != len(incumbent["unmet_criteria"]):
        return len(candidate["unmet_criteria"]) < len(incumbent["unmet_criteria"])
    return False
