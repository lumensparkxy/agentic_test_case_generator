"""
ADK Client - Multi-agent requirement extraction using Google ADK.

Uses LoopAgent with LlmAgents for iterative refinement following the official
google-adk patterns: https://google.github.io/adk-docs/
"""
import asyncio
import json
import logging
import os
import uuid
from typing import Optional, List, Dict, Any

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

# Default model
DEFAULT_MODEL = "gemini-2.5-flash"

# State keys
STATE_REQUIREMENTS = "current_requirements"
STATE_REVIEW_FEEDBACK = "review_feedback"

# Approval phrase for loop termination
APPROVAL_PHRASE = "APPROVED"

# ============================================================================
# JSON extraction helper
# ============================================================================

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


def _parse_requirements_json(text: str) -> List[Dict[str, str]]:
    """Parse requirements from agent response (handles JSON array)."""
    if not text:
        return []
    
    json_text = _extract_json(text)
    if not json_text:
        return []
    
    try:
        data = json.loads(json_text)
        if isinstance(data, list):
            valid = []
            for item in data:
                if isinstance(item, dict) and "id" in item and "text" in item:
                    valid.append({"id": item["id"], "text": item["text"]})
            return valid
        elif isinstance(data, dict) and "requirements" in data:
            return _parse_requirements_json(json.dumps(data["requirements"]))
    except json.JSONDecodeError:
        pass
    
    return []


# ============================================================================
# Tool to exit the refinement loop when requirements are approved
# ============================================================================

def exit_loop(tool_context: ToolContext) -> dict:
    """Call this function when the requirements are approved and meet quality standards."""
    logging.info(f"[exit_loop] Requirements approved - exiting refinement loop")
    tool_context.actions.escalate = True
    tool_context.actions.skip_summarization = True
    return {"status": "approved", "message": "Requirements approved"}


# ============================================================================
# Build the ADK Agent Pipeline
# ============================================================================

def _build_requirement_extraction_pipeline(model: str) -> Agent:
    """
    Build a multi-agent pipeline for requirement extraction using google-adk.
    
    Pipeline structure:
    1. InitialExtractorAgent - extracts raw requirements from document (runs once)
    2. RefinementLoop (LoopAgent):
       a. ReviewerAgent - reviews requirements, approves or suggests improvements
       b. RefinerAgent - refines requirements based on review OR exits if approved
    """
    
    # === Initial Extractor Agent ===
    # Uses include_contents='default' to read the document from the user message
    initial_extractor = Agent(
        name="InitialExtractorAgent",
        model=model,
        include_contents='default',  # Include conversation history with document
        instruction="""You are a Senior Business Analyst specializing in requirements engineering.

**Your Task:** Analyze the document provided in the user message and extract TESTABLE functional requirements.

**Rules for Requirements:**
1. Each requirement MUST be a complete, testable statement
2. Use "The system shall..." format consistently
3. Be specific and measurable - include acceptance criteria where possible
4. NO code snippets, file paths, directory structures, or implementation details
5. NO markdown formatting in requirements text
6. Each requirement must describe a verifiable behavior or capability
7. Combine related items into single comprehensive requirements

**Output Format:**
Return ONLY a valid JSON array of requirements:
[
  {"id": "REQ-001", "text": "The system shall allow users to upload requirement documents in .md and .docx formats."},
  {"id": "REQ-002", "text": "The system shall parse uploaded documents and extract testable requirements."}
]

Extract 5-15 high-quality testable requirements. Output ONLY the JSON array, no other text.""",
        description="Extracts initial requirements from the source document",
        output_key=STATE_REQUIREMENTS,
    )
    
    # === Reviewer Agent (inside loop) ===
    # Uses include_contents='none' and reads from state
    reviewer_agent = Agent(
        name="ReviewerAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a Quality Assurance Lead reviewing software requirements for testability.

**Current Requirements to Review:**
```
{{{STATE_REQUIREMENTS}}}
```

**Quality Checklist:**
1. Is each requirement testable and verifiable with a specific test case?
2. Are ALL requirements written in "The system shall..." format?
3. Are there any vague or ambiguous requirements that need clarification?
4. Are there any code snippets, file paths, or implementation details? (MUST be removed)
5. Is each requirement specific enough to write acceptance criteria for?
6. Are requirements atomic (one behavior per requirement)?

**Your Task:**
- If ALL requirements pass the checklist, respond with EXACTLY: "{APPROVAL_PHRASE}"
- If improvements are needed, provide a brief critique (1-3 sentences) explaining what needs improvement.

Be strict but fair. Requirements should be professional and suitable for formal software specification.""",
        description="Reviews requirements for quality and testability",
        output_key=STATE_REVIEW_FEEDBACK,
    )
    
    # === Refiner/Exit Agent (inside loop) ===
    refiner_agent = Agent(
        name="RefinerAgent",
        model=model,
        include_contents='none',
        instruction=f"""You are a Business Analyst refining or finalizing requirements.

**Current Requirements:**
```
{{{STATE_REQUIREMENTS}}}
```

**Reviewer Feedback:**
{{{STATE_REVIEW_FEEDBACK}}}

**Your Task:**
1. IF the reviewer feedback is EXACTLY "{APPROVAL_PHRASE}":
   - You MUST call the 'exit_loop' function immediately
   - Do NOT output any text

2. ELSE (the feedback contains improvement suggestions):
   - Apply the reviewer's suggestions to improve the requirements
   - Output ONLY the refined JSON array of requirements (same format as input)
   - Do NOT add explanations, just the JSON array

Either call exit_loop OR output the refined JSON array - never both.""",
        description="Refines requirements based on feedback or exits the loop if approved",
        tools=[exit_loop],
        output_key=STATE_REQUIREMENTS,
    )
    
    # === Refinement Loop ===
    refinement_loop = LoopAgent(
        name="RefinementLoop",
        sub_agents=[reviewer_agent, refiner_agent],
        max_iterations=5,
    )
    
    # === Full Pipeline ===
    pipeline = SequentialAgent(
        name="RequirementExtractionPipeline",
        sub_agents=[initial_extractor, refinement_loop],
        description="Extracts and iteratively refines requirements from documents",
    )
    
    return pipeline


# ============================================================================
# Main Entry Point - Run the ADK Pipeline
# ============================================================================

async def _run_pipeline_async(
    document_text: str,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, str]]:
    """Run the ADK pipeline asynchronously and return extracted requirements."""
    
    # Build the agent pipeline
    root_agent = _build_requirement_extraction_pipeline(model)
    
    # Create session service and runner
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="requirement_extractor",
        session_service=session_service,
    )
    
    # Create a new session with pre-populated state
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    session = await session_service.create_session(
        app_name="requirement_extractor",
        user_id=user_id,
        state={
            STATE_REQUIREMENTS: "[]",
            STATE_REVIEW_FEEDBACK: "",
        }
    )
    
    logging.info(f"[ADK Pipeline] Starting extraction for session {session.id}")
    
    # Run the pipeline - pass document in the user message content
    final_response = ""
    final_requirements = []
    
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=f"""Please extract and refine testable requirements from this document:

---DOCUMENT START---
{document_text}
---DOCUMENT END---

Analyze the features and functionality described and produce high-quality testable requirements.""")]
        ),
    ):
        # Capture text output
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, 'text') and part.text:
                    final_response = part.text
                    # Try to parse requirements from each response
                    parsed = _parse_requirements_json(final_response)
                    if parsed:
                        final_requirements = parsed
        
        # Log progress
        if hasattr(event, 'author'):
            logging.info(f"[ADK Pipeline] Event from {event.author}")
    
    # Also check session state for requirements
    state_reqs = session.state.get(STATE_REQUIREMENTS, "[]")
    state_parsed = _parse_requirements_json(state_reqs)
    if state_parsed:
        final_requirements = state_parsed
    
    logging.info(f"[ADK Pipeline] Extracted {len(final_requirements)} requirements")
    return final_requirements


def run_requirement_extraction_loop_sync(
    document_text: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 3,  # Kept for API compatibility, LoopAgent uses its own
) -> List[Dict[str, str]]:
    """
    Synchronous wrapper to run the ADK requirement extraction pipeline.
    
    Uses google-adk with LoopAgent pattern:
    1. InitialExtractorAgent extracts requirements from document
    2. RefinementLoop iteratively improves requirements:
       - ReviewerAgent checks quality
       - RefinerAgent applies feedback or exits when approved
    """
    try:
        # Check if we're already in an async context
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, use nest_asyncio
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(_run_pipeline_async(document_text, model))
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            return asyncio.run(_run_pipeline_async(document_text, model))
    except Exception as e:
        logging.error(f"[ADK Pipeline] Error: {e}")
        return []


# ============================================================================
# Legacy API compatibility functions
# ============================================================================

def run_adk_prompt(
    *,
    prompt: str,
    model: str,
    agent_name: str,
    instruction: str,
) -> str:
    """Legacy API - Run a single prompt (for backward compatibility)."""
    from google import genai
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
    try:
        response = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=instruction),
        )
        return response.text.strip() if response and response.text else ""
    except Exception as e:
        logging.error(f"[{agent_name}] Error: {e}")
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
    json_text = _extract_json(text)
    if not json_text:
        return None
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None


# ============================================================================
# Requirement Refinement with Human Feedback
# ============================================================================

async def _run_refinement_async(
    existing_requirements: List[Dict[str, Any]],
    feedback: str,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, str]]:
    """Run ADK refinement with human feedback asynchronously."""
    from google import genai
    
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
    
    # Format existing requirements
    req_text = json.dumps(existing_requirements, indent=2)
    
    # Refiner instruction
    refiner_instruction = """You are a Senior Business Analyst revising requirements based on human feedback.

**Your Task:** Refine the existing requirements according to the feedback provided.

**Rules:**
1. Apply ALL feedback suggestions provided by the reviewer
2. Keep requirements in "The system shall..." format
3. Add new requirements if the feedback requests them
4. Remove or merge requirements if the feedback suggests
5. Ensure each requirement remains testable and specific
6. Maintain proper REQ-XXX numbering (renumber if needed)

**Output Format:**
Return ONLY a valid JSON array of refined requirements:
[
  {"id": "REQ-001", "text": "The system shall..."},
  ...
]

Output ONLY the JSON array, no explanations."""

    prompt = f"""**Existing Requirements:**
```json
{req_text}
```

**Human Feedback to Implement:**
{feedback}

Please refine the requirements based on the feedback above."""

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=refiner_instruction),
        )
        
        if response and response.text:
            parsed = _parse_requirements_json(response.text)
            if parsed:
                return parsed
    except Exception as e:
        logging.error(f"[Requirement Refinement] Error: {e}")
    
    return []


def run_requirement_refinement_sync(
    existing_requirements: List[Dict[str, Any]],
    feedback: str,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, str]]:
    """
    Synchronous wrapper to refine requirements based on human feedback.
    """
    try:
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(_run_refinement_async(existing_requirements, feedback, model))
        except RuntimeError:
            return asyncio.run(_run_refinement_async(existing_requirements, feedback, model))
    except Exception as e:
        logging.error(f"[Requirement Refinement] Error: {e}")
        return []
