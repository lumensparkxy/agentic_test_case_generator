from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .orchestrator import OrchestratorStatusResponse
from .projects import QaProjectStageState


UseCaseReviewDecision = Literal["approve", "request_changes"]


class UseCaseReviewRequest(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=160)
    base_project_revision: int = Field(ge=0)
    decision: UseCaseReviewDecision
    comment: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("snapshot_id must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_comment(self) -> "UseCaseReviewRequest":
        normalized_comment = self.comment.strip() if self.comment else None
        if self.decision == "request_changes" and not normalized_comment:
            raise ValueError("A comment is required when requesting changes")
        self.comment = normalized_comment
        return self


class UseCaseReviewRecord(BaseModel):
    review_id: str
    project_id: str
    stage: Literal["use_cases"] = "use_cases"
    snapshot_id: str
    decision: UseCaseReviewDecision
    comment: Optional[str] = None
    reviewer_user_id: str
    reviewer_name: Optional[str] = None
    reviewer_email: Optional[str] = None
    request_id: str
    idempotency_key: str
    request_fingerprint: str
    timeline_event_id: str
    base_project_revision: int = Field(ge=0)
    resulting_project_revision: int = Field(ge=0)
    decided_at: datetime


class UseCaseReviewResponse(BaseModel):
    review: UseCaseReviewRecord
    project_revision: int = Field(ge=0)
    use_cases_state: QaProjectStageState
    orchestrator_status: OrchestratorStatusResponse


__all__ = [
    "UseCaseReviewDecision",
    "UseCaseReviewRequest",
    "UseCaseReviewRecord",
    "UseCaseReviewResponse",
]
