"""Request-local overrides used by synthetic quality evaluations."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationLimits:
    context_chars: int | None = None
    review_bytes: int | None = None
    case_output_tokens: int | None = None


current_evaluation_limits = ContextVar("evaluation_limits", default=EvaluationLimits())


@contextmanager
def evaluation_limits_scope(limits: EvaluationLimits):
    token = current_evaluation_limits.set(limits)
    try:
        yield
    finally:
        current_evaluation_limits.reset(token)
