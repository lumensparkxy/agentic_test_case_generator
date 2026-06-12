"""Deterministic fallback generation helpers for test-case workflows."""

import re
from typing import Any, Dict, List, Optional

from .test_case_coverage import (
    _default_scenarios_for_requirement,
    _extract_linked_requirement_ids_from_test_case,
    _extract_scenario_types_from_test_case,
    _fallback_coverage_plan,
    _normalize_coverage_plan,
    _normalize_priority,
    _normalize_scenario_type,
    _scenario_tag,
    _serialize_requirement_ids,
)
from ..models import Requirement

GROUNDING_STOPWORDS = {
    "about",
    "after",
    "allow",
    "allows",
    "and",
    "are",
    "before",
    "can",
    "for",
    "from",
    "into",
    "shall",
    "should",
    "system",
    "test",
    "tests",
    "that",
    "the",
    "their",
    "this",
    "to",
    "using",
    "user",
    "users",
    "via",
    "when",
    "with",
}


def _grounded_sources(context: Optional[Any]) -> List[Any]:
    grounded_context = getattr(context, "grounded_context", None) if context else None
    return list(getattr(grounded_context, "artifact_sources", []) or [])


def _grounded_ui_elements(context: Optional[Any]) -> List[Any]:
    grounded_context = getattr(context, "grounded_context", None) if context else None
    return list(getattr(grounded_context, "ui_elements", []) or [])


def _tokenize_grounding_text(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 2 and token not in GROUNDING_STOPWORDS
    }


def _score_grounded_text(requirement_tokens: set[str], *values: Any) -> int:
    candidate_tokens: set[str] = set()
    for value in values:
        candidate_tokens.update(_tokenize_grounding_text(value))
    return len(requirement_tokens & candidate_tokens)


def _source_url(source: Any) -> str:
    return str(getattr(source, "url", "") or "").strip()


def _select_grounded_source(requirement: Requirement, context: Optional[Any]) -> Any | None:
    all_sources = [source for source in _grounded_sources(context) if getattr(source, "id", None)]
    elements = _grounded_ui_elements(context)
    sources = [
        source
        for source in all_sources
        if _source_url(source)
        or any(str(getattr(element, "source_id", "") or "") == str(getattr(source, "id", "") or "") for element in elements)
    ]
    if not sources:
        return None

    requirement_tokens = _tokenize_grounding_text(requirement.text)

    def score(source: Any) -> int:
        source_id = str(getattr(source, "id", "") or "")
        related_elements = [element for element in elements if str(getattr(element, "source_id", "") or "") == source_id]
        values: List[Any] = [
            source_id,
            getattr(source, "label", ""),
            _source_url(source),
            getattr(source, "notes", ""),
        ]
        for element in related_elements:
            values.extend([getattr(element, "name", ""), getattr(element, "description", ""), getattr(element, "href", "")])
        return _score_grounded_text(requirement_tokens, *values)

    return max(sources, key=score)


def _elements_for_source(context: Optional[Any], source_id: str, element_type: str) -> List[Any]:
    return [
        element
        for element in _grounded_ui_elements(context)
        if str(getattr(element, "source_id", "") or "") == source_id
        and str(getattr(element, "element_type", "") or "") == element_type
    ]


def _select_grounded_element(requirement: Requirement, elements: List[Any]) -> Any | None:
    if not elements:
        return None
    requirement_tokens = _tokenize_grounding_text(requirement.text)
    return max(
        elements,
        key=lambda element: _score_grounded_text(
            requirement_tokens,
            getattr(element, "name", ""),
            getattr(element, "description", ""),
            getattr(element, "href", ""),
        ),
    )


def _fallback_context_url(context: Optional[Any]) -> str:
    for attr_name in ("app_link", "prototype_link"):
        value = getattr(context, attr_name, None) if context else None
        if value:
            return str(value)
    return ""


def _grounded_browser_step_details(requirement: Requirement, context: Optional[Any]) -> Dict[str, Any] | None:
    source = _select_grounded_source(requirement, context)
    if not source:
        return None

    source_id = str(getattr(source, "id", "") or "").strip()
    if not source_id:
        return None

    headings = _elements_for_source(context, source_id, "Heading")
    navigation_links = [element for element in _elements_for_source(context, source_id, "Navigation") if getattr(element, "href", None)]
    heading = _select_grounded_element(requirement, headings)
    navigation = _select_grounded_element(requirement, navigation_links)
    source_url = _source_url(source)
    target_url = str(source_url or getattr(navigation, "href", "") or _fallback_context_url(context)).strip()

    if not target_url and not heading:
        return None

    assertion_text = str(getattr(heading, "name", "") or getattr(navigation, "name", "") or "").strip()
    if not assertion_text:
        return None

    source_label = str(getattr(source, "label", "") or "Grounded artifact").strip()
    return {
        "source_id": source_id,
        "source_label": source_label,
        "target_url": target_url,
        "assertion_text": assertion_text,
        "navigation_name": str(getattr(navigation, "name", "") or "").strip(),
    }


def _fallback_steps_for_grounded_browser_requirement(
    requirement: Requirement,
    scenario_type: str,
    context: Optional[Any],
) -> List[Dict[str, Any]] | None:
    details = _grounded_browser_step_details(requirement, context)
    if not details:
        return None

    assertion_text = details["assertion_text"]
    target_url = details["target_url"]
    steps: List[Dict[str, Any]] = []
    if target_url:
        steps.append(
            {
                "step": 1,
                "action": f"Open {target_url}",
                "expected": f'heading "{assertion_text}" is visible',
                "test_data": f"source:{details['source_id']}",
            }
        )

    steps.append(
        {
            "step": len(steps) + 1,
            "action": f'Record the exact grounded heading "{assertion_text}".',
            "expected": f'heading "{assertion_text}" is visible',
            "test_data": None,
        }
    )

    if details.get("navigation_name") and target_url:
        steps.append(
            {
                "step": len(steps) + 1,
                "action": f'Record the grounded navigation reference "{details["navigation_name"]}".',
                "expected": "The real accessible link name is documented for traceability without inventing a label.",
                "test_data": f"href:{target_url}",
            }
        )

    steps.append(
        {
            "step": len(steps) + 1,
            "action": "Record the observed browser behavior for this scenario.",
            "expected": f"The observed page behavior supports the {scenario_type.lower()} coverage objective for {requirement.id}.",
            "test_data": None,
        }
    )
    return steps


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
            grounded_details = _grounded_browser_step_details(requirement, context)
            grounded_steps = _fallback_steps_for_grounded_browser_requirement(requirement, scenario_type, context)
            source_refs = [grounded_details["source_id"]] if grounded_details else grounded_source_refs
            component = grounded_details["source_label"] if grounded_details else "General"
            expected_result = (
                f'Exact grounded browser text "{grounded_details["assertion_text"]}" is verified for {requirement.id}.'
                if grounded_details
                else f"Requirement {requirement.id} is satisfied for the planned {scenario_type.lower()} scenario."
            )
            raw_test_cases.append(
                {
                    "id": f"TC-{len(raw_test_cases) + 1:03d}",
                    "title": str(scenario.get("title") or f"{scenario_type} validation for {requirement.id}"),
                    "description": str(scenario.get("objective") or f"Verify that {requirement.text[:100]}"),
                    "priority": _normalize_priority(scenario.get("priority")),
                    "type": "Functional",
                    "status": "Draft",
                    "preconditions": context.notes if context else None,
                    "steps": grounded_steps or [
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
                    "expected_result": expected_result,
                    "test_data": None,
                    "estimated_time": "5 mins",
                    "automation_status": "To Be Automated",
                    "component": component,
                    "tags": [requirement.id, _scenario_tag(scenario_type), "generated", f"plan:{idx:02d}-{scenario_offset:02d}"],
                    "linked_requirement_ids": [requirement.id],
                    "scenario_refs": [scenario_id],
                    "source_refs": source_refs,
                }
            )
    return raw_test_cases


def _covered_requirement_scenario_pairs(
    test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
) -> set[tuple[str, str]]:
    requirement_id_set = set(_serialize_requirement_ids(requirements))
    covered_pairs: set[tuple[str, str]] = set()

    for test_case in test_cases:
        linked_requirements = set(_extract_linked_requirement_ids_from_test_case(test_case, requirement_id_set))
        if not linked_requirements:
            continue

        scenario_types = _extract_scenario_types_from_test_case(test_case)
        for requirement_id in linked_requirements:
            for scenario_type in scenario_types:
                covered_pairs.add((requirement_id, scenario_type))

    return covered_pairs


def _assign_recovery_case_id(test_case: Dict[str, Any], used_ids: set[str], sequence: int) -> None:
    candidate_id = str(test_case.get("id") or "").strip()
    if candidate_id and candidate_id not in used_ids:
        used_ids.add(candidate_id)
        return

    while True:
        candidate_id = f"TC-FB-{sequence:03d}"
        sequence += 1
        if candidate_id not in used_ids:
            test_case["id"] = candidate_id
            used_ids.add(candidate_id)
            return


def _augment_with_fallback_coverage(
    test_cases: List[Dict[str, Any]],
    requirements: List[Requirement],
    context: Optional[Any],
    coverage_plan: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    augmented_cases = [dict(test_case) for test_case in test_cases]
    covered_pairs = _covered_requirement_scenario_pairs(augmented_cases, requirements)
    used_ids = {str(test_case.get("id") or "").strip() for test_case in augmented_cases if str(test_case.get("id") or "").strip()}
    fallback_cases = _fallback_raw_test_cases(requirements, context, coverage_plan=coverage_plan)

    for sequence, fallback_case in enumerate(fallback_cases, start=1):
        fallback_pairs = _covered_requirement_scenario_pairs([fallback_case], requirements)
        if fallback_pairs and fallback_pairs.issubset(covered_pairs):
            continue

        recovered_case = dict(fallback_case)
        _assign_recovery_case_id(recovered_case, used_ids, sequence)
        augmented_cases.append(recovered_case)
        covered_pairs.update(fallback_pairs)

    return augmented_cases
