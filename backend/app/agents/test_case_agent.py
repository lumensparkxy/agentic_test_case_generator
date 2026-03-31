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
from ..models import GenerateTestCasesInput, RefineTestCasesInput, Requirement, TestCase, TestStep
from ..utils.llm_json import parse_review_json, parse_test_cases_json

STATE_TEST_CASES = "current_test_cases"
STATE_VALIDATION_FEEDBACK = "validation_feedback"

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


def _heuristic_test_case_review(test_cases: List[Dict[str, Any]], requirements: List[Requirement], threshold: int) -> Dict[str, Any]:
    metrics = _compute_test_case_coverage_metrics(test_cases, requirements)
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

**Requirements:**
{requirements_text}
{feedback_section}
**Quality Checklist:**
1. Each test case has a clear title and meaningful description.
2. Steps are executable and expected results are specific.
3. Requirement traceability tags cover every requirement.
4. Priority, type, status, and automation status are valid.
5. Test data, preconditions, and overall expected_result are present when needed.
6. Human feedback has been addressed.

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
{feedback_section}
**Rules:**
1. Generate 1-3 test cases per requirement.
2. Tag each test case with at least one requirement ID.
3. Include detailed steps, expected results, realistic priorities, and execution metadata.
4. Cover positive, negative, and edge scenarios where appropriate.
5. Output ONLY a JSON object shaped like {{"test_cases": [...]}}.
""",
        description="Generates initial test cases from approved requirements",
        output_key=STATE_TEST_CASES,
    )

    return SequentialAgent(
        name="TestCaseGenerationPipeline",
        sub_agents=[generator_agent, _build_review_loop(model, threshold, 4, requirements_text, human_feedback=human_feedback)],
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

Rules:
1. Preserve good test cases and improve weak ones.
2. Add, merge, split, or remove cases as needed.
3. Keep requirement traceability intact or improve it.
4. Output ONLY the JSON object.
""",
        description="Applies human feedback to an existing test-case set before re-validation",
        output_key=STATE_TEST_CASES,
    )

    return SequentialAgent(
        name="TestCaseRefinementPipeline",
        sub_agents=[refinement_agent, _build_review_loop(model, threshold, 4, requirements_text, human_feedback=human_feedback)],
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
        },
    )

    current_test_cases: List[Dict[str, Any]] = []
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

    state_review = parse_review_json(session.state.get(STATE_VALIDATION_FEEDBACK, ""), default_threshold=threshold)
    if state_review:
        model_review = _normalize_review_result(state_review, threshold, "Test case validation completed.")

    heuristic_review = _heuristic_test_case_review(current_test_cases, requirements, threshold)
    final_review = _merge_review_results(model_review, heuristic_review)
    coverage_metrics = _compute_test_case_coverage_metrics(current_test_cases, requirements)

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
        return {
            "test_cases": [],
            "approved": False,
            "review": _heuristic_test_case_review([], requirements, DEFAULT_TEST_CASE_THRESHOLD),
            "iteration_history": [],
            "coverage_metrics": _compute_test_case_coverage_metrics([], requirements),
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


def _fallback_raw_test_cases(requirements: List[Requirement], context: Optional[Any]) -> List[Dict[str, Any]]:
    raw_test_cases: List[Dict[str, Any]] = []
    for idx, req in enumerate(requirements, start=1):
        raw_test_cases.append(
            {
                "id": f"TC-{idx:03d}",
                "title": f"Validate {req.text[:60]}",
                "description": f"Verify that {req.text[:100]}",
                "priority": "Medium",
                "type": "Functional",
                "status": "Draft",
                "preconditions": context.notes if context else None,
                "steps": [
                    {"step": 1, "action": f"Navigate to feature area for {req.id}", "expected": "Target page or control is available", "test_data": None},
                    {"step": 2, "action": f"Perform the behavior described by {req.id}", "expected": "The requirement is satisfied", "test_data": None},
                ],
                "expected_result": f"Requirement {req.id} is satisfied without defects.",
                "test_data": None,
                "estimated_time": "5 mins",
                "automation_status": "To Be Automated",
                "component": "General",
                "tags": [req.id, "generated"],
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


def _build_response(test_cases: List[TestCase], workflow: Dict[str, Any], requirements: List[Requirement]) -> Dict[str, Any]:
    serialized = _serialize_test_cases(test_cases)
    coverage_metrics = dict(workflow.get("coverage_metrics") or _compute_test_case_coverage_metrics(serialized, requirements))
    review = dict(workflow.get("review") or _heuristic_test_case_review(serialized, requirements, DEFAULT_TEST_CASE_THRESHOLD))
    approved = bool(workflow.get("approved", False))

    return {
        "test_cases": test_cases,
        "approved": approved,
        "review": review,
        "iteration_history": list(workflow.get("iteration_history") or []),
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
    if not raw_test_cases:
        logging.warning("[TestCase Workflow] No test cases from pipeline, using deterministic fallback")
        raw_test_cases = _fallback_raw_test_cases(payload.requirements, payload.context)
        fallback_review = _heuristic_test_case_review(raw_test_cases, payload.requirements, DEFAULT_TEST_CASE_THRESHOLD)
        workflow = {
            "test_cases": raw_test_cases,
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
            "coverage_metrics": _compute_test_case_coverage_metrics(raw_test_cases, payload.requirements),
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
    if not workflow.get("test_cases"):
        logging.warning("[TestCase Workflow] Refinement returned no test cases, restoring previous set")
        fallback_review = _heuristic_test_case_review(raw_test_cases, payload.requirements, DEFAULT_TEST_CASE_THRESHOLD)
        fallback_review["approved"] = False
        fallback_review["summary"] = "Test case refinement returned no updated output. Previous test cases were restored and require further review."
        fallback_review["blocking_issues"] = _dedupe_preserve(
            fallback_review["blocking_issues"] + ["Refinement loop did not return an updated test-case set."]
        )
        workflow = {
            "test_cases": raw_test_cases,
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
            "coverage_metrics": _compute_test_case_coverage_metrics(raw_test_cases, payload.requirements),
        }

    test_cases = _hydrate_test_cases(raw_test_cases)
    return _build_response(test_cases, workflow, payload.requirements)
