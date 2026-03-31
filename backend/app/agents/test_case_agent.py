"""
Test Case Generation Agent - Multi-agent loop using Google ADK.

Implements thresholded validation results, iteration history, and a dedicated
refine-existing-test-cases path so the UI can gate export on explicit approval.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import ValidationError

from ..config import get_settings
from ..models import GenerateTestCasesInput, RefineTestCasesInput, Requirement, RequirementCoveragePlan, ScenarioIntent, TestCase, TestStep
from ..utils.llm_json import parse_coverage_plan_json, parse_review_json, parse_test_cases_json

STATE_TEST_CASES = "current_test_cases"
STATE_VALIDATION_FEEDBACK = "validation_feedback"
STATE_COVERAGE_PLAN = "coverage_plan"

APPROVAL_PHRASE = "APPROVED"
DEFAULT_TEST_CASE_THRESHOLD = 90

ALLOWED_TEST_CASE_TYPES = {
    "Functional",
    "Integration",
    "E2E",
    "Regression",
    "Smoke",
    "Security",
    "Performance",
    "Usability",
    "UAT",
}

TEST_CASE_TYPE_ALIASES = {
    "e2e": "E2E",
    "end to end": "E2E",
    "end-to-end": "E2E",
    "functional": "Functional",
    "integration": "Integration",
    "regression": "Regression",
    "smoke": "Smoke",
    "security": "Security",
    "performance": "Performance",
    "usability": "Usability",
    "uat": "UAT",
    "user acceptance": "UAT",
    "user acceptance testing": "UAT",
}

ALLOWED_PRIORITIES = {"Critical", "High", "Medium", "Low"}
PRIORITY_ALIASES = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "p1": "Critical",
    "p2": "High",
    "p3": "Medium",
    "p4": "Low",
}

ALLOWED_STATUSES = {"Draft", "Ready", "In Review", "Approved", "Deprecated"}
STATUS_ALIASES = {
    "draft": "Draft",
    "ready": "Ready",
    "in review": "In Review",
    "approved": "Approved",
    "deprecated": "Deprecated",
}

ALLOWED_AUTOMATION_STATUSES = {"Manual", "Automated", "To Be Automated"}
AUTOMATION_ALIASES = {
    "manual": "Manual",
    "automated": "Automated",
    "to be automated": "To Be Automated",
    "to_be_automated": "To Be Automated",
    "planned": "To Be Automated",
}

ALLOWED_SCENARIO_TYPES = {
    "Happy Path",
    "Negative",
    "Boundary",
    "Validation",
    "Authorization",
    "State Transition",
    "Integration",
    "Error Handling",
    "Data Variation",
}
SCENARIO_TYPE_ALIASES = {
    "happy": "Happy Path",
    "happy path": "Happy Path",
    "positive": "Happy Path",
    "positive flow": "Happy Path",
    "negative": "Negative",
    "negative path": "Negative",
    "sad path": "Negative",
    "boundary": "Boundary",
    "boundary value": "Boundary",
    "limit": "Boundary",
    "validation": "Validation",
    "input validation": "Validation",
    "authorization": "Authorization",
    "authentication": "Authorization",
    "permission": "Authorization",
    "permissions": "Authorization",
    "role based": "Authorization",
    "state": "State Transition",
    "state transition": "State Transition",
    "workflow": "State Transition",
    "integration": "Integration",
    "api": "Integration",
    "dependency": "Integration",
    "external": "Integration",
    "error": "Error Handling",
    "error handling": "Error Handling",
    "failure": "Error Handling",
    "exception": "Error Handling",
    "data": "Data Variation",
    "data variation": "Data Variation",
}
SCENARIO_KEYWORD_RULES = [
    (("invalid", "required", "format", "blank", "empty", "field", "input"), "Validation"),
    (("min", "max", "limit", "length", "range", "threshold", "boundary"), "Boundary"),
    (("login", "auth", "permission", "role", "access", "admin", "user"), "Authorization"),
    (("status", "state", "workflow", "approve", "reject", "submit", "cancel", "transition"), "State Transition"),
    (("api", "integration", "service", "email", "payment", "upload", "download", "import", "export", "webhook"), "Integration"),
    (("error", "failure", "timeout", "unavailable", "retry", "exception"), "Error Handling"),
    (("search", "sort", "filter", "duplicate", "record", "dataset", "data"), "Data Variation"),
]


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen: set[str] = set()
    unique: List[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _normalize_test_case_type(raw_type: Any) -> str:
    if not raw_type:
        return "Functional"

    raw = str(raw_type).strip()
    if raw in ALLOWED_TEST_CASE_TYPES:
        return raw

    normalized_key = " ".join(raw.replace("_", " ").replace("-", " ").split()).lower()
    mapped = TEST_CASE_TYPE_ALIASES.get(normalized_key)
    if mapped:
        return mapped

    title_case = raw.title()
    if title_case in ALLOWED_TEST_CASE_TYPES:
        return title_case

    logging.warning("[TestCase Workflow] Unknown test case type '%s', defaulting to Functional", raw)
    return "Functional"


def _normalize_priority(raw_priority: Any) -> str:
    if not raw_priority:
        return "Medium"
    raw = str(raw_priority).strip()
    if raw in ALLOWED_PRIORITIES:
        return raw
    return PRIORITY_ALIASES.get(raw.lower(), "Medium")


def _normalize_status(raw_status: Any) -> str:
    if not raw_status:
        return "Draft"
    raw = str(raw_status).strip()
    if raw in ALLOWED_STATUSES:
        return raw
    return STATUS_ALIASES.get(raw.lower(), "Draft")


def _normalize_automation_status(raw_status: Any) -> str:
    if not raw_status:
        return "Manual"
    raw = str(raw_status).strip()
    if raw in ALLOWED_AUTOMATION_STATUSES:
        return raw
    return AUTOMATION_ALIASES.get(raw.lower(), "Manual")


def _normalize_scenario_type(raw_type: Any) -> str:
    if not raw_type:
        return "Happy Path"

    raw = str(raw_type).strip()
    if raw in ALLOWED_SCENARIO_TYPES:
        return raw

    normalized_key = " ".join(raw.replace("_", " ").replace("-", " ").split()).lower()
    mapped = SCENARIO_TYPE_ALIASES.get(normalized_key)
    if mapped:
        return mapped

    title_case = raw.title()
    if title_case in ALLOWED_SCENARIO_TYPES:
        return title_case

    logging.warning("[TestCase Workflow] Unknown scenario type '%s', defaulting to Happy Path", raw)
    return "Happy Path"


def _scenario_tag(scenario_type: str) -> str:
    normalized = _normalize_scenario_type(scenario_type)
    return f"scenario:{normalized.lower().replace(' ', '-')}"


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "1", "required", "must", "y"}:
        return True
    if normalized in {"false", "no", "0", "optional", "n"}:
        return False
    return default


def _build_default_scenario(requirement: Requirement, scenario_type: str, index: int, *, must_have: bool = True) -> Dict[str, Any]:
    normalized_type = _normalize_scenario_type(scenario_type)
    return {
        "id": f"{requirement.id}-SCN-{index:02d}",
        "requirement_id": requirement.id,
        "scenario_type": normalized_type,
        "title": f"{normalized_type} coverage for {requirement.id}",
        "objective": f"Validate requirement {requirement.id} ({requirement.text}) under {normalized_type.lower()} conditions.",
        "priority": "High" if must_have else "Medium",
        "must_have": must_have,
    }


def _default_scenarios_for_requirement(requirement: Requirement) -> List[Dict[str, Any]]:
    text = requirement.text.lower()
    scenario_types: List[str] = ["Happy Path", "Negative"]

    for keywords, scenario_type in SCENARIO_KEYWORD_RULES:
        if any(keyword in text for keyword in keywords) and scenario_type not in scenario_types:
            scenario_types.append(scenario_type)
        if len(scenario_types) >= 4:
            break

    if "Validation" not in scenario_types and any(keyword in text for keyword in ("field", "input", "form", "validate", "value")):
        scenario_types.append("Validation")
    elif len(scenario_types) < 3:
        scenario_types.append("Validation")

    defaults: List[Dict[str, Any]] = []
    for index, scenario_type in enumerate(_dedupe_preserve(scenario_types)[:4], start=1):
        defaults.append(
            _build_default_scenario(
                requirement,
                scenario_type,
                index,
                must_have=index <= 2 or scenario_type in {"Validation", "Authorization", "State Transition", "Integration"},
            )
        )
    return defaults


def _normalize_coverage_plan(raw_plan: List[Dict[str, Any]], requirements: List[Requirement]) -> List[Dict[str, Any]]:
    requirement_lookup = {requirement.id: requirement for requirement in requirements}
    normalized_by_requirement: Dict[str, Dict[str, Any]] = {}

    for raw_item in raw_plan or []:
        requirement_id = str(raw_item.get("requirement_id") or "").strip()
        if requirement_id not in requirement_lookup:
            continue

        requirement = requirement_lookup[requirement_id]
        normalized_scenarios: List[Dict[str, Any]] = []
        seen_scenario_types: set[str] = set()

        for raw_scenario in raw_item.get("scenarios") or []:
            if not isinstance(raw_scenario, dict):
                continue

            scenario_type = _normalize_scenario_type(raw_scenario.get("scenario_type") or raw_scenario.get("type"))
            if scenario_type in seen_scenario_types:
                continue

            title = str(raw_scenario.get("title") or f"{scenario_type} coverage for {requirement_id}").strip()
            objective = str(
                raw_scenario.get("objective")
                or raw_scenario.get("description")
                or f"Validate requirement {requirement_id} under {scenario_type.lower()} conditions."
            ).strip()
            if not title or not objective:
                continue

            normalized_scenarios.append(
                {
                    "id": str(raw_scenario.get("id") or f"{requirement_id}-SCN-{len(normalized_scenarios) + 1:02d}"),
                    "requirement_id": requirement_id,
                    "scenario_type": scenario_type,
                    "title": title,
                    "objective": objective,
                    "priority": _normalize_priority(raw_scenario.get("priority")),
                    "must_have": _coerce_bool(raw_scenario.get("must_have"), default=True),
                }
            )
            seen_scenario_types.add(scenario_type)

        normalized_by_requirement[requirement_id] = {
            "requirement_id": requirement_id,
            "requirement_text": str(raw_item.get("requirement_text") or requirement.text).strip() or requirement.text,
            "scenarios": normalized_scenarios,
        }

    normalized_plan: List[Dict[str, Any]] = []
    for requirement in requirements:
        existing = normalized_by_requirement.get(requirement.id)
        if not existing:
            normalized_plan.append(
                {
                    "requirement_id": requirement.id,
                    "requirement_text": requirement.text,
                    "scenarios": _default_scenarios_for_requirement(requirement),
                }
            )
            continue

        existing_types = {scenario["scenario_type"] for scenario in existing["scenarios"]}
        for default_scenario in _default_scenarios_for_requirement(requirement):
            if default_scenario["scenario_type"] in existing_types or len(existing["scenarios"]) >= 4:
                continue
            existing["scenarios"].append(default_scenario)
            existing_types.add(default_scenario["scenario_type"])

        if not existing["scenarios"]:
            existing["scenarios"] = _default_scenarios_for_requirement(requirement)

        normalized_plan.append(existing)

    return normalized_plan


def _fallback_coverage_plan(requirements: List[Requirement]) -> List[Dict[str, Any]]:
    return [
        {
            "requirement_id": requirement.id,
            "requirement_text": requirement.text,
            "scenarios": _default_scenarios_for_requirement(requirement),
        }
        for requirement in requirements
    ]


def _extract_scenario_types_from_test_case(test_case: Dict[str, Any]) -> List[str]:
    tags = test_case.get("tags") or []
    extracted: List[str] = []

    for tag in tags:
        normalized_tag = str(tag).strip()
        if not normalized_tag.lower().startswith("scenario:"):
            continue
        extracted.append(_normalize_scenario_type(normalized_tag.split(":", 1)[1]))

    if extracted:
        return _dedupe_preserve(extracted)

    steps = test_case.get("steps") or []
    step_text = " ".join(
        f"{step.get('action', '')} {step.get('expected', '')}"
        for step in steps
        if isinstance(step, dict)
    )
    combined_text = " ".join(
        [
            str(test_case.get("title") or ""),
            str(test_case.get("description") or ""),
            str(test_case.get("expected_result") or ""),
            step_text,
        ]
    ).lower()

    for keywords, scenario_type in SCENARIO_KEYWORD_RULES:
        if any(keyword in combined_text for keyword in keywords):
            extracted.append(scenario_type)

    if not extracted:
        extracted.append("Happy Path")

    return _dedupe_preserve(extracted)


def _compute_planned_scenario_metrics(
    coverage_plan: List[Dict[str, Any]],
    test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
) -> Dict[str, Any]:
    requirement_ids = _serialize_requirement_ids(requirements)
    requirement_id_set = set(requirement_ids)
    scenarios_covered_by_requirement: Dict[str, set[str]] = {requirement_id: set() for requirement_id in requirement_ids}

    for test_case in test_cases:
        tags = test_case.get("tags") or []
        linked_requirements = {str(tag).strip() for tag in tags if str(tag).strip() in requirement_id_set}
        if not linked_requirements:
            continue

        scenario_types = _extract_scenario_types_from_test_case(test_case)
        for requirement_id in linked_requirements:
            scenarios_covered_by_requirement[requirement_id].update(scenario_types)

    planned_total = 0
    covered_total = 0
    must_have_total = 0
    must_have_covered = 0
    missing_scenarios: List[str] = []
    missing_must_have_scenarios: List[str] = []
    requirement_summary: Dict[str, Dict[str, Any]] = {}

    for plan_item in coverage_plan:
        requirement_id = str(plan_item.get("requirement_id") or "").strip()
        planned_scenarios = []
        covered_scenarios = []
        missing_scenario_types = []
        matched_scenarios = scenarios_covered_by_requirement.get(requirement_id, set())

        for scenario in plan_item.get("scenarios") or []:
            scenario_type = _normalize_scenario_type(scenario.get("scenario_type"))
            planned_total += 1
            planned_scenarios.append(scenario_type)

            is_must_have = _coerce_bool(scenario.get("must_have"), default=True)
            if is_must_have:
                must_have_total += 1

            if scenario_type in matched_scenarios:
                covered_total += 1
                covered_scenarios.append(scenario_type)
                if is_must_have:
                    must_have_covered += 1
                continue

            label = f"{requirement_id} - {scenario_type}: {scenario.get('title') or scenario_type}"
            missing_scenarios.append(label)
            missing_scenario_types.append(scenario_type)
            if is_must_have:
                missing_must_have_scenarios.append(label)

        requirement_summary[requirement_id] = {
            "planned_scenarios": len(_dedupe_preserve(planned_scenarios)),
            "covered_scenarios": len(_dedupe_preserve(covered_scenarios)),
            "missing_scenario_types": _dedupe_preserve(missing_scenario_types),
            "covered_scenario_types": _dedupe_preserve(covered_scenarios),
        }

    return {
        "planned_scenarios_total": planned_total,
        "covered_planned_scenarios": covered_total,
        "scenario_coverage_ratio": round(covered_total / planned_total, 2) if planned_total else 1.0,
        "must_have_scenarios_total": must_have_total,
        "covered_must_have_scenarios": must_have_covered,
        "must_have_scenario_coverage_ratio": round(must_have_covered / must_have_total, 2) if must_have_total else 1.0,
        "missing_scenarios": missing_scenarios,
        "missing_must_have_scenarios": missing_must_have_scenarios,
        "requirement_scenario_summary": requirement_summary,
    }


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


def _serialize_requirement_ids(requirements: List[Requirement]) -> List[str]:
    return [req.id for req in requirements if req.id]


def _compute_test_case_coverage_metrics(test_cases: List[Dict[str, Any]], requirements: List[Requirement]) -> Dict[str, Any]:
    total = len(test_cases)
    requirement_ids = _serialize_requirement_ids(requirements)
    requirement_id_set = set(requirement_ids)
    traceability_counts = {req_id: 0 for req_id in requirement_ids}

    cases_with_descriptions = 0
    cases_with_expected_results = 0
    cases_with_preconditions = 0
    cases_with_traceability = 0
    steps_total = 0

    for test_case in test_cases:
        if str(test_case.get("description") or "").strip():
            cases_with_descriptions += 1
        if str(test_case.get("expected_result") or "").strip():
            cases_with_expected_results += 1
        if str(test_case.get("preconditions") or "").strip():
            cases_with_preconditions += 1

        steps = test_case.get("steps") or []
        if isinstance(steps, list):
            steps_total += len(steps)

        tags = test_case.get("tags") or []
        tagged_ids = {str(tag).strip() for tag in tags if str(tag).strip() in requirement_id_set}
        if tagged_ids:
            cases_with_traceability += 1
            for tagged_id in tagged_ids:
                traceability_counts[tagged_id] += 1

    covered_requirements = [req_id for req_id, count in traceability_counts.items() if count > 0]
    uncovered_requirements = [req_id for req_id, count in traceability_counts.items() if count == 0]
    average_steps_per_case = round(steps_total / total, 2) if total else 0.0

    return {
        "total_test_cases": total,
        "requirements_total": len(requirement_ids),
        "requirements_covered": len(covered_requirements),
        "traceability_coverage_ratio": round(len(covered_requirements) / len(requirement_ids), 2) if requirement_ids else 1.0,
        "requirements_without_tests": uncovered_requirements,
        "cases_with_descriptions": cases_with_descriptions,
        "cases_with_expected_results": cases_with_expected_results,
        "cases_with_preconditions": cases_with_preconditions,
        "cases_with_traceability": cases_with_traceability,
        "average_steps_per_case": average_steps_per_case,
        "test_cases_per_requirement": traceability_counts,
    }


def _heuristic_test_case_review(
    test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
    threshold: int,
    coverage_plan: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    metrics = _compute_test_case_coverage_metrics(test_cases, requirements)
    scenario_metrics = _compute_planned_scenario_metrics(
        coverage_plan or _fallback_coverage_plan(requirements),
        test_cases,
        requirements,
    )
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
            blocking_issues.append(
                f"Requirements without traceable test cases: {', '.join(metrics['requirements_without_tests'])}."
            )
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
                "Missing must-have planned scenarios: "
                f"{preview}"
                + ("." if len(scenario_metrics["missing_must_have_scenarios"]) <= 3 else ", ...")
            )
            unmet_criteria.append("Every must-have scenario in the coverage plan needs at least one corresponding test case.")
            score -= len(scenario_metrics["missing_must_have_scenarios"]) * 7
        elif scenario_metrics["missing_scenarios"]:
            preview = ", ".join(scenario_metrics["missing_scenarios"][:3])
            suggestions.append(
                "Add more planned scenario coverage: "
                f"{preview}"
                + ("." if len(scenario_metrics["missing_scenarios"]) <= 3 else ", ...")
            )
            score -= min(12, len(scenario_metrics["missing_scenarios"]) * 2)

    score = max(0, min(100, score))
    approved = score >= threshold and not blocking_issues
    summary = (
        "Test cases meet the current quality threshold."
        if approved
        else "Test cases still need refinement before export is unlocked."
    )

    return {
        "approved": approved,
        "score": score,
        "threshold": threshold,
        "summary": summary,
        "blocking_issues": blocking_issues,
        "suggestions": suggestions,
        "unmet_criteria": unmet_criteria,
    }


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
    summary = " ".join(_dedupe_preserve([normalized_model["summary"], heuristic_review["summary"]]))

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
    feedback_section = ""
    if human_feedback:
        feedback_section = f"""
**Human Feedback to Consider:**
{human_feedback}
"""

    return Agent(
        name="CoveragePlannerAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a Senior QA Strategist creating a scenario coverage plan before detailed test cases are written.

**Requirements:**
{requirements_text}

**Context:**
{context_text}
{feedback_section}
**Rules:**
1. Produce 2-4 scenarios per requirement.
2. Always include a 'Happy Path' scenario for every requirement.
3. Include at least one non-happy-path scenario per requirement.
4. Use ONLY these scenario types: Happy Path, Negative, Boundary, Validation, Authorization, State Transition, Integration, Error Handling, Data Variation.
5. Mark essential scenarios with must_have=true.
6. Output ONLY a JSON object shaped like:
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
    feedback_section = ""
    if human_feedback:
        feedback_section = f"""
**Human Feedback That Must Be Honored:**
{human_feedback}
"""

    validator_agent = Agent(
        name="TestCaseValidatorAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a QA Lead reviewing test cases for quality, completeness, and traceability.

**Current Test Cases:**
```
{{{STATE_TEST_CASES}}}
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
    3. Requirement traceability tags cover every requirement.
    4. Every must-have scenario from the coverage plan is represented by at least one test case.
    5. Tags include a scenario marker formatted like scenario:happy-path or scenario:negative.
    6. Priority, type, status, and automation status are valid.
    7. Test data, preconditions, and overall expected_result are present when needed.
    8. Human feedback has been addressed.

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
        include_contents='none',
        instruction=f"""You are a QA Engineer refining test cases.

**Current Test Cases:**
```
{{{STATE_TEST_CASES}}}
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
3. Output ONLY a JSON object with the shape {{"test_cases": [...]}}.

Either call exit_loop OR output the refined JSON object. Never add commentary.
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


def _build_generation_pipeline(
    model: str,
    requirements_text: str,
    context_text: str,
    template_text: str,
    threshold: int,
    human_feedback: Optional[str] = None,
) -> Agent:
    feedback_section = ""
    if human_feedback:
        feedback_section = f"""
**Human Feedback to Address:**
{human_feedback}
"""

    generator_agent = Agent(
        name="TestCaseGeneratorAgent",
        model=model,
        include_contents='default',
        instruction=f"""You are a Senior QA Engineer specializing in detailed, execution-ready test design.

**Requirements to Test:**
{requirements_text}

**Context:**
{context_text}

**Template Configuration:**
{template_text}

**Coverage Plan To Implement:**
{{{STATE_COVERAGE_PLAN}}}
{feedback_section}
**Rules:**
1. Generate 1-3 test cases per requirement.
2. Implement every must-have planned scenario and as many recommended scenarios as possible.
3. Tag each test case with at least one requirement ID and one scenario tag using the format scenario:<kebab-case-scenario-type>.
4. Include detailed steps, expected results, realistic priorities, and execution metadata.
5. Keep each test case centered on one primary scenario from the coverage plan.
6. Output ONLY a JSON object shaped like {{"test_cases": [...]}}.
""",
        description="Generates initial test cases from approved requirements",
        output_key=STATE_TEST_CASES,
    )

    return SequentialAgent(
        name="TestCaseGenerationPipeline",
        sub_agents=[
            _build_coverage_planner_agent(model, requirements_text, context_text, human_feedback=human_feedback),
            generator_agent,
            _build_review_loop(model, threshold, 4, requirements_text, human_feedback=human_feedback),
        ],
        description="Generates and iteratively validates test cases from requirements",
    )


def _build_refinement_pipeline(
    model: str,
    requirements_text: str,
    context_text: str,
    template_text: str,
    threshold: int,
    human_feedback: str,
) -> Agent:
    refinement_agent = Agent(
        name="TestCaseRefinementAgent",
        model=model,
        include_contents='default',
        instruction=f"""You are a Senior QA Engineer refining an existing test suite.

Use the existing test cases, requirements, context, template, and human feedback in the user message to produce an improved JSON object shaped like {{"test_cases": [...]}}.

Requirements reference:
{requirements_text}

Context:
{context_text}

Template:
{template_text}

Coverage plan:
{{{STATE_COVERAGE_PLAN}}}

Rules:
1. Preserve good test cases and improve weak ones.
2. Add, merge, split, or remove cases as needed.
3. Keep requirement traceability intact or improve it.
4. Ensure each test case includes a scenario tag formatted like scenario:happy-path.
5. Output ONLY the JSON object.
""",
        description="Applies human feedback to an existing test-case set before re-validation",
        output_key=STATE_TEST_CASES,
    )

    return SequentialAgent(
        name="TestCaseRefinementPipeline",
        sub_agents=[
            _build_coverage_planner_agent(model, requirements_text, context_text, human_feedback=human_feedback),
            refinement_agent,
            _build_review_loop(model, threshold, 4, requirements_text, human_feedback=human_feedback),
        ],
        description="Refines an existing test-case set and re-validates it against the approval threshold",
    )


async def _run_test_case_workflow_async(
    *,
    requirements: List[Requirement],
    requirements_text: str,
    context_text: str,
    template_text: str,
    model: str,
    human_feedback: Optional[str] = None,
    existing_test_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    threshold = DEFAULT_TEST_CASE_THRESHOLD
    is_refinement = existing_test_cases is not None

    if is_refinement:
        root_agent = _build_refinement_pipeline(
            model,
            requirements_text,
            context_text,
            template_text,
            threshold,
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
{human_feedback or 'No human feedback provided.'}
"""
    else:
        root_agent = _build_generation_pipeline(
            model,
            requirements_text,
            context_text,
            template_text,
            threshold,
            human_feedback=human_feedback,
        )
        message_text = "Generate and validate comprehensive test cases from the approved requirements."

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="test_case_generator",
        session_service=session_service,
    )

    user_id = f"user_{uuid.uuid4().hex[:8]}"
    session = await session_service.create_session(
        app_name="test_case_generator",
        user_id=user_id,
        state={
            STATE_TEST_CASES: "[]",
            STATE_VALIDATION_FEEDBACK: "",
            STATE_COVERAGE_PLAN: "[]",
        },
    )

    current_test_cases: List[Dict[str, Any]] = []
    current_coverage_plan: List[Dict[str, Any]] = []
    iteration_history: List[Dict[str, Any]] = []
    model_review: Optional[Dict[str, Any]] = None

    logging.info("[TestCase Workflow] Starting session %s", session.id)

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message_text)]),
    ):
        author = getattr(event, 'author', 'unknown')
        logging.info("[TestCase Workflow] Event from %s", author)

        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            text = getattr(part, 'text', None)
            if not text:
                continue

            parsed_test_cases = parse_test_cases_json(text)
            if parsed_test_cases and author in {"TestCaseGeneratorAgent", "TestCaseRefinementAgent", "TestCaseRefinerAgent"}:
                current_test_cases = parsed_test_cases

            parsed_coverage_plan = parse_coverage_plan_json(text)
            if parsed_coverage_plan and author == "CoveragePlannerAgent":
                current_coverage_plan = _normalize_coverage_plan(parsed_coverage_plan, requirements)

            parsed_review = parse_review_json(text, default_threshold=threshold)
            if parsed_review and author == "TestCaseValidatorAgent":
                model_review = _normalize_review_result(parsed_review, threshold, "Test case validation completed.")
                iteration_history.append(
                    _make_history_entry(
                        iteration=len(iteration_history) + 1,
                        actor=author,
                        review=model_review,
                        test_cases=current_test_cases,
                    )
                )

    state_test_cases = parse_test_cases_json(session.state.get(STATE_TEST_CASES, "[]"))
    if state_test_cases:
        current_test_cases = state_test_cases

    state_coverage_plan = parse_coverage_plan_json(session.state.get(STATE_COVERAGE_PLAN, "[]"))
    if state_coverage_plan:
        current_coverage_plan = _normalize_coverage_plan(state_coverage_plan, requirements)

    if not current_coverage_plan:
        current_coverage_plan = _fallback_coverage_plan(requirements)

    state_review = parse_review_json(session.state.get(STATE_VALIDATION_FEEDBACK, ""), default_threshold=threshold)
    if state_review:
        model_review = _normalize_review_result(state_review, threshold, "Test case validation completed.")

    heuristic_review = _heuristic_test_case_review(
        current_test_cases,
        requirements,
        threshold,
        coverage_plan=current_coverage_plan,
    )
    final_review = _merge_review_results(model_review, heuristic_review)
    coverage_metrics = _compute_test_case_coverage_metrics(current_test_cases, requirements)
    coverage_metrics.update(_compute_planned_scenario_metrics(current_coverage_plan, current_test_cases, requirements))

    if iteration_history:
        iteration_history[-1] = _make_history_entry(
            iteration=iteration_history[-1]["iteration"],
            actor=iteration_history[-1]["actor"],
            review=final_review,
            test_cases=current_test_cases,
        )
    else:
        iteration_history.append(
            _make_history_entry(
                iteration=1,
                actor="HeuristicValidation",
                review=final_review,
                test_cases=current_test_cases,
            )
        )

    logging.info("[TestCase Workflow] Final test cases: %s, approved=%s", len(current_test_cases), final_review["approved"])
    return {
        "test_cases": current_test_cases,
        "coverage_plan": current_coverage_plan,
        "approved": final_review["approved"],
        "review": final_review,
        "iteration_history": iteration_history,
        "coverage_metrics": coverage_metrics,
    }


def _run_workflow_sync(**kwargs: Any) -> Dict[str, Any]:
    try:
        try:
            asyncio.get_running_loop()
            import nest_asyncio

            nest_asyncio.apply()
            return asyncio.run(_run_test_case_workflow_async(**kwargs))
        except RuntimeError:
            return asyncio.run(_run_test_case_workflow_async(**kwargs))
    except Exception as exc:
        logging.error("[TestCase Workflow] Error: %s", exc)
        requirements = kwargs.get("requirements") or []
        fallback_plan = _fallback_coverage_plan(requirements)
        return {
            "test_cases": [],
            "coverage_plan": fallback_plan,
            "approved": False,
            "review": _heuristic_test_case_review([], requirements, DEFAULT_TEST_CASE_THRESHOLD, coverage_plan=fallback_plan),
            "iteration_history": [],
            "coverage_metrics": {
                **_compute_test_case_coverage_metrics([], requirements),
                **_compute_planned_scenario_metrics(fallback_plan, [], requirements),
            },
        }


def _prepare_workflow_inputs(requirements: List[Requirement], context: Optional[Any], template: Any) -> tuple[str, str, str]:
    requirements_text = "\n".join([f"- {req.id}: {req.text}" for req in requirements])

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
    context_text = "\n".join(context_parts) if context_parts else "No additional context provided."

    template_text = f"Name: {template.name}, Format: {template.format}, Fields: {', '.join(template.fields)}"
    return requirements_text, context_text, template_text


def _fallback_raw_test_cases(
    requirements: List[Requirement],
    context: Optional[Any],
    coverage_plan: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raw_test_cases: List[Dict[str, Any]] = []
    requirements_by_id = {requirement.id: requirement for requirement in requirements}
    normalized_plan = _normalize_coverage_plan(coverage_plan or _fallback_coverage_plan(requirements), requirements)

    for idx, plan_item in enumerate(normalized_plan, start=1):
        requirement = requirements_by_id.get(str(plan_item.get("requirement_id") or "").strip())
        if not requirement:
            continue

        planned_scenarios = list(plan_item.get("scenarios") or [])
        selected_scenarios = [scenario for scenario in planned_scenarios if _coerce_bool(scenario.get("must_have"), default=True)]
        if not selected_scenarios:
            selected_scenarios = planned_scenarios[:1]

        for scenario_offset, scenario in enumerate(selected_scenarios[:2], start=1):
            scenario_type = _normalize_scenario_type(scenario.get("scenario_type"))
            raw_test_cases.append(
                {
                    "id": f"TC-{len(raw_test_cases) + 1:03d}",
                    "title": str(scenario.get("title") or f"{scenario_type} validation for {requirement.id}"),
                    "description": str(scenario.get("objective") or f"Verify that {requirement.text[:100]}"),
                    "priority": _normalize_priority(scenario.get("priority")),
                    "type": "Functional",
                    "status": "Draft",
                    "preconditions": context.notes if context else None,
                    "steps": [
                        {
                            "step": 1,
                            "action": f"Navigate to the feature area that implements {requirement.id}",
                            "expected": "The relevant screen, API, or control is available for testing.",
                            "test_data": None,
                        },
                        {
                            "step": 2,
                            "action": f"Execute the {scenario_type.lower()} scenario for {requirement.id}",
                            "expected": str(scenario.get("objective") or f"Requirement {requirement.id} behaves as expected."),
                            "test_data": None,
                        },
                        {
                            "step": 3,
                            "action": "Observe system behavior and capture the outcome.",
                            "expected": f"The outcome matches the expected {scenario_type.lower()} behavior for {requirement.id}.",
                            "test_data": None,
                        },
                    ],
                    "expected_result": f"Requirement {requirement.id} is satisfied for the planned {scenario_type.lower()} scenario.",
                    "test_data": None,
                    "estimated_time": "5 mins",
                    "automation_status": "To Be Automated",
                    "component": "General",
                    "tags": [requirement.id, _scenario_tag(scenario_type), "generated", f"plan:{idx:02d}-{scenario_offset:02d}"],
                }
            )
    return raw_test_cases


def _hydrate_test_cases(raw_test_cases: List[Dict[str, Any]]) -> List[TestCase]:
    test_cases: List[TestCase] = []
    for index, raw_test_case in enumerate(raw_test_cases, start=1):
        try:
            steps = []
            for raw_step in raw_test_case.get("steps", []) or []:
                steps.append(
                    TestStep(
                        step=raw_step.get("step", len(steps) + 1),
                        action=raw_step.get("action", ""),
                        expected=raw_step.get("expected", ""),
                        test_data=raw_step.get("test_data"),
                    )
                )

            test_cases.append(
                TestCase(
                    id=raw_test_case.get("id", f"TC-{index:03d}"),
                    title=raw_test_case.get("title", "Untitled Test Case"),
                    description=raw_test_case.get("description"),
                    priority=_normalize_priority(raw_test_case.get("priority")),
                    type=_normalize_test_case_type(raw_test_case.get("type")),
                    status=_normalize_status(raw_test_case.get("status")),
                    preconditions=raw_test_case.get("preconditions"),
                    steps=steps,
                    expected_result=raw_test_case.get("expected_result"),
                    test_data=raw_test_case.get("test_data"),
                    estimated_time=raw_test_case.get("estimated_time"),
                    automation_status=_normalize_automation_status(raw_test_case.get("automation_status")),
                    component=raw_test_case.get("component"),
                    tags=raw_test_case.get("tags", []),
                )
            )
        except (ValidationError, KeyError) as exc:
            logging.warning("[TestCase Workflow] Skipping invalid test case: %s", exc)
            continue
    return test_cases


def _serialize_test_cases(test_cases: List[TestCase]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for test_case in test_cases:
        serialized.append(
            {
                "id": test_case.id,
                "title": test_case.title,
                "description": test_case.description,
                "priority": test_case.priority,
                "type": test_case.type,
                "status": test_case.status,
                "preconditions": test_case.preconditions,
                "steps": [
                    {
                        "step": step.step,
                        "action": step.action,
                        "expected": step.expected,
                        "test_data": step.test_data,
                    }
                    for step in test_case.steps
                ],
                "expected_result": test_case.expected_result,
                "test_data": test_case.test_data,
                "estimated_time": test_case.estimated_time,
                "automation_status": test_case.automation_status,
                "component": test_case.component,
                "tags": test_case.tags or [],
            }
        )
    return serialized


def _hydrate_coverage_plan(
    raw_coverage_plan: List[Dict[str, Any]],
    requirements: List[Requirement],
) -> List[RequirementCoveragePlan]:
    normalized_plan = _normalize_coverage_plan(raw_coverage_plan or _fallback_coverage_plan(requirements), requirements)
    hydrated: List[RequirementCoveragePlan] = []

    for plan_item in normalized_plan:
        scenarios = [
            ScenarioIntent(
                id=str(scenario.get("id") or ""),
                requirement_id=str(plan_item.get("requirement_id") or ""),
                scenario_type=_normalize_scenario_type(scenario.get("scenario_type")),
                title=str(scenario.get("title") or "Untitled scenario"),
                objective=str(scenario.get("objective") or scenario.get("title") or ""),
                priority=_normalize_priority(scenario.get("priority")),
                must_have=_coerce_bool(scenario.get("must_have"), default=True),
            )
            for scenario in plan_item.get("scenarios") or []
        ]
        hydrated.append(
            RequirementCoveragePlan(
                requirement_id=str(plan_item.get("requirement_id") or ""),
                requirement_text=str(plan_item.get("requirement_text") or ""),
                scenarios=scenarios,
            )
        )

    return hydrated


def _build_response(test_cases: List[TestCase], workflow: Dict[str, Any], requirements: List[Requirement]) -> Dict[str, Any]:
    serialized = _serialize_test_cases(test_cases)
    raw_coverage_plan = list(workflow.get("coverage_plan") or _fallback_coverage_plan(requirements))
    normalized_coverage_plan = _normalize_coverage_plan(raw_coverage_plan, requirements)
    default_coverage_metrics = _compute_test_case_coverage_metrics(serialized, requirements)
    default_coverage_metrics.update(_compute_planned_scenario_metrics(normalized_coverage_plan, serialized, requirements))
    coverage_metrics = dict(workflow.get("coverage_metrics") or default_coverage_metrics)
    review = dict(
        workflow.get("review")
        or _heuristic_test_case_review(
            serialized,
            requirements,
            DEFAULT_TEST_CASE_THRESHOLD,
            coverage_plan=normalized_coverage_plan,
        )
    )
    approved = bool(workflow.get("approved", False))
    coverage_plan = _hydrate_coverage_plan(normalized_coverage_plan, requirements)

    return {
        "test_cases": test_cases,
        "approved": approved,
        "review": review,
        "iteration_history": list(workflow.get("iteration_history") or []),
        "coverage_plan": coverage_plan,
        "coverage_metrics": coverage_metrics,
    }


def generate_test_cases(payload: GenerateTestCasesInput) -> Dict[str, Any]:
    settings = get_settings()
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)

    workflow = _run_workflow_sync(
        requirements=payload.requirements,
        requirements_text=requirements_text,
        context_text=context_text,
        template_text=template_text,
        model=settings.model_name,
        human_feedback=payload.feedback if payload.feedback else None,
        existing_test_cases=None,
    )

    raw_test_cases = workflow.get("test_cases", [])
    coverage_plan = list(workflow.get("coverage_plan") or _fallback_coverage_plan(payload.requirements))
    if not raw_test_cases:
        logging.warning("[TestCase Workflow] No test cases from pipeline, using deterministic fallback")
        raw_test_cases = _fallback_raw_test_cases(payload.requirements, payload.context, coverage_plan=coverage_plan)
        fallback_review = _heuristic_test_case_review(
            raw_test_cases,
            payload.requirements,
            DEFAULT_TEST_CASE_THRESHOLD,
            coverage_plan=coverage_plan,
        )
        workflow = {
            "test_cases": raw_test_cases,
            "coverage_plan": coverage_plan,
            "review": fallback_review,
            "approved": fallback_review["approved"],
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
            },
        }

    test_cases = _hydrate_test_cases(raw_test_cases)
    return _build_response(test_cases, workflow, payload.requirements)


def refine_test_cases(payload: RefineTestCasesInput) -> Dict[str, Any]:
    settings = get_settings()
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)

    existing_test_cases = _serialize_test_cases(payload.test_cases)
    workflow = _run_workflow_sync(
        requirements=payload.requirements,
        requirements_text=requirements_text,
        context_text=context_text,
        template_text=template_text,
        model=settings.model_name,
        human_feedback=payload.feedback,
        existing_test_cases=existing_test_cases,
    )

    raw_test_cases = workflow.get("test_cases", []) or existing_test_cases
    coverage_plan = list(workflow.get("coverage_plan") or _fallback_coverage_plan(payload.requirements))
    if not workflow.get("test_cases"):
        logging.warning("[TestCase Workflow] Refinement returned no test cases, restoring previous set")
        fallback_review = _heuristic_test_case_review(
            raw_test_cases,
            payload.requirements,
            DEFAULT_TEST_CASE_THRESHOLD,
            coverage_plan=coverage_plan,
        )
        fallback_review["approved"] = False
        fallback_review["summary"] = "Test case refinement returned no updated output. Previous test cases were restored and require further review."
        fallback_review["blocking_issues"] = _dedupe_preserve(
            fallback_review["blocking_issues"] + ["Refinement loop did not return an updated test-case set."]
        )
        workflow = {
            "test_cases": raw_test_cases,
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
            },
        }

    test_cases = _hydrate_test_cases(raw_test_cases)
    return _build_response(test_cases, workflow, payload.requirements)
