"""Hydration helpers for normalized test-case workflow payloads."""

import logging
import re
from typing import Any, Dict, List

from pydantic import ValidationError

from .analysis_agent import fallback_requirement_analysis, normalize_requirement_analysis
from .test_case_coverage import (
    _coerce_bool,
    _dedupe_preserve,
    _extract_linked_requirement_ids_from_test_case,
    _extract_scenario_refs_from_test_case,
    _fallback_coverage_plan,
    _normalize_coverage_plan,
    _normalize_automation_status,
    _normalize_priority,
    _normalize_scenario_type,
    _normalize_source_refs,
    _normalize_status,
    _normalize_string_list,
    _normalize_test_case_type,
)
from ..models import (
    BusinessRule,
    FieldConstraint,
    Requirement,
    RequirementAnalysis,
    RequirementCoveragePlan,
    RiskSignal,
    RolePermission,
    ScenarioIntent,
    StateTransition,
    TestCase,
    TestStep,
)

STEP_TEXT_PREFIX_PATTERN = re.compile(r"^\s*(?:step\s*)?\d+[\).:-]\s*", re.IGNORECASE)
STEP_TEXT_MARKER_PATTERN = re.compile(r"(?:^|\n)\s*(?:step\s*)?\d+[\).:-]\s*", re.IGNORECASE)
STEP_BULLET_MARKER_PATTERN = re.compile(r"(?:^|\n)\s*[-*\u2022]\s+")

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
    cleaned = re.sub(r"^\s*[-*\u2022]\s+", "", cleaned)
    cleaned = re.sub(r"^\s*action\s*[:\-]\s*", "", cleaned, flags=re.IGNORECASE)

    action = cleaned
    expected = ""

    for separator in ("->", "=>", "\u2192"):
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
