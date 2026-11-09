"""Owner-scoped status and recovery for an admitted scenario refresh."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScenarioRefreshAttempt(BaseModel):
    operation_id: str
    fence: int
    request_id: str
    state: Literal["running", "interrupted"]
    reason: str | None = None
    source_snapshot_id: str


class ScenarioRefreshResult(BaseModel):
    request_id: str
    previous_snapshot_id: str
    snapshot_id: str
    project_revision: int
    refreshed_requirement_ids: list[str]
    retained_requirement_ids: list[str]
    removed_requirement_ids: list[str]


class ScenarioRefreshStatus(BaseModel):
    enabled: bool = False
    eligible: bool = False
    blocked: bool = False
    required_requirement_ids: list[str] = Field(default_factory=list)
    attempt: ScenarioRefreshAttempt | None = None
    result: ScenarioRefreshResult | None = None


class ScenarioRefreshRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fence: int = Field(ge=1)


class ScenarioRefreshReleased(BaseModel):
    released: Literal[True] = True
