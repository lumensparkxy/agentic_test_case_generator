"""Insert source text after ADK expands its own state placeholders."""

import re
from uuid import uuid4

from google.adk.utils.instructions_utils import inject_session_state

from .prompting import sanitize_human_feedback
from ..services.guidance_service import apply_agent_guidance


def literal_source_agent(builder, model, requirements_text, context_text, template_text, *args, **kwargs):
    values = [requirements_text, context_text, template_text]
    feedback = kwargs.get("human_feedback")
    if feedback:
        values.append(sanitize_human_feedback(feedback))
    tokens = [f"SOURCE_LITERAL_{uuid4().hex}" for _ in values]
    bindings = dict(zip(tokens, values, strict=True))
    if feedback:
        kwargs["human_feedback"] = tokens[3]
    root = apply_agent_guidance(builder(model, *tokens[:3], *args, **kwargs), "test_cases")
    pattern = re.compile("|".join(re.escape(token) for token in tokens))

    def bind(agent):
        if isinstance(getattr(agent, "instruction", None), str):
            template = agent.instruction

            async def instruction(context):
                expanded = await inject_session_state(template, context)
                # One pass: source text cannot introduce state or another source substitution.
                return pattern.sub(lambda match: bindings[match.group()], expanded)

            agent.instruction = instruction
        for child in getattr(agent, "sub_agents", ()):
            bind(child)

    bind(root)
    return root
