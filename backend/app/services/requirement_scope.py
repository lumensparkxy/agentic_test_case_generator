"""Explicit deferral/exclusion narrows generation without approving unknown rules."""

from ..contracts.requirements import Requirement
from pydantic import ValidationError


def approved_generation_requirements(project):
    baseline = project.current_snapshots.get("requirements")
    state = project.stage_state.get("requirements")
    if not baseline or not state or state.current_snapshot_id != baseline.snapshot_id or state.stale:
        return []
    try:
        rows = [Requirement.model_validate(row) for row in baseline.payload.get("requirements", [])]
    except ValidationError:
        return []
    return eligible_generation_scope(rows, approved=state.approved)


def eligible_generation_scope(rows: list[Requirement], *, approved: bool) -> list[Requirement]:
    """Use the same scope rules before and after an approval snapshot is committed."""
    active = [row for row in rows if row.lifecycle_status == "active" and row.review_status != "Rejected"]
    scoped = [row for row in active if row.clarification_status not in ("deferred", "excluded")]
    explicitly_scoped = any(row.clarification_status in ("deferred", "excluded") for row in rows)
    if not approved and not explicitly_scoped:
        return []
    if (
        not scoped
        or len({row.id for row in scoped}) != len(scoped)
        or any(row.review_status != "Approved" or row.clarification_status == "open" for row in scoped)
    ):
        return []
    return scoped
