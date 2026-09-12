"""Project import contracts. Candidate IDs are temporary; requirement IDs are stable."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from .requirements import Requirement


class ImportMatch(BaseModel):
    requirement_uid: str
    reason: str


class ImportCandidate(BaseModel):
    candidate_id: str
    requirement: Requirement
    classification: Literal["new", "updated", "unchanged", "needs_decision"]
    target_requirement_uid: str | None = None
    suggestions: list[ImportMatch] = Field(default_factory=list)
    reason: str


class RequirementImportPreview(BaseModel):
    import_id: str
    project_id: str
    base_project_revision: int
    source_name: str
    operation: str
    status: Literal["pending", "applied", "cancelled"] = "pending"
    created_at: datetime
    current_requirements: list[Requirement]
    candidates: list[ImportCandidate]
    suggested_update_scope: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    recovery_snapshot_ids: list[str] = Field(default_factory=list)
    guidance: dict[str, Any] | None = None


class ImportDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    action: Literal["add", "update", "keep", "skip"]
    target_requirement_uid: str | None = None


class ImportApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_project_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)
    decisions: list[ImportDecision]
    update_scope: list[str] = Field(default_factory=list)
    confirm_retirements: bool = False


class ImportRecoveryInput(BaseModel):
    base_project_revision: int = Field(ge=0)
    baseline_snapshot_id: str
    incoming_snapshot_id: str


class ImportHistoryEntry(BaseModel):
    snapshot_id: str
    project_revision: int
    created_at: datetime
    requirement: Requirement


class RequirementReviewChange(BaseModel):
    requirement_uid: str
    review_status: Literal["Draft", "Needs Review", "Approved", "Rejected"]
    quality_flags: list[str] = Field(default_factory=list, max_length=30)


class RequirementReviewsInput(BaseModel):
    base_project_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)
    reviews: list[RequirementReviewChange] = Field(min_length=1)
