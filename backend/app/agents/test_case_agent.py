"""
Test Case Generation Agent - Multi-agent loop using Google ADK.

Implements thresholded validation results, iteration history, and a dedicated
refine-existing-test-cases path so the UI can gate export on explicit approval.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import ValidationError

from .adk_runtime import json_generation_config, tool_generation_config
from .analysis_agent import build_requirement_analysis_agent, fallback_requirement_analysis, normalize_requirement_analysis
from .prompting import REAL_WORLD_QA_POLICY, TEST_DESIGN_PROMPT_GUARDRAILS, human_feedback_section
from ..config import get_settings
from ..models import (
    BusinessRule,
    FieldConstraint,
    GenerateTestCasesInput,
    RefineTestCasesInput,
    Requirement,
    RequirementAnalysis,
    RequirementCoveragePlanOutput,
    RequirementCoveragePlan,
    ReviewResult,
    RiskSignal,
    RolePermission,
    ScenarioIntent,
    StateTransition,
    TestCase,
    TestCasesOutput,
    TestStep,
    WorkflowSettings,
)
from ..observability.logging import bind_log_context, get_log_context, reset_log_context
from ..utils.llm_json import (
    parse_coverage_plan_json_detailed,
    parse_requirement_analysis_json_detailed,
    parse_review_json_detailed,
    parse_test_cases_json_detailed,
)

STATE_TEST_CASES = "current_test_cases"
STATE_VALIDATION_FEEDBACK = "validation_feedback"
STATE_COVERAGE_PLAN = "coverage_plan"
STATE_REQUIREMENT_ANALYSIS = "requirement_analysis"

APPROVAL_PHRASE = "APPROVED"
DEFAULT_TEST_CASE_THRESHOLD = 90
DEFAULT_TEST_CASE_MAX_ITERATIONS = 4
DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT = 2
DEFAULT_TEST_CASE_RETRY_ATTEMPTS = 1

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
    "validation": "Functional",
    "boundary": "Functional",
    "boundary value": "Functional",
    "negative": "Functional",
    "happy path": "Functional",
    "positive": "Functional",
    "state transition": "Functional",
    "data variation": "Functional",
    "error handling": "Functional",
    "authorization": "Security",
    "authentication": "Security",
    "compliance": "Security",
    "access control": "Security",
    "api": "Integration",
    "service": "Integration",
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
    (("ignore", "unsupported", "reject", "prevent", "deny", "blocked", "skip", "non prefixed"), "Negative"),
    (("invalid", "required", "format", "blank", "empty", "field", "input"), "Validation"),
    (("min", "max", "limit", "length", "range", "threshold", "boundary"), "Boundary"),
    (("login", "auth", "permission", "role", "access", "admin", "user"), "Authorization"),
    (("status", "state", "workflow", "approve", "reject", "submit", "cancel", "transition"), "State Transition"),
    (("api", "integration", "service", "email", "payment", "upload", "download", "import", "export", "webhook", "install", "installation", "upgrade", "browser", "engine", "dependency", "module", "plugin", "extension"), "Integration"),
    (("error", "failure", "timeout", "unavailable", "retry", "exception"), "Error Handling"),
    (("search", "sort", "filter", "duplicate", "record", "dataset", "data"), "Data Variation"),
]

RULE_TYPE_SCENARIO_HINTS = {
    "Business": {"Happy Path", "Negative"},
    "Validation": {"Validation", "Negative"},
    "Authorization": {"Authorization", "Negative"},
    "State Transition": {"State Transition", "Negative"},
    "Integration": {"Integration", "Error Handling"},
    "Notification": {"Integration", "Error Handling"},
    "Data": {"Data Variation", "Happy Path"},
    "Constraint": {"Validation", "Boundary", "Negative"},
    "Other": {"Happy Path"},
}

CONSTRAINT_SCENARIO_HINTS = {
    "Required": {"Validation", "Negative"},
    "Format": {"Validation", "Negative"},
    "Length": {"Boundary", "Validation"},
    "Range": {"Boundary", "Validation", "Negative"},
    "File Type": {"Validation", "Negative"},
    "File Size": {"Boundary", "Validation"},
    "Allowed Values": {"Validation", "Data Variation"},
    "Uniqueness": {"Data Variation", "Negative"},
    "Dependency": {"Integration", "Error Handling"},
    "Other": {"Validation", "Negative"},
}

RISK_SCENARIO_HINTS = {
    "Security": {"Authorization", "Negative"},
    "Data Integrity": {"Data Variation", "Negative"},
    "Availability": {"Error Handling", "Integration"},
    "Usability": {"Happy Path", "Validation"},
    "Compliance": {"Authorization", "Validation"},
    "Workflow": {"State Transition", "Negative"},
    "Validation": {"Validation", "Boundary"},
    "Integration": {"Integration", "Error Handling"},
    "Other": {"Happy Path", "Negative"},
}

MATCH_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "shall",
    "must",
    "when",
    "only",
    "allow",
    "allows",
    "allowing",
    "system",
    "user",
    "users",
    "into",
    "from",
    "their",
    "than",
    "then",
    "have",
    "will",
}

STEP_TEXT_PREFIX_PATTERN = re.compile(r"^\s*(?:step\s*)?\d+[\).:-]\s*", re.IGNORECASE)
STEP_TEXT_MARKER_PATTERN = re.compile(r"(?:^|\n)\s*(?:step\s*)?\d+[\).:-]\s*", re.IGNORECASE)
STEP_BULLET_MARKER_PATTERN = re.compile(r"(?:^|\n)\s*[-*•]\s+")


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

    scenario_mapped = _normalize_scenario_type(raw)
    scenario_type_to_test_type = {
        "Happy Path": "Functional",
        "Negative": "Functional",
        "Boundary": "Functional",
        "Validation": "Functional",
        "Authorization": "Security",
        "State Transition": "Functional",
        "Integration": "Integration",
        "Error Handling": "Functional",
        "Data Variation": "Functional",
    }
    if scenario_mapped in scenario_type_to_test_type:
        return scenario_type_to_test_type[scenario_mapped]

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


def _infer_scenario_type_from_text(raw_text: Any) -> Optional[str]:
    normalized_text = " ".join(str(raw_text or "").replace("_", " ").replace("-", " ").split()).lower()
    if not normalized_text:
        return None

    mapped = SCENARIO_TYPE_ALIASES.get(normalized_text)
    if mapped:
        return mapped

    if any(token in normalized_text for token in ("happy", "success", "successful", "primary flow", "positive")):
        return "Happy Path"

    for keywords, scenario_type in SCENARIO_KEYWORD_RULES:
        if any(keyword in normalized_text for keyword in keywords):
            return scenario_type

    return None


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

    inferred = _infer_scenario_type_from_text(normalized_key)
    if inferred:
        return inferred

    title_case = raw.title()
    if title_case in ALLOWED_SCENARIO_TYPES:
        return title_case

    logging.warning("[TestCase Workflow] Unknown scenario type '%s', defaulting to Happy Path", raw)
    return "Happy Path"


def _scenario_tag(scenario_type: str) -> str:
    normalized = _normalize_scenario_type(scenario_type)
    return f"scenario:{normalized.lower().replace(' ', '-')}"


def _normalize_source_refs(raw_source_refs: Any) -> List[str]:
    if not isinstance(raw_source_refs, list):
        return []
    return _dedupe_preserve([str(reference).strip() for reference in raw_source_refs if str(reference).strip()])


def _normalize_string_list(raw_values: Any) -> List[str]:
    if raw_values is None:
        return []
    if isinstance(raw_values, list):
        return _dedupe_preserve([str(value).strip() for value in raw_values if str(value).strip()])
    value = str(raw_values).strip()
    return [value] if value else []


def _extract_linked_requirement_ids_from_test_case(
    test_case: Dict[str, Any],
    requirement_id_set: Optional[set[str]] = None,
) -> List[str]:
    candidates: List[str] = []
    candidates.extend(_normalize_string_list(test_case.get("linked_requirement_ids")))
    candidates.extend(_normalize_string_list(test_case.get("requirement_ids")))
    candidates.extend(_normalize_string_list(test_case.get("requirement_id")))
    candidates.extend(_normalize_string_list(test_case.get("tags")))

    linked: List[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        if not value:
            continue
        if requirement_id_set is not None:
            if value in requirement_id_set:
                linked.append(value)
            continue
        if re.match(r"^REQ-[A-Za-z0-9_-]+$", value, flags=re.IGNORECASE):
            linked.append(value)
    return _dedupe_preserve(linked)


def _extract_scenario_refs_from_test_case(test_case: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    candidates.extend(_normalize_string_list(test_case.get("scenario_refs")))
    candidates.extend(_normalize_string_list(test_case.get("scenario_ids")))
    candidates.extend(_normalize_string_list(test_case.get("scenario_id")))
    return _dedupe_preserve(candidates)


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

        if not existing["scenarios"]:
            existing["scenarios"] = _default_scenarios_for_requirement(requirement)
            normalized_plan.append(existing)
            continue

        existing_types = {scenario["scenario_type"] for scenario in existing["scenarios"]}
        for default_scenario in _default_scenarios_for_requirement(requirement):
            if default_scenario["scenario_type"] in existing_types or len(existing["scenarios"]) >= 4:
                continue
            existing["scenarios"].append({**default_scenario, "must_have": False})
            existing_types.add(default_scenario["scenario_type"])

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
        inferred_type = _infer_scenario_type_from_text(normalized_tag.split(":", 1)[1])
        if inferred_type:
            extracted.append(inferred_type)

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
        linked_requirements = set(_extract_linked_requirement_ids_from_test_case(test_case, requirement_id_set))
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


def _collect_test_case_text(test_case: Dict[str, Any]) -> str:
    steps = test_case.get("steps") or []
    step_text = " ".join(
        f"{step.get('action', '')} {step.get('expected', '')} {step.get('test_data', '')}"
        for step in steps
        if isinstance(step, dict)
    )
    return " ".join(
        [
            str(test_case.get("title") or ""),
            str(test_case.get("description") or ""),
            str(test_case.get("preconditions") or ""),
            str(test_case.get("expected_result") or ""),
            str(test_case.get("test_data") or ""),
            " ".join(str(tag) for tag in (test_case.get("tags") or [])),
            " ".join(_normalize_string_list(test_case.get("linked_requirement_ids"))),
            " ".join(_normalize_string_list(test_case.get("scenario_refs"))),
            step_text,
        ]
    ).lower()


def _tokenize_for_match(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", str(text).lower())
    return {token for token in tokens if len(token) >= 3 and token not in MATCH_STOP_WORDS}


def _analysis_item_is_covered(item_text: str, linked_case_texts: List[str], linked_scenarios: set[str], hinted_scenarios: set[str]) -> bool:
    if hinted_scenarios and linked_scenarios.intersection(hinted_scenarios):
        return True

    item_tokens = _tokenize_for_match(item_text)
    if not item_tokens:
        return bool(linked_case_texts)

    minimum_overlap = 1 if len(item_tokens) <= 3 else 2
    for case_text in linked_case_texts:
        case_tokens = _tokenize_for_match(case_text)
        if len(item_tokens.intersection(case_tokens)) >= minimum_overlap:
            return True
    return False


def _compute_requirement_analysis_metrics(
    requirement_analysis: List[Dict[str, Any]],
    test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
) -> Dict[str, Any]:
    normalized_analysis = normalize_requirement_analysis(
        requirement_analysis or fallback_requirement_analysis(requirements),
        requirements,
    )
    requirement_ids = _serialize_requirement_ids(requirements)
    requirement_id_set = set(requirement_ids)
    test_cases_by_requirement: Dict[str, List[Dict[str, Any]]] = {requirement_id: [] for requirement_id in requirement_ids}

    for test_case in test_cases:
        linked_ids = set(_extract_linked_requirement_ids_from_test_case(test_case, requirement_id_set))
        for requirement_id in linked_ids:
            test_cases_by_requirement[requirement_id].append(test_case)

    business_rules_total = 0
    business_rules_covered = 0
    field_constraints_total = 0
    field_constraints_covered = 0
    role_permissions_total = 0
    role_permissions_covered = 0
    state_transitions_total = 0
    state_transitions_covered = 0
    risk_signals_total = 0
    risk_signals_covered = 0
    rules_without_tests: List[str] = []
    constraints_without_tests: List[str] = []
    role_permissions_without_tests: List[str] = []
    transitions_without_tests: List[str] = []
    high_risk_items_without_tests: List[str] = []
    requirement_analysis_summary: Dict[str, Dict[str, Any]] = {}

    for item in normalized_analysis:
        requirement_id = str(item.get("requirement_id") or "").strip()
        linked_cases = test_cases_by_requirement.get(requirement_id, [])
        linked_case_texts = [_collect_test_case_text(test_case) for test_case in linked_cases]
        linked_scenarios = {
            scenario_type
            for test_case in linked_cases
            for scenario_type in _extract_scenario_types_from_test_case(test_case)
        }

        rule_hits = 0
        constraint_hits = 0
        permission_hits = 0
        transition_hits = 0
        risk_hits = 0
        covered_rule_ids: List[str] = []
        missing_rule_ids: List[str] = []
        covered_constraint_ids: List[str] = []
        missing_constraint_ids: List[str] = []
        covered_permission_ids: List[str] = []
        missing_permission_ids: List[str] = []
        covered_transition_ids: List[str] = []
        missing_transition_ids: List[str] = []
        covered_risk_ids: List[str] = []
        missing_risk_ids: List[str] = []

        for rule in item.get("business_rules") or []:
            business_rules_total += 1
            rule_text = f"{rule.get('title', '')} {rule.get('description', '')}"
            hinted_scenarios = RULE_TYPE_SCENARIO_HINTS.get(str(rule.get("rule_type") or "Other"), {"Happy Path"})
            covered = _analysis_item_is_covered(rule_text, linked_case_texts, linked_scenarios, hinted_scenarios)
            if covered:
                business_rules_covered += 1
                rule_hits += 1
                covered_rule_ids.append(str(rule.get("id") or ""))
            else:
                missing_rule_ids.append(str(rule.get("id") or ""))
                rules_without_tests.append(f"{requirement_id} - {rule.get('title') or 'Untitled rule'}")

        for constraint in item.get("field_constraints") or []:
            field_constraints_total += 1
            constraint_text = " ".join(
                [
                    str(constraint.get("field_name") or ""),
                    str(constraint.get("description") or ""),
                    str(constraint.get("value") or ""),
                    str(constraint.get("negative_example") or ""),
                ]
            )
            hinted_scenarios = CONSTRAINT_SCENARIO_HINTS.get(str(constraint.get("constraint_type") or "Other"), {"Validation"})
            covered = _analysis_item_is_covered(constraint_text, linked_case_texts, linked_scenarios, hinted_scenarios)
            if covered:
                field_constraints_covered += 1
                constraint_hits += 1
                covered_constraint_ids.append(str(constraint.get("id") or ""))
            else:
                missing_constraint_ids.append(str(constraint.get("id") or ""))
                constraints_without_tests.append(
                    f"{requirement_id} - {constraint.get('field_name') or 'field'}: {constraint.get('description') or 'constraint'}"
                )

        for permission in item.get("role_permissions") or []:
            role_permissions_total += 1
            permission_text = f"{permission.get('role', '')} {permission.get('action', '')} {permission.get('conditions', '')}"
            covered = _analysis_item_is_covered(permission_text, linked_case_texts, linked_scenarios, {"Authorization", "Negative"})
            if covered:
                role_permissions_covered += 1
                permission_hits += 1
                covered_permission_ids.append(str(permission.get("id") or ""))
            else:
                missing_permission_ids.append(str(permission.get("id") or ""))
                role_permissions_without_tests.append(
                    f"{requirement_id} - {permission.get('role') or 'Role'} {permission.get('action') or ''}".strip()
                )

        for transition in item.get("state_transitions") or []:
            state_transitions_total += 1
            transition_text = " ".join(
                [
                    str(transition.get("entity") or ""),
                    str(transition.get("from_state") or ""),
                    str(transition.get("to_state") or ""),
                    str(transition.get("trigger") or ""),
                    str(transition.get("guards") or ""),
                ]
            )
            covered = _analysis_item_is_covered(transition_text, linked_case_texts, linked_scenarios, {"State Transition", "Negative"})
            if covered:
                state_transitions_covered += 1
                transition_hits += 1
                covered_transition_ids.append(str(transition.get("id") or ""))
            else:
                missing_transition_ids.append(str(transition.get("id") or ""))
                transitions_without_tests.append(
                    f"{requirement_id} - {transition.get('from_state') or 'Unknown'} → {transition.get('to_state') or 'Unknown'}"
                )

        for risk in item.get("risk_signals") or []:
            risk_signals_total += 1
            risk_text = f"{risk.get('title', '')} {risk.get('rationale', '')}"
            hinted_scenarios = RISK_SCENARIO_HINTS.get(str(risk.get("category") or "Other"), {"Happy Path", "Negative"})
            covered = _analysis_item_is_covered(risk_text, linked_case_texts, linked_scenarios, hinted_scenarios)
            if covered:
                risk_signals_covered += 1
                risk_hits += 1
                covered_risk_ids.append(str(risk.get("id") or ""))
            elif str(risk.get("severity") or "Medium") in {"Critical", "High"}:
                missing_risk_ids.append(str(risk.get("id") or ""))
                high_risk_items_without_tests.append(f"{requirement_id} - {risk.get('title') or 'Untitled risk'}")
            else:
                missing_risk_ids.append(str(risk.get("id") or ""))

        requirement_analysis_summary[requirement_id] = {
            "business_rules_total": len(item.get("business_rules") or []),
            "business_rules_covered": rule_hits,
            "rules_covered": _dedupe_preserve(covered_rule_ids),
            "rules_missing": _dedupe_preserve(missing_rule_ids),
            "field_constraints_total": len(item.get("field_constraints") or []),
            "field_constraints_covered": constraint_hits,
            "constraints_covered": _dedupe_preserve(covered_constraint_ids),
            "constraints_missing": _dedupe_preserve(missing_constraint_ids),
            "role_permissions_total": len(item.get("role_permissions") or []),
            "role_permissions_covered": permission_hits,
            "permissions_covered": _dedupe_preserve(covered_permission_ids),
            "permissions_missing": _dedupe_preserve(missing_permission_ids),
            "state_transitions_total": len(item.get("state_transitions") or []),
            "state_transitions_covered": transition_hits,
            "transitions_covered": _dedupe_preserve(covered_transition_ids),
            "transitions_missing": _dedupe_preserve(missing_transition_ids),
            "risk_signals_total": len(item.get("risk_signals") or []),
            "risk_signals_covered": risk_hits,
            "risks_covered": _dedupe_preserve(covered_risk_ids),
            "risks_missing": _dedupe_preserve(missing_risk_ids),
        }

    return {
        "business_rules_total": business_rules_total,
        "business_rules_covered": business_rules_covered,
        "rule_coverage_ratio": round(business_rules_covered / business_rules_total, 2) if business_rules_total else 1.0,
        "field_constraints_total": field_constraints_total,
        "field_constraints_covered": field_constraints_covered,
        "constraint_coverage_ratio": round(field_constraints_covered / field_constraints_total, 2) if field_constraints_total else 1.0,
        "role_permissions_total": role_permissions_total,
        "role_permissions_covered": role_permissions_covered,
        "role_permission_coverage_ratio": round(role_permissions_covered / role_permissions_total, 2) if role_permissions_total else 1.0,
        "state_transitions_total": state_transitions_total,
        "state_transitions_covered": state_transitions_covered,
        "transition_coverage_ratio": round(state_transitions_covered / state_transitions_total, 2) if state_transitions_total else 1.0,
        "risk_signals_total": risk_signals_total,
        "risk_signals_covered": risk_signals_covered,
        "risk_coverage_ratio": round(risk_signals_covered / risk_signals_total, 2) if risk_signals_total else 1.0,
        "rules_without_tests": _dedupe_preserve(rules_without_tests),
        "constraints_without_tests": _dedupe_preserve(constraints_without_tests),
        "role_permissions_without_tests": _dedupe_preserve(role_permissions_without_tests),
        "transitions_without_tests": _dedupe_preserve(transitions_without_tests),
        "high_risk_items_without_tests": _dedupe_preserve(high_risk_items_without_tests),
        "requirement_analysis_summary": requirement_analysis_summary,
    }


def _compute_grounded_context_metrics(test_cases: List[Dict[str, Any]], context: Optional[Any]) -> Dict[str, Any]:
    grounded_context = getattr(context, "grounded_context", None) if context else None
    artifact_sources = list(getattr(grounded_context, "artifact_sources", []) or [])
    artifact_ids = [str(source.id).strip() for source in artifact_sources if getattr(source, "id", None)]
    artifact_id_set = set(artifact_ids)

    if not artifact_ids:
        return {
            "grounded_artifact_count": 0,
            "source_backed_test_cases": 0,
            "grounded_source_backed_case_ratio": 1.0,
            "artifacts_with_references": 0,
            "artifact_reference_coverage_ratio": 1.0,
            "unreferenced_artifacts": [],
        }

    referenced_artifacts: set[str] = set()
    source_backed_test_cases = 0
    for test_case in test_cases:
        source_refs = [reference for reference in _normalize_source_refs(test_case.get("source_refs")) if reference in artifact_id_set]
        if not source_refs:
            continue
        source_backed_test_cases += 1
        referenced_artifacts.update(source_refs)

    return {
        "grounded_artifact_count": len(artifact_ids),
        "source_backed_test_cases": source_backed_test_cases,
        "grounded_source_backed_case_ratio": round(source_backed_test_cases / len(test_cases), 2) if test_cases else 0.0,
        "artifacts_with_references": len(referenced_artifacts),
        "artifact_reference_coverage_ratio": round(len(referenced_artifacts) / len(artifact_ids), 2) if artifact_ids else 1.0,
        "unreferenced_artifacts": [artifact_id for artifact_id in artifact_ids if artifact_id not in referenced_artifacts],
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
        tagged_ids = set(_extract_linked_requirement_ids_from_test_case(test_case, requirement_id_set))
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

        if analysis_metrics["high_risk_items_without_tests"]:
            preview = ", ".join(analysis_metrics["high_risk_items_without_tests"][:3])
            blocking_issues.append(
                "High-risk requirement analysis items without coverage: "
                f"{preview}"
                + ("." if len(analysis_metrics["high_risk_items_without_tests"]) <= 3 else ", ...")
            )
            unmet_criteria.append("High or critical risks from requirement analysis need corresponding test coverage.")
            score -= len(analysis_metrics["high_risk_items_without_tests"]) * 6

        if analysis_metrics["rules_without_tests"]:
            preview = ", ".join(analysis_metrics["rules_without_tests"][:3])
            suggestions.append(
                "Add requirement-rule coverage for: "
                f"{preview}"
                + ("." if len(analysis_metrics["rules_without_tests"]) <= 3 else ", ...")
            )
            score -= min(10, len(analysis_metrics["rules_without_tests"]) * 2)

        if analysis_metrics["constraints_without_tests"]:
            preview = ", ".join(analysis_metrics["constraints_without_tests"][:3])
            suggestions.append(
                "Add validation or boundary coverage for: "
                f"{preview}"
                + ("." if len(analysis_metrics["constraints_without_tests"]) <= 3 else ", ...")
            )
            score -= min(10, len(analysis_metrics["constraints_without_tests"]) * 2)

        if analysis_metrics["role_permissions_without_tests"]:
            preview = ", ".join(analysis_metrics["role_permissions_without_tests"][:3])
            suggestions.append(
                "Add authorization coverage for: "
                f"{preview}"
                + ("." if len(analysis_metrics["role_permissions_without_tests"]) <= 3 else ", ...")
            )
            score -= min(8, len(analysis_metrics["role_permissions_without_tests"]) * 2)

        if analysis_metrics["transitions_without_tests"]:
            preview = ", ".join(analysis_metrics["transitions_without_tests"][:3])
            suggestions.append(
                "Add state-transition coverage for: "
                f"{preview}"
                + ("." if len(analysis_metrics["transitions_without_tests"]) <= 3 else ", ...")
            )
            score -= min(10, len(analysis_metrics["transitions_without_tests"]) * 2)

        if grounded_context_metrics["grounded_artifact_count"] > 0:
            if grounded_context_metrics["source_backed_test_cases"] == 0:
                suggestions.append("Grounded context is available, but no test cases cite artifact source references yet.")
                score -= 5
            elif grounded_context_metrics["artifact_reference_coverage_ratio"] < 0.5:
                preview = ", ".join(grounded_context_metrics["unreferenced_artifacts"][:3])
                suggestions.append(
                    "Add broader grounded-context references for artifacts such as: "
                    f"{preview}"
                    + ("." if len(grounded_context_metrics["unreferenced_artifacts"]) <= 3 else ", ...")
                )
                score -= 3

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

    resolved_threshold = int(
        settings.approval_threshold if settings.approval_threshold is not None else DEFAULT_TEST_CASE_THRESHOLD
    )
    resolved_max_iterations = int(
        settings.max_iterations if settings.max_iterations is not None else DEFAULT_TEST_CASE_MAX_ITERATIONS
    )
    resolved_timeout_seconds = int(settings.timeout_seconds) if settings.timeout_seconds is not None else None
    resolved_stall_iteration_limit = int(
        settings.stall_iteration_limit
        if settings.stall_iteration_limit is not None
        else DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT
    )
    resolved_retry_attempts = int(
        settings.retry_attempts if settings.retry_attempts is not None else DEFAULT_TEST_CASE_RETRY_ATTEMPTS
    )

    return {
        "approval_threshold": max(0, min(100, resolved_threshold)),
        "max_iterations": max(1, resolved_max_iterations),
        "timeout_seconds": max(1, resolved_timeout_seconds) if resolved_timeout_seconds is not None else None,
        "stall_iteration_limit": max(1, resolved_stall_iteration_limit),
        "retry_attempts": max(0, resolved_retry_attempts),
    }


def _new_workflow_diagnostics(*, attempt_count: int = 1) -> Dict[str, Any]:
    return {
        "status": "completed",
        "used_fallback": False,
        "failure_reason": None,
        "timed_out": False,
        "stalled": False,
        "max_iterations_reached": False,
        "parser_failures": [],
        "warnings": [],
        "best_iteration": None,
        "attempt_count": attempt_count,
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
) -> None:
    if not error:
        return
    message = f"{author}: {error}"
    sample = _diagnostic_sample(raw_text)
    if sample:
        message = f"{message} | sample: {sample}"
    _append_unique_message(diagnostics["parser_failures"], message)
    diagnostics["status"] = "partial"
    diagnostics["failure_reason"] = diagnostics["failure_reason"] or "parser_failure"
    _log_test_case_workflow(
        "parser_failure",
        author=author,
        error=error,
        sample=sample or None,
        parser_failure_count=len(diagnostics["parser_failures"]),
        status=diagnostics["status"],
    )


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
        include_contents='none',
        generate_content_config=json_generation_config(max_output_tokens=12000),
        output_schema=RequirementCoveragePlanOutput,
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
        include_contents='none',
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
5. Every must-have scenario from the coverage plan is represented by at least one test case.
6. Requirement analysis details (rules, constraints, permissions, transitions, and risks) are reflected when present.
7. Tags include a scenario marker formatted like scenario:happy-path or scenario:negative.
8. Priority, type, status, and automation status are valid.
9. Test data, preconditions, and overall expected_result are present when needed.
10. Cases are realistic enough for manual execution and future Playwright automation: no vague steps, TBD values, or unsupported feature invention.
11. Human feedback has been addressed without treating feedback as an instruction to weaken quality gates.

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


def _build_generation_pipeline(
    model: str,
    requirements_text: str,
    context_text: str,
    template_text: str,
    threshold: int,
    max_iterations: int,
    human_feedback: Optional[str] = None,
) -> Agent:
    feedback_section = human_feedback_section("Human Feedback to Address", human_feedback)

    generator_agent = Agent(
        name="TestCaseGeneratorAgent",
        model=model,
        include_contents='default',
        generate_content_config=json_generation_config(max_output_tokens=20000, temperature=0.15),
        output_schema=TestCasesOutput,
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
2. Prefer one test case per planned scenario; combine scenarios only when they share the same actor, setup, and expected outcome.
3. Reflect business rules, field constraints, role permissions, state transitions, and risks from the requirement analysis whenever they apply.
4. Set `linked_requirement_ids` to a JSON array containing every requirement ID covered by the test case.
5. Also include those requirement IDs in `tags` for backward compatibility, plus one scenario tag using the format scenario:<kebab-case-scenario-type>.
6. Set `scenario_refs` to the coverage-plan scenario ID(s) implemented by the test case when available.
7. When grounded context is provided, include `source_refs` with the relevant artifact IDs used by the test case.
8. Include detailed steps, expected results, realistic priorities, and execution metadata.
9. Keep each test case centered on one primary scenario from the coverage plan.
10. The `steps` field MUST be a JSON array of step objects shaped like {{"step": 1, "action": "...", "expected": "...", "test_data": null}}.
11. Never return `steps` as a single string, markdown list, or paragraph.
12. Make steps real-world executable: name the actor/role, setup data, UI/API action, validation point, and observable outcome.
13. Use concrete but non-sensitive test data. If exact data is unknown, put explicit assumptions in preconditions or test_data; never use TBD/placeholder text.
14. Include negative, boundary, authorization, and error-handling coverage when the coverage plan or requirement analysis calls for it; do not overproduce only happy paths.
15. Prefer business-readable test data such as `qa.manager@example.test`, `INV-1001`, or `2026-05-10`; do not use real personal data or secrets.
16. Output ONLY a JSON object shaped like {{"test_cases": [...]}}.
""",
        description="Generates initial test cases from approved requirements",
        output_key=STATE_TEST_CASES,
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
        include_contents='default',
        generate_content_config=json_generation_config(max_output_tokens=20000, temperature=0.15),
        output_schema=TestCasesOutput,
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
5. Preserve or improve `scenario_refs` from the coverage plan when available.
6. Preserve or improve any grounded-context `source_refs` when grounded context is available.
7. The `steps` field MUST remain a JSON array of objects with `step`, `action`, `expected`, and optional `test_data`.
8. Never return `steps` as a plain string, markdown list, or free-form paragraph.
9. Remove generic actions like "navigate to the feature area" when a more concrete UI/API action can be inferred.
10. Add missing negative, boundary, authorization, state-transition, or integration cases when feedback or coverage gaps require them.
11. Output ONLY the JSON object.
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
    stall_iteration_limit = int(
        resolved_settings["stall_iteration_limit"] or DEFAULT_TEST_CASE_STALL_ITERATION_LIMIT
    )
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
{human_feedback or 'No human feedback provided.'}
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
            author = getattr(event, 'author', 'unknown')
            _log_test_case_workflow("event_received", session_id=session.id, author=author)
            _record_event_error(diagnostics, author, event)

            if getattr(event, "partial", False):
                continue

            if not event.content or not event.content.parts:
                continue

            for part in event.content.parts:
                text = getattr(part, 'text', None)
                if not text:
                    continue

                if author in {"TestCaseGeneratorAgent", "TestCaseRefinementAgent", "TestCaseRefinerAgent"}:
                    parsed_test_cases, parse_error = parse_test_cases_json_detailed(text)
                    if parsed_test_cases:
                        current_test_cases = parsed_test_cases
                    else:
                        _record_parser_failure(diagnostics, author, parse_error, text)

                if author == "CoveragePlannerAgent":
                    parsed_coverage_plan, parse_error = parse_coverage_plan_json_detailed(text)
                    if parsed_coverage_plan:
                        current_coverage_plan = _normalize_coverage_plan(parsed_coverage_plan, requirements)
                    else:
                        _record_parser_failure(diagnostics, author, parse_error, text)

                if author == "RequirementAnalysisAgent":
                    parsed_requirement_analysis, parse_error = parse_requirement_analysis_json_detailed(text)
                    if parsed_requirement_analysis:
                        current_requirement_analysis = normalize_requirement_analysis(parsed_requirement_analysis, requirements)
                    else:
                        _record_parser_failure(diagnostics, author, parse_error, text)

                if author == "TestCaseValidatorAgent":
                    parsed_review, parse_error = parse_review_json_detailed(text, default_threshold=threshold)
                    if not parsed_review:
                        _record_parser_failure(diagnostics, author, parse_error, text)
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
                    if current_test_cases and (
                        not best_candidate or _prefer_review(candidate_review, best_candidate["review"])
                    ):
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
    if state_test_cases:
        current_test_cases = state_test_cases
    elif str(state_test_cases_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateTestCases", state_test_cases_error, state_test_cases_raw)

    state_coverage_plan_raw = session_state.get(STATE_COVERAGE_PLAN, "[]")
    state_coverage_plan, state_coverage_plan_error = parse_coverage_plan_json_detailed(state_coverage_plan_raw)
    if state_coverage_plan:
        current_coverage_plan = _normalize_coverage_plan(state_coverage_plan, requirements)
    elif str(state_coverage_plan_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateCoveragePlan", state_coverage_plan_error, state_coverage_plan_raw)

    state_requirement_analysis_raw = session_state.get(STATE_REQUIREMENT_ANALYSIS, "[]")
    state_requirement_analysis, state_requirement_analysis_error = parse_requirement_analysis_json_detailed(
        state_requirement_analysis_raw
    )
    if state_requirement_analysis:
        current_requirement_analysis = normalize_requirement_analysis(state_requirement_analysis, requirements)
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
        _record_parser_failure(diagnostics, "SessionStateValidationReview", state_review_error, state_review_raw)

    heuristic_review = _heuristic_test_case_review(
        current_test_cases,
        requirements,
        threshold,
        coverage_plan=current_coverage_plan,
        requirement_analysis=current_requirement_analysis,
        context=context,
    )
    final_review = _merge_review_results(model_review, heuristic_review)

    if best_candidate and (
        not current_test_cases or _prefer_review(best_candidate["review"], final_review)
    ):
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
        selection_entry["summary"] = (
            f"Retained best-scoring test cases from iteration {diagnostics['best_iteration']}. "
            f"{final_review['summary']}"
        ).strip()
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
                sources = ", ".join(
                    f"{source.id} ({source.label}, {source.status})"
                    for source in grounded_context.artifact_sources[:8]
                )
                context_parts.append(f"Grounded artifact sources: {sources}")
            if grounded_context.ui_elements:
                ui_elements = ", ".join(
                    f"{element.element_type}: {element.name}"
                    for element in grounded_context.ui_elements[:8]
                )
                context_parts.append(f"Grounded UI elements: {ui_elements}")
            if grounded_context.api_surfaces:
                api_surfaces = ", ".join(
                    f"{surface.method or 'API'} {surface.path or surface.name}"
                    for surface in grounded_context.api_surfaces[:8]
                )
                context_parts.append(f"Grounded API surfaces: {api_surfaces}")
            if grounded_context.workflows:
                workflows = ", ".join(
                    f"{workflow.name} [{'; '.join(workflow.transitions[:4])}]"
                    for workflow in grounded_context.workflows[:4]
                )
                context_parts.append(f"Grounded workflows: {workflows}")
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
    grounded_source_refs: List[str] = []
    if context and getattr(context, "grounded_context", None) and getattr(context.grounded_context, "artifact_sources", None):
        grounded_source_refs = [str(source.id) for source in context.grounded_context.artifact_sources[:1] if getattr(source, "id", None)]

    for idx, plan_item in enumerate(normalized_plan, start=1):
        requirement = requirements_by_id.get(str(plan_item.get("requirement_id") or "").strip())
        if not requirement:
            continue

        planned_scenarios = list(plan_item.get("scenarios") or [])
        selected_scenarios = planned_scenarios or _default_scenarios_for_requirement(requirement)

        for scenario_offset, scenario in enumerate(selected_scenarios, start=1):
            scenario_type = _normalize_scenario_type(scenario.get("scenario_type"))
            scenario_id = str(scenario.get("id") or f"{requirement.id}-SCN-{scenario_offset:02d}")
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
                    "linked_requirement_ids": [requirement.id],
                    "scenario_refs": [scenario_id],
                    "source_refs": grounded_source_refs,
                }
            )
    return raw_test_cases


def _extract_step_text_blocks(text: str, marker_pattern: re.Pattern[str]) -> List[str]:
    matches = list(marker_pattern.finditer(text))
    if not matches:
        return []

    blocks: List[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def _normalize_raw_steps(raw_steps: Any) -> List[Any]:
    if raw_steps is None:
        return []

    if isinstance(raw_steps, list):
        normalized: List[Any] = []
        for item in raw_steps:
            if isinstance(item, list):
                normalized.extend(_normalize_raw_steps(item))
                continue
            if item is None:
                continue
            normalized.append(item)
        return normalized

    if isinstance(raw_steps, dict):
        return [raw_steps]

    if isinstance(raw_steps, str):
        normalized_text = raw_steps.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized_text:
            return []

        numbered_blocks = _extract_step_text_blocks(normalized_text, STEP_TEXT_MARKER_PATTERN)
        if numbered_blocks:
            return numbered_blocks

        bullet_blocks = _extract_step_text_blocks(normalized_text, STEP_BULLET_MARKER_PATTERN)
        if bullet_blocks:
            return bullet_blocks

        lines = [line.strip() for line in normalized_text.split("\n") if line.strip()]
        if len(lines) > 1:
            return lines

        return [normalized_text]

    text_value = str(raw_steps).strip()
    return [text_value] if text_value else []


def _hydrate_text_step(raw_step: str, step_number: int) -> TestStep:
    cleaned = STEP_TEXT_PREFIX_PATTERN.sub("", raw_step.strip())
    cleaned = re.sub(r"^\s*[-*•]\s+", "", cleaned)
    cleaned = re.sub(r"^\s*action\s*[:\-]\s*", "", cleaned, flags=re.IGNORECASE)

    action = cleaned
    expected = ""

    for separator in ("->", "=>", "→"):
        if separator not in cleaned:
            continue
        action, expected = cleaned.split(separator, 1)
        break

    if not expected:
        split_parts = re.split(r"\bexpected(?:\s+result)?\s*[:\-]\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(split_parts) == 2:
            action, expected = split_parts

    action = action.strip() or cleaned.strip() or f"Step {step_number}"
    expected = expected.strip()

    return TestStep(
        step=step_number,
        action=action,
        expected=expected,
        test_data=None,
    )


def _hydrate_test_cases(raw_test_cases: List[Dict[str, Any]]) -> List[TestCase]:
    test_cases: List[TestCase] = []
    for index, raw_test_case in enumerate(raw_test_cases, start=1):
        try:
            tags = _normalize_string_list(raw_test_case.get("tags"))
            linked_requirement_ids = _extract_linked_requirement_ids_from_test_case({**raw_test_case, "tags": tags})
            scenario_refs = _extract_scenario_refs_from_test_case(raw_test_case)
            normalized_tags = _dedupe_preserve(tags + linked_requirement_ids)
            steps = []
            for raw_step in _normalize_raw_steps(raw_test_case.get("steps", [])):
                if isinstance(raw_step, str):
                    steps.append(_hydrate_text_step(raw_step, len(steps) + 1))
                    continue

                if not isinstance(raw_step, dict):
                    continue

                steps.append(
                    TestStep(
                        step=raw_step.get("step", len(steps) + 1),
                        action=str(raw_step.get("action", "") or ""),
                        expected=str(raw_step.get("expected", "") or ""),
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
                    estimated_time=str(raw_test_case["estimated_time"]) if raw_test_case.get("estimated_time") is not None else None,
                    automation_status=_normalize_automation_status(raw_test_case.get("automation_status")),
                    component=raw_test_case.get("component"),
                    tags=normalized_tags,
                    linked_requirement_ids=linked_requirement_ids,
                    scenario_refs=scenario_refs,
                    source_refs=_normalize_source_refs(raw_test_case.get("source_refs")),
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
                "linked_requirement_ids": test_case.linked_requirement_ids or _extract_linked_requirement_ids_from_test_case({"tags": test_case.tags or []}),
                "scenario_refs": test_case.scenario_refs or [],
                "source_refs": test_case.source_refs or [],
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


def _hydrate_requirement_analysis(
    raw_requirement_analysis: List[Dict[str, Any]],
    requirements: List[Requirement],
) -> List[RequirementAnalysis]:
    normalized_analysis = normalize_requirement_analysis(
        raw_requirement_analysis or fallback_requirement_analysis(requirements),
        requirements,
    )
    hydrated: List[RequirementAnalysis] = []

    for item in normalized_analysis:
        hydrated.append(
            RequirementAnalysis(
                requirement_id=str(item.get("requirement_id") or ""),
                requirement_text=str(item.get("requirement_text") or ""),
                business_rules=[
                    BusinessRule(
                        id=str(rule.get("id") or ""),
                        requirement_id=str(rule.get("requirement_id") or item.get("requirement_id") or ""),
                        title=str(rule.get("title") or "Untitled rule"),
                        description=str(rule.get("description") or rule.get("title") or ""),
                        rule_type=str(rule.get("rule_type") or "Business"),
                    )
                    for rule in item.get("business_rules") or []
                ],
                field_constraints=[
                    FieldConstraint(
                        id=str(constraint.get("id") or ""),
                        requirement_id=str(constraint.get("requirement_id") or item.get("requirement_id") or ""),
                        field_name=str(constraint.get("field_name") or "field"),
                        description=str(constraint.get("description") or ""),
                        constraint_type=str(constraint.get("constraint_type") or "Other"),
                        operator=constraint.get("operator"),
                        value=constraint.get("value"),
                        negative_example=constraint.get("negative_example"),
                    )
                    for constraint in item.get("field_constraints") or []
                ],
                role_permissions=[
                    RolePermission(
                        id=str(permission.get("id") or ""),
                        requirement_id=str(permission.get("requirement_id") or item.get("requirement_id") or ""),
                        role=str(permission.get("role") or "Unknown role"),
                        action=str(permission.get("action") or "Unknown action"),
                        effect=str(permission.get("effect") or "Allow"),
                        conditions=permission.get("conditions"),
                    )
                    for permission in item.get("role_permissions") or []
                ],
                state_transitions=[
                    StateTransition(
                        id=str(transition.get("id") or ""),
                        requirement_id=str(transition.get("requirement_id") or item.get("requirement_id") or ""),
                        entity=str(transition.get("entity") or "Workflow item"),
                        from_state=str(transition.get("from_state") or "Unknown"),
                        to_state=str(transition.get("to_state") or "Unknown"),
                        trigger=transition.get("trigger"),
                        guards=transition.get("guards"),
                    )
                    for transition in item.get("state_transitions") or []
                ],
                risk_signals=[
                    RiskSignal(
                        id=str(risk.get("id") or ""),
                        requirement_id=str(risk.get("requirement_id") or item.get("requirement_id") or ""),
                        title=str(risk.get("title") or "Untitled risk"),
                        rationale=str(risk.get("rationale") or ""),
                        category=str(risk.get("category") or "Other"),
                        severity=str(risk.get("severity") or "Medium"),
                    )
                    for risk in item.get("risk_signals") or []
                ],
                suggested_scenarios=[str(scenario) for scenario in item.get("suggested_scenarios") or []],
                dependencies=[str(dependency) for dependency in item.get("dependencies") or []],
            )
        )

    return hydrated


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

    return {
        "test_cases": test_cases,
        "approved": approved,
        "review": review,
        "iteration_history": list(workflow.get("iteration_history") or []),
        "coverage_plan": coverage_plan,
        "requirement_analysis": requirement_analysis,
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": dict(workflow.get("workflow_diagnostics") or {}),
    }
def generate_test_cases(
    payload: GenerateTestCasesInput,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)

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
    if not raw_test_cases:
        logging.warning("[TestCase Workflow] No test cases from pipeline, using deterministic fallback")
        raw_test_cases = _fallback_raw_test_cases(payload.requirements, payload.context, coverage_plan=coverage_plan)
        fallback_review = _heuristic_test_case_review(
            raw_test_cases,
            payload.requirements,
            threshold,
            coverage_plan=coverage_plan,
            requirement_analysis=requirement_analysis,
            context=payload.context,
        )
        fallback_review["approved"] = False
        fallback_review["summary"] = "Test-case fallback produced a draft suite that still requires review approval."
        fallback_review["blocking_issues"] = _dedupe_preserve(
            fallback_review["blocking_issues"] + ["Deterministic fallback was used instead of a completed generation/validation loop."]
        )
        workflow_diagnostics = dict(workflow.get("workflow_diagnostics") or _new_workflow_diagnostics())
        workflow_diagnostics["status"] = "fallback"
        workflow_diagnostics["used_fallback"] = True
        workflow_diagnostics["failure_reason"] = workflow_diagnostics.get("failure_reason") or "fallback_generated_artifacts"
        _append_unique_message(
            workflow_diagnostics["warnings"],
            "Test-case fallback produced deterministic draft artifacts because the generation workflow returned no test cases.",
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


def refine_test_cases(
    payload: RefineTestCasesInput,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)

    existing_test_cases = _serialize_test_cases(payload.test_cases)
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
        fallback_review["blocking_issues"] = _dedupe_preserve(
            fallback_review["blocking_issues"] + ["Refinement loop did not return an updated test-case set."]
        )
        workflow_diagnostics = dict(workflow.get("workflow_diagnostics") or _new_workflow_diagnostics())
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
