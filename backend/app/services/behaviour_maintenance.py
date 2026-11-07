"""Explicit maintenance boundary; runtime findings cannot publish behaviour."""

from contextlib import contextmanager
from contextvars import ContextVar
import os
from fastapi import HTTPException

_maintenance = ContextVar("agent_behaviour_maintenance", default=False)


def require_maintenance(actor):
    from .guidance_service import current_guidance

    if current_guidance() is not None or os.getenv("AGENT_MAINTENANCE_ENABLED", "false").lower() != "true" or "agent_maintainer" not in (actor.roles or []):
        raise HTTPException(403, "Agent behaviour is read-only during application use. Publication requires a separate authenticated maintenance session.")


@contextmanager
def maintenance_scope(actor):
    require_maintenance(actor)
    token = _maintenance.set(True)
    try:
        yield
    finally:
        _maintenance.reset(token)


def check_knowledge_mutation(actor, project_id, action):
    from .guidance_service import current_guidance

    if project_id and action in ("propose", "dismiss"):
        return
    if not _maintenance.get() or current_guidance() is not None:
        raise HTTPException(403, "Runtime cannot publish, revise or promote behavioural guidance. Record a project finding instead.")
    require_maintenance(actor)
