"""Project decisions are source data, separate from optional behavioural guidance."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    finding_id: str
    text: str
    requirement_ids: tuple[str, ...]
    supersedes: tuple[str, ...] = ()
    accepted_by: str
    accepted_at: str


class ProjectInputManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    revision: int
    requirements_snapshot_id: str | None = None
    context_snapshot_id: str | None = None
    decisions: tuple[ProjectDecision, ...] = ()


class RequirementCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(min_length=1, max_length=160)
    previous_text: str = Field(min_length=1, max_length=20000)
    text: str = Field(min_length=1, max_length=20000)


class AcceptProjectFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_project_revision: int = Field(ge=0)
    finding_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(min_length=1, max_length=1000)
    corrections: list[RequirementCorrection] = Field(default_factory=list, max_length=100)
    supersedes: list[str] = Field(default_factory=list, max_length=100)
    resolution: str = Field(default="", max_length=1000)

    @field_validator("decision", "resolution")
    @classmethod
    def non_sensitive(cls, value):
        from .knowledge import KnowledgeDraft

        return KnowledgeDraft(text=value, stages=["test_cases"]).text if value else value


class ResumeProjectSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=160)


class ProjectSyncAdmissionAttempt(BaseModel):
    operation_id: str
    fence: int = Field(ge=1)
    state: Literal["running", "interrupted"]
    change_id: str
    stage: Literal["use_cases", "test_cases"]
    request_id: str


class ProjectSyncAdmissionStatus(BaseModel):
    enabled: bool
    blocked: bool = False
    attempt: ProjectSyncAdmissionAttempt | None = None


class ProjectSyncAdmissionReleaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fence: int = Field(strict=True, ge=1)


class ProjectSyncAdmissionReleaseResult(BaseModel):
    released: Literal[True] = True
