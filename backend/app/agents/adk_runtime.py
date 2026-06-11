"""Shared ADK runtime configuration helpers.

Keep model-call behavior deterministic and resilient across the agent pipelines.
"""

from google.genai import types

DEFAULT_MODEL_HTTP_RETRY_ATTEMPTS = 2
DEFAULT_MODEL_HTTP_RETRY_INITIAL_DELAY = 1.0


def _http_options() -> types.HttpOptions:
    return types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            attempts=DEFAULT_MODEL_HTTP_RETRY_ATTEMPTS,
            initial_delay=DEFAULT_MODEL_HTTP_RETRY_INITIAL_DELAY,
        )
    )


def json_generation_config(*, max_output_tokens: int = 8192, temperature: float = 0.0) -> types.GenerateContentConfig:
    """Config for agents that must emit JSON text or use output_schema."""
    return types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        http_options=_http_options(),
    )


def tool_generation_config(*, max_output_tokens: int = 8192, temperature: float = 0.0) -> types.GenerateContentConfig:
    """Config for agents that may call tools, where JSON response MIME can block function calls."""
    return types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        http_options=_http_options(),
    )


def text_generation_config(
    *,
    system_instruction: str | None = None,
    max_output_tokens: int = 8192,
    temperature: float = 0.0,
) -> types.GenerateContentConfig:
    """Config for direct text generations that still need stable, retryable behavior."""
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        http_options=_http_options(),
    )
