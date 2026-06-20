"""
Test Case Generation Agent - Multi-agent loop using Google ADK.

Implements thresholded validation results, iteration history, and a dedicated
refine-existing-test-cases path so the UI can gate export on explicit approval.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .adk_runtime import json_generation_config, tool_generation_config
from .analysis_agent import build_requirement_analysis_agent, fallback_requirement_analysis, normalize_requirement_analysis
from .prompting import REAL_WORLD_QA_POLICY, TEST_DESIGN_PROMPT_GUARDRAILS, human_feedback_section
from ..config import get_settings
from ..models import (
    GenerateTestCasesInput,
    RefineTestCasesInput,
    Requirement,
    ReviewResult,
    TestCase,
    TestCasesOutput,
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
    _fallback_coverage_plan,
    _normalize_coverage_plan,
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
    *,
    retryable: bool = False,
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
) -> None:
    if not error:
        return
    message = f"{author}: {error}"
    sample = _diagnostic_sample(raw_text)
    if sample:
        message = f"{message} | sample: {sample}"
    _append_unique_message(diagnostics["parser_failures"], message)
    if diagnostics["status"] == "completed":
        diagnostics["status"] = "partial"
    _append_unique_message(diagnostics["warnings"], f"{author}: recovered usable coverage-plan JSON from malformed output.")
    _log_test_case_workflow(
        "parser_recovery",
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
5. Every must-have scenario from the coverage plan is represented by at least one test case.
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
        include_contents="default",
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

            if not event.content or not event.content.parts:
                continue

            for part in event.content.parts:
                text = getattr(part, "text", None)
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
                        _record_parser_recovery(diagnostics, author, parse_error, text)
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
    if state_test_cases:
        current_test_cases = state_test_cases
    elif str(state_test_cases_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateTestCases", state_test_cases_error, state_test_cases_raw)

    state_coverage_plan_raw = session_state.get(STATE_COVERAGE_PLAN, "[]")
    state_coverage_plan, state_coverage_plan_error = parse_coverage_plan_json_detailed(state_coverage_plan_raw)
    had_event_coverage_plan = bool(current_coverage_plan)
    if state_coverage_plan:
        current_coverage_plan = _normalize_coverage_plan(state_coverage_plan, requirements)
        if not had_event_coverage_plan:
            _record_parser_recovery(diagnostics, "SessionStateCoveragePlan", state_coverage_plan_error, state_coverage_plan_raw)
    elif str(state_coverage_plan_raw).strip() not in {"", "[]"}:
        _record_parser_failure(diagnostics, "SessionStateCoveragePlan", state_coverage_plan_error, state_coverage_plan_raw)

    state_requirement_analysis_raw = session_state.get(STATE_REQUIREMENT_ANALYSIS, "[]")
    state_requirement_analysis, state_requirement_analysis_error = parse_requirement_analysis_json_detailed(state_requirement_analysis_raw)
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

    return {
        "test_cases": test_cases,
        "approved": approved,
        "review": review,
        "iteration_history": list(workflow.get("iteration_history") or []),
        "coverage_plan": coverage_plan,
        "requirement_analysis": requirement_analysis,
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": public_workflow_diagnostics(dict(workflow.get("workflow_diagnostics") or {})),
    }


def generate_test_cases(
    payload: GenerateTestCasesInput,
    actor_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    settings = _get_model_settings_or_none()
    requirements_text, context_text, template_text = _prepare_workflow_inputs(payload.requirements, payload.context, payload.template)

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
                "warnings": ["Model credentials are unavailable; deterministic fallback was used."],
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
        record_agent_fallback(workflow=operation or "testcases.generate", reason="fallback_generated_artifacts")
        raw_test_cases = _fallback_raw_test_cases(payload.requirements, payload.context, coverage_plan=coverage_plan)
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
        _append_unique_message(
            workflow_diagnostics["warnings"],
            "Test-case fallback produced deterministic draft artifacts because the generation workflow returned no test cases.",
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
        }
    elif not bool(workflow.get("approved", False)):
        recovery_test_cases = _augment_with_fallback_coverage(
            raw_test_cases,
            payload.requirements,
            payload.context,
            coverage_plan,
        )
        if len(recovery_test_cases) > len(raw_test_cases):
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
                record_agent_fallback(workflow=operation or "testcases.generate", reason="quality_recovery")
                raw_test_cases = recovery_test_cases
                workflow_diagnostics = {**_new_workflow_diagnostics(), **dict(workflow.get("workflow_diagnostics") or {})}
                workflow_diagnostics["status"] = "fallback"
                workflow_diagnostics["used_fallback"] = True
                workflow_diagnostics["failure_reason"] = "quality_recovery"
                _append_unique_message(
                    workflow_diagnostics["warnings"],
                    "Rejected model output was augmented with deterministic fallback cases to restore requirement and scenario coverage.",
                )
                iteration_history = list(workflow.get("iteration_history") or [])
                iteration_history.append(
                    _make_history_entry(
                        iteration=len(iteration_history) + 1,
                        actor="FallbackCoverageRecovery",
                        review=recovery_review,
                        test_cases=recovery_test_cases,
                    )
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
