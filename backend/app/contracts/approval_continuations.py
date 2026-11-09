"""Owner-facing approval continuation views."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .projects import QaProjectDetail
from .requirement_imports import RequirementReviewChange, RequirementReviewsInput
from .use_case_reviews import UseCaseReviewRequest, UseCaseReviewResponse


ApprovalContinuationStage = Literal["prepare_scenarios", "generate_tests"]
ApprovalContinuationState = Literal["queued", "running", "awaiting_user", "completed", "failed", "interrupted", "cancelled", "superseded"]


class ApprovedRequirementReviewChange(RequirementReviewChange):
    model_config = ConfigDict(extra="forbid")

    review_status: Literal["Approved"]


class ApproveAndPrepareScenariosInput(RequirementReviewsInput):
    model_config = ConfigDict(extra="forbid")

    reviews: list[ApprovedRequirementReviewChange] = Field(min_length=1)


class ApproveAndGenerateTestsInput(UseCaseReviewRequest):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve"]


class ApprovalContinuationView(BaseModel):
    run_id: str
    state: ApprovalContinuationState
    stage: ApprovalContinuationStage
    project_revision: int = Field(ge=1)
    request_id: str
    reason: str | None
    source_snapshot_id: str
    result_snapshot_id: str | None


class ApprovalContinuationReceipt(BaseModel):
    request_id: str
    run_id: str | None
    blocked_reason: str | None


class SavedReviewAdmissionView(BaseModel):
    operation_id: str
    fence: int = Field(ge=1)
    state: Literal["running", "interrupted"]
    source_snapshot_id: str
    request_id: str


class SavedReviewReleaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fence: int = Field(strict=True, ge=1)


class SavedReviewReleaseResult(BaseModel):
    released: Literal[True] = True


class ApprovalContinuationStatus(BaseModel):
    enabled: bool
    tests_enabled: bool
    runs: list[ApprovalContinuationView]
    receipt: ApprovalContinuationReceipt | None = None
    saved_review_enabled: bool = False
    saved_review_attempt: SavedReviewAdmissionView | None = None


class ApproveAndPrepareScenariosResult(BaseModel):
    project: QaProjectDetail
    continuation: ApprovalContinuationView | None
    blocked_reason: str | None


class ApproveAndGenerateTestsResult(UseCaseReviewResponse):
    continuation: ApprovalContinuationView | None
    blocked_reason: str | None
