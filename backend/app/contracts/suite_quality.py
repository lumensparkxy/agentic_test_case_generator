"""Independent design evidence for one saved suite."""

from typing import Literal

from pydantic import BaseModel, Field


class SuiteQualityProjection(BaseModel):
    suite_snapshot_id: str
    source_freshness: Literal["current", "stale", "unknown"] = "unknown"
    design_status: Literal["complete", "incomplete", "unknown"] = "unknown"
    scenario_decision: Literal["approved", "changes_requested", "unavailable"] = "unavailable"
    scenario_snapshot_id: str | None = None
    scenario_review_id: str | None = None
    human_acceptance: Literal["approved", "changes_requested", "unavailable"] = "unavailable"
    declared_setup: Literal["blocked", "not_assessed", "unknown"] = "unknown"
    required_requirement_ids: list[str] = Field(default_factory=list)
    deferred_requirement_ids: list[str] = Field(default_factory=list)
    excluded_requirement_ids: list[str] = Field(default_factory=list)
    active_case_count: int = Field(default=0, ge=0)
    deprecated_case_count: int = Field(default=0, ge=0)
    reason_codes: list[str] = Field(default_factory=list)
