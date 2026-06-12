"""Coverage, traceability, and normalization helpers for test-case generation."""

import logging
import re
from typing import Any, Dict, List, Optional

from .analysis_agent import fallback_requirement_analysis, normalize_requirement_analysis
from ..models import Requirement

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
                    f"{requirement_id} - {transition.get('from_state') or 'Unknown'} \u2192 {transition.get('to_state') or 'Unknown'}"
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
