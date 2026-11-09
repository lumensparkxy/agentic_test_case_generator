"""Shared ADK runtime configuration helpers.

Keep model-call behavior deterministic and resilient across the agent pipelines.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

from google.adk.models import Gemini
from google.genai import types
from .quality_goal import QUALITY_GOAL

DEFAULT_MODEL_HTTP_RETRY_ATTEMPTS = 2
DEFAULT_MODEL_HTTP_RETRY_INITIAL_DELAY = 1.0
_model_reservation: ContextVar[Callable[[], float] | None] = ContextVar("model_request_reservation", default=None)


@contextmanager
def bounded_model_requests(reserve):
    """Use the same reservation callback in each copied shard context."""
    token = _model_reservation.set(reserve)
    try:
        yield
    finally:
        _model_reservation.reset(token)


class _BoundedGemini(Gemini):
    async def generate_content_async(self, llm_request, stream=False):
        reserve = _model_reservation.get()
        if reserve is None:
            raise RuntimeError("The bounded model requires a request reservation")
        timeout = await asyncio.to_thread(reserve)
        llm_request.config = _bounded_request_config(llm_request.config or types.GenerateContentConfig(), timeout)
        llm_request.cache_config = None
        async with asyncio.timeout(timeout):
            async for response in super().generate_content_async(llm_request, stream):
                yield response


def _single_attempt_client_options():
    return {
        "vertexai": False,
        "http_options": types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1),
            client_args={"follow_redirects": False},
            async_client_args={"follow_redirects": False, "allow_redirects": False},
        ),
    }


def _bounded_request_config(config, timeout):
    http_options = config.http_options or types.HttpOptions()
    return config.model_copy(
        update={
            "http_options": http_options.model_copy(update={"timeout": max(1, int(timeout * 1000)), "retry_options": types.HttpRetryOptions(attempts=1)}),
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
    )


def bounded_adk_model(model: str) -> str | Gemini:
    if _model_reservation.get() is None:
        return model
    return _BoundedGemini(model=model, client_kwargs=_single_attempt_client_options())


scenario_model = bounded_adk_model


def generate_direct_content(client_factory, *, api_key, model, contents, config, timeout_seconds):
    """Preserve direct SDK calls, sharing the lease and limit when run by a worker."""
    reserve = _model_reservation.get()
    if reserve is None:
        with client_factory(api_key=api_key) as client:
            return client.models.generate_content(model=model, contents=contents, config=config)

    timeout = min(timeout_seconds, reserve())
    request_config = _bounded_request_config(config, timeout)

    async def call(client):
        async with asyncio.timeout(timeout), client.aio as transport:
            return await transport.models.generate_content(model=model, contents=contents, config=request_config)

    with client_factory(api_key=api_key, **_single_attempt_client_options()) as client:
        return asyncio.run(call(client))


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
        system_instruction=QUALITY_GOAL + "\n" + (system_instruction or ""),
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        http_options=_http_options(),
    )
