"""Owner-facing approval continuation views."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .projects import QaProjectDetail
from .requirement_imports import RequirementReviewChange, RequirementReviewsInput
from .test_cases import TestCaseTemplate
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
    template: TestCaseTemplate | None = None

    @field_validator("template")
    @classmethod
    def bounded_template(cls, value: TestCaseTemplate | None) -> TestCaseTemplate | None:
        if value is not None and len(value.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("The continuation template must fit in 4096 UTF-8 bytes.")
        return value


class SavedPlanGenerationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_project_revision: int = Field(ge=1)
    snapshot_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    review_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")


class GenerateTestsFromSavedApprovalInput(SavedPlanGenerationTarget):
    template: TestCaseTemplate | None = None

    @field_validator("template")
    @classmethod
    def bounded_template(cls, value: TestCaseTemplate | None) -> TestCaseTemplate | None:
        return ApproveAndGenerateTestsInput.bounded_template(value)


class ApprovalContinuationView(BaseModel):
    run_id: str
    state: ApprovalContinuationState
    stage: ApprovalContinuationStage
    project_revision: int = Field(ge=1)
    request_id: str
    reason: str | None
    source_snapshot_id: str
    result_snapshot_id: str | None
    retry_available: bool = False
    provider_usage_unconfirmed: bool = False


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


class CurrentJourneyBudgetBlocker(BaseModel):
    stage: ApprovalContinuationStage
    reason: Literal["journey_run_budget_exhausted", "journey_run_budget_unavailable"]
    source_snapshot_id: str
    project_revision: int = Field(ge=1)


class ApprovalContinuationStatus(BaseModel):
    enabled: bool
    tests_enabled: bool
    runs: list[ApprovalContinuationView]
    receipt: ApprovalContinuationReceipt | None = None
    saved_review_enabled: bool = False
    saved_review_attempt: SavedReviewAdmissionView | None = None
    saved_plan_generation: SavedPlanGenerationTarget | None = Field(default=None, exclude_if=lambda value: value is None)
    current_budget_blocker: CurrentJourneyBudgetBlocker | None = Field(default=None, exclude_if=lambda value: value is None)


class ApproveAndPrepareScenariosResult(BaseModel):
    project: QaProjectDetail
    continuation: ApprovalContinuationView | None
    blocked_reason: str | None


class ApproveAndGenerateTestsResult(UseCaseReviewResponse):
    continuation: ApprovalContinuationView | None
    blocked_reason: str | None


class GenerateTestsFromSavedApprovalResult(BaseModel):
    continuation: ApprovalContinuationView
