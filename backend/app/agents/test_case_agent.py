"""
Test Case Generation Agent - Multi-agent loop using Google ADK.

Pipeline:
1. TestCaseGeneratorAgent - generates test cases from requirements
2. TestCaseValidatorAgent - validates quality, coverage, and completeness
3. Loop until validator approves or max iterations reached
"""
import asyncio
import json
import logging
import os
import uuid
from typing import List, Optional, Dict, Any

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import ValidationError

from ..models import GenerateTestCasesInput, TestCase, TestStep
from ..config import get_settings

# State keys
STATE_TEST_CASES = "current_test_cases"
STATE_VALIDATION_FEEDBACK = "validation_feedback"

APPROVAL_PHRASE = "APPROVED"


def _extract_json(text: str) -> Optional[str]:
    """Extract JSON from text that may contain markdown fences."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    if text.startswith("{") or text.startswith("["):
        return text
    start = min(
        [pos for pos in [text.find("{"), text.find("[")] if pos != -1],
        default=-1,
    )
    if start == -1:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    if end == -1:
        return None
    return text[start : end + 1]


def _parse_test_cases_json(text: str) -> List[Dict[str, Any]]:
    """Parse test cases from agent response."""
    if not text:
        return []
    
    json_text = _extract_json(text)
    if not json_text:
        return []
    
    try:
        data = json.loads(json_text)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "test_cases" in data:
            return data["test_cases"]
    except json.JSONDecodeError:
        pass
    
    return []


def exit_loop(tool_context: ToolContext) -> dict:
    """Call this function when test cases are validated and approved."""
    logging.info("[exit_loop] Test cases approved - exiting validation loop")
    tool_context.actions.escalate = True
    tool_context.actions.skip_summarization = True
    return {"status": "approved", "message": "Test cases approved"}


def _build_test_case_pipeline(
    model: str, 
    requirements_text: str, 
    context_text: str, 
    template_text: str,
    human_feedback: Optional[str] = None
) -> Agent:
    """Build a multi-agent pipeline for test case generation using google-adk."""
    
    feedback_section = ""
    if human_feedback:
        feedback_section = f"""
**IMPORTANT - Human Feedback to Address:**
{human_feedback}

You MUST address all points in this feedback when generating/refining test cases.
"""
    
    # === Initial Generator Agent ===
    generator_agent = Agent(
        name="TestCaseGeneratorAgent",
        model=model,
        include_contents='default',
        instruction=f"""You are a Senior QA Engineer specializing in test case design following industry standards (JIRA/Xray/TestRail).

**Your Task:** Generate comprehensive, executable test cases from the requirements provided.
{feedback_section}
**Requirements to Test:**
{requirements_text}

**Context:**
{context_text}

**Template Configuration:**
{template_text}

**Rules for Test Cases:**
1. Each test case MUST be traceable to one or more requirements (include requirement IDs in tags)
2. Test cases should be ATOMIC - test one specific behavior per case
3. Steps must be clear, actionable, and include specific expected results
4. Include test data where needed (credentials, sample inputs, etc.)
5. Cover positive, negative, and edge cases where applicable
6. Assign appropriate priority based on business impact
7. Set correct type (Functional, Integration, E2E, Regression, Smoke, etc.)
8. Estimate execution time realistically

**Output Format:**
Return ONLY a valid JSON object with industry-standard fields:
{{
  "test_cases": [
    {{
      "id": "TC-001",
      "title": "Verify user can upload .docx requirements file",
      "description": "Validates that the system accepts and processes Word documents for requirement extraction",
      "priority": "High",
      "type": "Functional",
      "status": "Draft",
      "preconditions": "User is logged in and on the Upload page",
      "steps": [
        {{"step": 1, "action": "Click the 'Choose File' button", "expected": "File picker dialog opens", "test_data": null}},
        {{"step": 2, "action": "Select a .docx file (test_requirements.docx)", "expected": "File name appears in the upload field", "test_data": "test_requirements.docx (5 requirements)"}},
        {{"step": 3, "action": "Click 'Parse Requirements' button", "expected": "Loading indicator appears, then requirements list displays", "test_data": null}}
      ],
      "expected_result": "Requirements are extracted and displayed in a structured list format",
      "test_data": "Valid .docx file with 5 requirement statements",
      "estimated_time": "5 mins",
      "automation_status": "To Be Automated",
      "component": "Upload Module",
      "tags": ["REQ-001", "upload", "functional", "smoke"]
    }}
  ]
}}

**Field Guidelines:**
- priority: Critical (blocking issues), High (core functionality), Medium (important features), Low (nice-to-have)
- type: Functional, Integration, E2E, Regression, Smoke, Security, Performance, Usability
- status: Always set to "Draft" for new test cases
- estimated_time: "2 mins", "5 mins", "10 mins", "15 mins", "30 mins"
- automation_status: "Manual", "To Be Automated", "Automated"

Generate 1-3 test cases per requirement. Output ONLY the JSON, no other text.""",
        description="Generates test cases from requirements",
        output_key=STATE_TEST_CASES,
    )
    
    # === Validator Agent (inside loop) ===
    validator_agent = Agent(
        name="TestCaseValidatorAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a QA Lead reviewing test cases for quality and completeness.
{feedback_section}
**Test Cases to Review:**
```
{{{STATE_TEST_CASES}}}
```

**Original Requirements:**
{requirements_text}

**Quality Checklist:**
1. Does each test case have a clear, descriptive title?
2. Is the description meaningful and explains what is being tested?
3. Are preconditions specific and complete?
4. Are steps clear, actionable, and numbered correctly?
5. Does each step have a specific expected result (not vague like "works correctly")?
6. Is test data specified where needed?
7. Is each test case traceable to at least one requirement (via tags)?
8. Are priority and type appropriately assigned?
9. Is estimated_time realistic?
10. Are there enough test cases to cover the requirements adequately?
11. Is the JSON format valid and includes ALL required fields?

**Your Task:**
- If ALL test cases pass the checklist, respond with EXACTLY: "{APPROVAL_PHRASE}"
- If improvements are needed, provide a brief critique (2-4 sentences) explaining what needs improvement.

Be thorough but constructive. Test cases should be ready for execution.""",
        description="Validates test cases for quality and coverage",
        output_key=STATE_VALIDATION_FEEDBACK,
    )
    
    # === Refiner Agent (inside loop) ===
    refiner_agent = Agent(
        name="TestCaseRefinerAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a QA Engineer refining test cases based on validation feedback.

**Current Test Cases:**
```
{{{STATE_TEST_CASES}}}
```

**Validator Feedback:**
{{{STATE_VALIDATION_FEEDBACK}}}

**Your Task:**
1. IF the feedback is EXACTLY "{APPROVAL_PHRASE}":
   - Call the 'exit_loop' function immediately
   - Do NOT output any text

2. ELSE (feedback contains improvement suggestions):
   - Apply the validator's suggestions to improve the test cases
   - Output ONLY the refined JSON object (same format as input)
   - Ensure all issues mentioned are addressed
   - Keep ALL fields: id, title, description, priority, type, status, preconditions, steps, expected_result, test_data, estimated_time, automation_status, component, tags

Either call exit_loop OR output the refined JSON - never both.""",
        description="Refines test cases based on validation feedback or exits when approved",
        tools=[exit_loop],
        output_key=STATE_TEST_CASES,
    )
    
    # === Validation Loop ===
    validation_loop = LoopAgent(
        name="ValidationLoop",
        sub_agents=[validator_agent, refiner_agent],
        max_iterations=4,
    )
    
    # === Full Pipeline ===
    pipeline = SequentialAgent(
        name="TestCaseGenerationPipeline",
        sub_agents=[generator_agent, validation_loop],
        description="Generates and iteratively validates test cases from requirements",
    )
    
    return pipeline


async def _run_test_case_pipeline_async(
    requirements_text: str,
    context_text: str,
    template_text: str,
    model: str,
    human_feedback: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run the ADK pipeline asynchronously and return generated test cases."""
    
    root_agent = _build_test_case_pipeline(
        model, requirements_text, context_text, template_text, human_feedback
    )
    
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
        }
    )
    
    logging.info(f"[TestCase Pipeline] Starting generation for session {session.id}")
    if human_feedback:
        logging.info(f"[TestCase Pipeline] With human feedback: {human_feedback[:100]}...")
    
    final_test_cases = []
    
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="Generate and validate comprehensive test cases from the requirements.")]
        ),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, 'text') and part.text:
                    parsed = _parse_test_cases_json(part.text)
                    if parsed:
                        final_test_cases = parsed
        
        if hasattr(event, 'author'):
            logging.info(f"[TestCase Pipeline] Event from {event.author}")
    
    # Check session state
    state_tcs = session.state.get(STATE_TEST_CASES, "[]")
    state_parsed = _parse_test_cases_json(state_tcs)
    if state_parsed:
        final_test_cases = state_parsed
    
    logging.info(f"[TestCase Pipeline] Generated {len(final_test_cases)} test cases")
    return final_test_cases


def _run_pipeline_sync(
    requirements_text: str,
    context_text: str,
    template_text: str,
    model: str,
    human_feedback: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Synchronous wrapper for the ADK pipeline."""
    try:
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(_run_test_case_pipeline_async(
                requirements_text, context_text, template_text, model, human_feedback
            ))
        except RuntimeError:
            return asyncio.run(_run_test_case_pipeline_async(
                requirements_text, context_text, template_text, model, human_feedback
            ))
    except Exception as e:
        logging.error(f"[TestCase Pipeline] Error: {e}")
        return []


def generate_test_cases(payload: GenerateTestCasesInput) -> List[TestCase]:
    """Generate test cases using the ADK multi-agent pipeline."""
    settings = get_settings()
    
    # Prepare input texts for the agents
    requirements_text = "\n".join([f"- {r.id}: {r.text}" for r in payload.requirements])
    
    context_parts = []
    if payload.context:
        if payload.context.app_link:
            context_parts.append(f"Application URL: {payload.context.app_link}")
        if payload.context.prototype_link:
            context_parts.append(f"Prototype URL: {payload.context.prototype_link}")
        if payload.context.diagram_links:
            context_parts.append(f"Diagrams: {', '.join(str(d) for d in payload.context.diagram_links)}")
        if payload.context.notes:
            context_parts.append(f"Notes: {payload.context.notes}")
    context_text = "\n".join(context_parts) if context_parts else "No additional context provided."
    
    template_text = f"Name: {payload.template.name}, Format: {payload.template.format}, Fields: {', '.join(payload.template.fields)}"
    
    # Get human feedback if provided
    human_feedback = payload.feedback if payload.feedback else None
    
    # Run the ADK pipeline
    raw_test_cases = _run_pipeline_sync(
        requirements_text=requirements_text,
        context_text=context_text,
        template_text=template_text,
        model=settings.model_name,
        human_feedback=human_feedback,
    )
    
    # Convert to TestCase models with all new fields
    test_cases: List[TestCase] = []
    for tc in raw_test_cases:
        try:
            # Ensure steps are properly formatted
            steps = []
            for s in tc.get("steps", []):
                steps.append(TestStep(
                    step=s.get("step", 1),
                    action=s.get("action", ""),
                    expected=s.get("expected", ""),
                    test_data=s.get("test_data"),
                ))
            
            test_cases.append(TestCase(
                id=tc.get("id", f"TC-{len(test_cases)+1:03d}"),
                title=tc.get("title", "Untitled Test Case"),
                description=tc.get("description"),
                priority=tc.get("priority", "Medium"),
                type=tc.get("type", "Functional"),
                status=tc.get("status", "Draft"),
                preconditions=tc.get("preconditions"),
                steps=steps,
                expected_result=tc.get("expected_result"),
                test_data=tc.get("test_data"),
                estimated_time=tc.get("estimated_time"),
                automation_status=tc.get("automation_status", "Manual"),
                component=tc.get("component"),
                tags=tc.get("tags", []),
            ))
        except (ValidationError, KeyError) as e:
            logging.warning(f"[TestCase Pipeline] Skipping invalid test case: {e}")
            continue
    
    # Fallback if pipeline returned nothing
    if not test_cases:
        logging.warning("[TestCase Pipeline] No test cases from pipeline, using fallback")
        for idx, req in enumerate(payload.requirements, start=1):
            steps = [
                TestStep(step=1, action=f"Navigate to feature for {req.id}", expected="Page loads"),
                TestStep(step=2, action=f"Perform action for {req.id}", expected="Expected outcome"),
            ]
            test_cases.append(
                TestCase(
                    id=f"TC-{idx:03d}",
                    title=f"Validate {req.text[:60]}",
                    description=f"Verify that {req.text[:100]}",
                    priority="Medium",
                    type="Functional",
                    status="Draft",
                    preconditions=payload.context.notes if payload.context else None,
                    steps=steps,
                    expected_result="Feature works as expected per requirement",
                    estimated_time="5 mins",
                    automation_status="To Be Automated",
                    component="General",
                    tags=[req.id, "generated"],
                )
            )
    
    return test_cases
