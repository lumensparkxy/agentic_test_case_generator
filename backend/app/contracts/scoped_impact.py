"""Owner-only status and exact receipt for bounded direct case updates."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScopedImpactAttempt(BaseModel):
    operation_id: str
    fence: int
    request_id: str
    state: Literal["running", "interrupted"]
    reason: str | None = None
    analysis_snapshot_id: str
    source_snapshot_id: str
    accepted_recommendation_ids: list[str]


class ScopedImpactResult(BaseModel):
    request_id: str
    analysis_snapshot_id: str
    source_snapshot_id: str
    result_snapshot_id: str
    project_revision: int
    accepted_recommendation_ids: list[str]
    changed_test_case_ids: list[str]
    preserved_count: int
    updated_count: int


class ScopedImpactStatus(BaseModel):
    enabled: bool = False
    eligible: bool = False
    blocked: bool = False
    selectable_recommendation_ids: list[str] = Field(default_factory=list)
    attempt: ScopedImpactAttempt | None = None
    result: ScopedImpactResult | None = None


class ScopedImpactRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fence: int = Field(ge=1)


class ScopedImpactReleased(BaseModel):
    released: Literal[True] = True
