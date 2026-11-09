"""Owner-scoped contracts for repairing one exact saved test case."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SelectedCaseRepairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=160)
    base_project_revision: int = Field(ge=1)
    case_id: str = Field(min_length=1, max_length=160)
    case_version_id: str = Field(min_length=1, max_length=160)
    target_id: str = Field(pattern=r"^repair_[0-9a-f]{64}$")


class SelectedCaseRepairTarget(BaseModel):
    target_id: str
    case_id: str
    case_version_id: str
    requirement_id: str
    scenario_id: str
    obligation: str
    source_quote: str
    reason: str
    step_numbers: list[int]


class SelectedCaseRepairAttempt(BaseModel):
    operation_id: str
    fence: int
    request_id: str
    state: Literal["running", "interrupted"]
    reason: str | None = None
    source_snapshot_id: str
    case_id: str


class SelectedCaseRepairResult(BaseModel):
    request_id: str
    source_snapshot_id: str
    result_snapshot_id: str
    project_revision: int
    case_id: str
    before_case_version_id: str
    after_case_version_id: str
    preserved_case_count: int


class SelectedCaseRepairStatus(BaseModel):
    enabled: bool = False
    blocked: bool = False
    targets: list[SelectedCaseRepairTarget] = Field(default_factory=list)
    attempt: SelectedCaseRepairAttempt | None = None
    result: SelectedCaseRepairResult | None = None


class SelectedCaseRepairRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fence: int = Field(ge=1)


class SelectedCaseRepairReleased(BaseModel):
    released: Literal[True] = True
