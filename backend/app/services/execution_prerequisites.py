"""Owner/project-scoped references; values are never copied into artifacts."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re

REFERENCE = re.compile(r"^env:(PETF_TEST_[A-Z0-9_]+)$")
_authorized: ContextVar[frozenset[str]] = ContextVar("execution_reference_access", default=frozenset())


@contextmanager
def execution_reference_scope(owner_user_id, project_id):
    """Server configuration grants access; artifact text cannot grant itself access."""
    names = set()
    try:
        grants = json.loads(os.getenv("PETF_TEST_REFERENCE_ACCESS", "[]"))
        for grant in grants if isinstance(grants, list) else []:
            if isinstance(grant, dict) and project_id and grant.get("owner_user_id") == owner_user_id and grant.get("project_id") == project_id:
                names.update(name for name in grant.get("variables", []) if isinstance(name, str) and REFERENCE.fullmatch("env:" + name))
    except ValueError, TypeError:
        names = set()
    token = _authorized.set(frozenset(names))
    try:
        yield
    finally:
        _authorized.reset(token)


def prerequisite_blockers(case):
    blockers = []
    allowed = _authorized.get()
    for item in case.execution_prerequisites:
        match = REFERENCE.fullmatch(item.reference or "")
        if item.status != "configured" or not match or match[1] not in allowed or not os.getenv(match[1]):
            blockers.append(f"Prerequisite {item.name} requires an available reference authorized for this owner and project; no execution value is recorded.")
    # A literal runtime marker must not bypass admission by omitting the contract.
    for step in case.steps:
        for name in re.findall(r"\[\[env:([^\]]+)\]\]", step.action):
            if name not in allowed or not os.getenv(name):
                blockers.append("An input reference is unavailable or not authorized for this owner and project.")
    return blockers
