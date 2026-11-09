"""Owner-facing views of the scenario preparation continuation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .projects import QaProjectDetail
from .requirement_imports import RequirementReviewChange, RequirementReviewsInput


ApprovalContinuationState = Literal["queued", "running", "awaiting_user", "completed", "failed", "interrupted", "cancelled", "superseded"]


class ApprovedRequirementReviewChange(RequirementReviewChange):
    model_config = ConfigDict(extra="forbid")

    review_status: Literal["Approved"]


class ApproveAndPrepareScenariosInput(RequirementReviewsInput):
    model_config = ConfigDict(extra="forbid")

    reviews: list[ApprovedRequirementReviewChange] = Field(min_length=1)


class ApprovalContinuationView(BaseModel):
    run_id: str
    state: ApprovalContinuationState
    stage: Literal["prepare_scenarios"]
    project_revision: int = Field(ge=1)
    request_id: str
    reason: str | None
    result_snapshot_id: str | None


class ApprovalContinuationReceipt(BaseModel):
    request_id: str
    run_id: str | None
    blocked_reason: str | None


class ApprovalContinuationStatus(BaseModel):
    enabled: bool
    runs: list[ApprovalContinuationView]
    receipt: ApprovalContinuationReceipt | None = None


class ApproveAndPrepareScenariosResult(BaseModel):
    project: QaProjectDetail
    continuation: ApprovalContinuationView | None
    blocked_reason: str | None
