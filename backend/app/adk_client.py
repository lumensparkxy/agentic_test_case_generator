"""
ADK Client - Multi-agent requirement extraction and refinement using Google ADK.

Implements reviewer/refiner loops with structured review outputs so the UI can
gate progression on explicit approval thresholds rather than implied success.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .utils.llm_json import extract_json, parse_requirements_json, parse_review_json

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_REQUIREMENT_THRESHOLD = 85

STATE_REQUIREMENTS = "current_requirements"
STATE_REVIEW_FEEDBACK = "review_feedback"

APPROVAL_PHRASE = "APPROVED"


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


def _compute_requirement_coverage_metrics(requirements: List[Dict[str, str]], document_count: int) -> Dict[str, Any]:
    total = len(requirements)
    unique_texts = {str(item.get("text", "")).strip().lower() for item in requirements if item.get("text")}
    shall_format_count = sum(
        1 for item in requirements if str(item.get("text", "")).strip().lower().startswith("the system shall")
    )
    average_word_count = round(
        sum(len(str(item.get("text", "")).split()) for item in requirements) / total,
        2,
    ) if total else 0.0

    return {
        "document_count": max(1, document_count),
        "total_requirements": total,
        "unique_requirements": len(unique_texts),
        "duplicate_requirements": max(0, total - len(unique_texts)),
        "shall_format_count": shall_format_count,
        "shall_format_ratio": round(shall_format_count / total, 2) if total else 0.0,
        "average_word_count": average_word_count,
        "requirements_per_document": round(total / max(1, document_count), 2),
    }


def _heuristic_requirement_review(
    requirements: List[Dict[str, str]],
    threshold: int,
    document_count: int,
) -> Dict[str, Any]:
    metrics = _compute_requirement_coverage_metrics(requirements, document_count)
    blocking_issues: List[str] = []
    suggestions: List[str] = []
    unmet_criteria: List[str] = []
    score = 100

    if metrics["total_requirements"] == 0:
        blocking_issues.append("No requirements were extracted from the provided documents.")
        unmet_criteria.append("Extract at least one testable requirement.")
        score = 0
    else:
        missing_shall = metrics["total_requirements"] - metrics["shall_format_count"]
        if missing_shall > 0:
            blocking_issues.append(
                f"{missing_shall} requirement(s) are not in the required 'The system shall...' format."
            )
            unmet_criteria.append("Normalize all requirements into a consistent, testable format.")
            score -= missing_shall * 10

        if metrics["duplicate_requirements"] > 0:
            blocking_issues.append(
                f"{metrics['duplicate_requirements']} duplicate requirement(s) were detected."
            )
            unmet_criteria.append("Remove duplicate or overlapping requirements.")
            score -= metrics["duplicate_requirements"] * 8

        if metrics["average_word_count"] < 8:
            suggestions.append("Add more specificity to short requirements so they are easier to test.")
            score -= 5

        if metrics["requirements_per_document"] < 2:
            suggestions.append("Consider whether each document has enough extracted coverage.")
            score -= 3

    score = max(0, min(100, score))
    approved = score >= threshold and not blocking_issues
    summary = (
        "Requirements meet the current quality threshold."
        if approved
        else "Requirements still need refinement before the workflow can move forward."
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


def _make_history_entry(iteration: int, actor: str, review: Dict[str, Any], requirements: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "iteration": iteration,
        "actor": actor,
        "approved": review["approved"],
        "score": review["score"],
        "threshold": review["threshold"],
        "summary": review["summary"],
        "artifact_count": len(requirements),
        "artifact_ids": [str(item.get("id", "")) for item in requirements[:8] if item.get("id")],
        "blocking_issues": list(review["blocking_issues"]),
        "suggestions": list(review["suggestions"]),
    }


def exit_loop(tool_context: ToolContext) -> dict:
    """Call this function when the requirements are approved and meet quality standards."""
    logging.info("[exit_loop] Requirements approved - exiting refinement loop")
    tool_context.actions.escalate = True
    tool_context.actions.skip_summarization = True
    return {"status": "approved", "message": "Requirements approved"}


def _build_review_loop(model: str, threshold: int, max_iterations: int, human_feedback: Optional[str] = None) -> LoopAgent:
    feedback_section = ""
    if human_feedback:
        feedback_section = f"""
**Human Feedback That Must Be Honored:**
{human_feedback}
"""

    reviewer_agent = Agent(
        name="ReviewerAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a Quality Assurance Lead reviewing software requirements for testability.

**Current Requirements to Review:**
```
{{{STATE_REQUIREMENTS}}}
```
{feedback_section}
**Quality Checklist:**
1. Every requirement is testable and verifiable.
2. Every requirement uses the format 'The system shall...'.
3. Requirements are specific, non-duplicative, and free of implementation details.
4. Any human feedback has been addressed.
5. The set is strong enough to move into test-case generation.

**Response Rules:**
- Return ONLY a JSON object with this exact shape:
{{
  "approved": true,
  "score": 92,
  "threshold": {threshold},
  "summary": "Brief explanation.",
  "blocking_issues": [],
  "suggestions": [],
  "unmet_criteria": []
}}
- Set approved=true ONLY when score >= {threshold} and there are no blocking_issues.
- Keep blocking_issues and suggestions concise.
""",
        description="Reviews requirements for quality and threshold readiness",
        output_key=STATE_REVIEW_FEEDBACK,
    )

    refiner_agent = Agent(
        name="RefinerAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a Business Analyst refining or finalizing requirements.

**Current Requirements:**
```
{{{STATE_REQUIREMENTS}}}
```

**Structured Review Result:**
{{{STATE_REVIEW_FEEDBACK}}}
{feedback_section}
**Your Task:**
1. If the review JSON indicates approved=true, score >= threshold, and blocking_issues is empty, call 'exit_loop' immediately.
2. Otherwise, revise the requirements to address all blocking issues, unmet criteria, and suggestions.
3. Preserve good requirements, renumber sequentially as REQ-001, REQ-002, etc., and output ONLY the refined JSON array.

Either call exit_loop OR output the refined JSON array. Never add commentary.
""",
        description="Refines requirements based on structured reviewer feedback",
        tools=[exit_loop],
        output_key=STATE_REQUIREMENTS,
    )

    return LoopAgent(
        name="RefinementLoop",
        sub_agents=[reviewer_agent, refiner_agent],
        max_iterations=max(1, max_iterations),
    )


def _build_requirement_extraction_pipeline(model: str, max_iterations: int, threshold: int) -> Agent:
    initial_extractor = Agent(
        name="InitialExtractorAgent",
        model=model,
        include_contents='default',
        instruction="""You are a Senior Business Analyst specializing in requirements engineering.

**Your Task:** Analyze the document content in the user message and extract TESTABLE functional requirements.

**Rules:**
1. Each requirement must be a complete, testable statement.
2. Use the format 'The system shall...' consistently.
3. Exclude code snippets, file paths, infrastructure notes, and implementation details.
4. Merge obvious duplicates and keep the set concise.
5. Output ONLY a JSON array like:
[
  {"id": "REQ-001", "text": "The system shall ..."}
]
""",
        description="Extracts initial requirements from source documents",
        output_key=STATE_REQUIREMENTS,
    )

    return SequentialAgent(
        name="RequirementExtractionPipeline",
        sub_agents=[initial_extractor, _build_review_loop(model, threshold, max_iterations)],
        description="Extracts and iteratively refines requirements from documents",
    )


def _build_requirement_refinement_pipeline(
    model: str,
    max_iterations: int,
    threshold: int,
    human_feedback: str,
) -> Agent:
    initial_refiner = Agent(
        name="HumanFeedbackRefinerAgent",
        model=model,
        include_contents='default',
        instruction="""You are a Senior Business Analyst revising an existing requirement set.

Use the existing requirements and human feedback in the user message to produce an improved JSON array of requirements.

Rules:
1. Apply all human feedback.
2. Keep requirements in the format 'The system shall...'.
3. Add, merge, split, or delete requirements as needed.
4. Renumber sequentially as REQ-001, REQ-002, etc.
5. Output ONLY the refined JSON array.
""",
        description="Applies human feedback to the existing requirement set before re-review",
        output_key=STATE_REQUIREMENTS,
    )

    return SequentialAgent(
        name="RequirementRefinementPipeline",
        sub_agents=[
            initial_refiner,
            _build_review_loop(model, threshold, max_iterations, human_feedback=human_feedback),
        ],
        description="Refines an existing requirement set and re-validates it against the approval threshold",
    )


async def _run_requirement_workflow_async(
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 3,
    document_text: Optional[str] = None,
    existing_requirements: Optional[List[Dict[str, Any]]] = None,
    human_feedback: Optional[str] = None,
    document_count: int = 1,
) -> Dict[str, Any]:
    threshold = DEFAULT_REQUIREMENT_THRESHOLD
    safe_iterations = max(1, max_iterations)

    is_refinement = bool(existing_requirements is not None)
    if is_refinement:
        root_agent = _build_requirement_refinement_pipeline(
            model,
            safe_iterations,
            threshold,
            human_feedback or "No human feedback provided.",
        )
        message_text = f"""Refine these existing requirements using the human feedback.

Existing requirements JSON:
{json.dumps(existing_requirements or [], indent=2)}

Human feedback:
{human_feedback or 'No human feedback provided.'}
"""
    else:
        root_agent = _build_requirement_extraction_pipeline(model, safe_iterations, threshold)
        message_text = f"""Please extract and refine testable requirements from these document contents.

---DOCUMENT START---
{document_text or ''}
---DOCUMENT END---
"""

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="requirement_extractor",
        session_service=session_service,
    )

    user_id = f"user_{uuid.uuid4().hex[:8]}"
    session = await session_service.create_session(
        app_name="requirement_extractor",
        user_id=user_id,
        state={
            STATE_REQUIREMENTS: "[]",
            STATE_REVIEW_FEEDBACK: "",
        },
    )

    current_requirements: List[Dict[str, str]] = []
    iteration_history: List[Dict[str, Any]] = []
    model_review: Optional[Dict[str, Any]] = None

    logging.info("[Requirement Workflow] Starting session %s", session.id)

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message_text)]),
    ):
        author = getattr(event, 'author', 'unknown')
        logging.info("[Requirement Workflow] Event from %s", author)

        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            text = getattr(part, 'text', None)
            if not text:
                continue

            parsed_requirements = parse_requirements_json(text)
            if parsed_requirements and author in {"InitialExtractorAgent", "HumanFeedbackRefinerAgent", "RefinerAgent"}:
                current_requirements = parsed_requirements

            parsed_review = parse_review_json(text, default_threshold=threshold)
            if parsed_review and author == "ReviewerAgent":
                model_review = _normalize_review_result(parsed_review, threshold, "Requirement review completed.")
                iteration_history.append(
                    _make_history_entry(
                        iteration=len(iteration_history) + 1,
                        actor=author,
                        review=model_review,
                        requirements=current_requirements,
                    )
                )

    state_requirements = parse_requirements_json(session.state.get(STATE_REQUIREMENTS, "[]"))
    if state_requirements:
        current_requirements = state_requirements

    state_review = parse_review_json(session.state.get(STATE_REVIEW_FEEDBACK, ""), default_threshold=threshold)
    if state_review:
        model_review = _normalize_review_result(state_review, threshold, "Requirement review completed.")

    heuristic_review = _heuristic_requirement_review(current_requirements, threshold, document_count)
    final_review = _merge_review_results(model_review, heuristic_review)
    coverage_metrics = _compute_requirement_coverage_metrics(current_requirements, document_count)

    if iteration_history:
        iteration_history[-1] = _make_history_entry(
            iteration=iteration_history[-1]["iteration"],
            actor=iteration_history[-1]["actor"],
            review=final_review,
            requirements=current_requirements,
        )
    else:
        iteration_history.append(
            _make_history_entry(
                iteration=1,
                actor="HeuristicReview",
                review=final_review,
                requirements=current_requirements,
            )
        )

    logging.info("[Requirement Workflow] Final requirements: %s, approved=%s", len(current_requirements), final_review["approved"])
    return {
        "requirements": current_requirements,
        "approved": final_review["approved"],
        "review": final_review,
        "iteration_history": iteration_history,
        "coverage_metrics": coverage_metrics,
    }


def _run_requirement_workflow_sync(**kwargs: Any) -> Dict[str, Any]:
    try:
        try:
            asyncio.get_running_loop()
            import nest_asyncio

            nest_asyncio.apply()
            return asyncio.run(_run_requirement_workflow_async(**kwargs))
        except RuntimeError:
            return asyncio.run(_run_requirement_workflow_async(**kwargs))
    except Exception as exc:
        logging.error("[Requirement Workflow] Error: %s", exc)
        document_count = kwargs.get("document_count", 1)
        return {
            "requirements": [],
            "approved": False,
            "review": _heuristic_requirement_review([], DEFAULT_REQUIREMENT_THRESHOLD, document_count),
            "iteration_history": [],
            "coverage_metrics": _compute_requirement_coverage_metrics([], document_count),
        }


def run_requirement_extraction_workflow_sync(
    document_text: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 3,
    document_count: int = 1,
) -> Dict[str, Any]:
    return _run_requirement_workflow_sync(
        document_text=document_text,
        model=model,
        max_iterations=max_iterations,
        document_count=document_count,
    )


def run_requirement_refinement_workflow_sync(
    existing_requirements: List[Dict[str, Any]],
    feedback: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 3,
) -> Dict[str, Any]:
    return _run_requirement_workflow_sync(
        existing_requirements=existing_requirements,
        human_feedback=feedback,
        model=model,
        max_iterations=max_iterations,
        document_count=1,
    )


def run_requirement_extraction_loop_sync(
    document_text: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 3,
) -> List[Dict[str, str]]:
    result = run_requirement_extraction_workflow_sync(document_text, model, max_iterations)
    return result.get("requirements", [])


def run_requirement_refinement_sync(
    existing_requirements: List[Dict[str, Any]],
    feedback: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 3,
) -> List[Dict[str, str]]:
    result = run_requirement_refinement_workflow_sync(existing_requirements, feedback, model, max_iterations)
    return result.get("requirements", [])


def run_adk_prompt(
    *,
    prompt: str,
    model: str,
    agent_name: str,
    instruction: str,
) -> str:
    """Legacy API - Run a single prompt (for backward compatibility)."""
    from google import genai

    try:
        with genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", "")) as client:
            response = client.models.generate_content(
                model=model or DEFAULT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=instruction),
            )
            return response.text.strip() if response and response.text else ""
    except Exception as exc:
        logging.error("[%s] Error: %s", agent_name, exc)
        return ""


def run_adk_json(
    *,
    prompt: str,
    model: str,
    agent_name: str,
    instruction: str,
) -> Optional[object]:
    """Legacy API - Run a prompt and parse JSON response."""
    text = run_adk_prompt(prompt=prompt, model=model, agent_name=agent_name, instruction=instruction)
    json_text = extract_json(text)
    if not json_text:
        return None
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None
