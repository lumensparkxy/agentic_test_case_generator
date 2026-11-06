"""Evidence-backed findings and non-secret execution prerequisites."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .knowledge import KnowledgeDraft


class ExecutionPrerequisite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    kind: Literal["test_data", "credential", "role", "environment", "setup", "cleanup"]
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    # A reference is a contract, not evidence that the resource exists.
    status: Literal["missing", "configured"] = "missing"

    @field_validator("reference")
    @classmethod
    def reference_only(cls, value):
        import re

        if value and not re.fullmatch(r"(?:env:PETF_TEST_[A-Z0-9_]+|(?:fixture|context|setup|secret):[A-Za-z0-9_./-]+)", value):
            raise ValueError("Use a non-secret reference such as env:PETF_TEST_LOGIN or fixture:employee")
        return value

    @field_validator("description")
    @classmethod
    def non_sensitive(cls, value):
        if value:
            KnowledgeDraft(text=value, stages=["test_cases"])
        return value


class QualityFindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_project_revision: int = Field(ge=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    category: Literal[
        "unknown", "missing_data", "access", "environment", "ambiguous_requirement", "unsupported_assumption", "script_defect", "application_defect"
    ]
    summary: str = Field(min_length=1, max_length=1000)
    expected_behavior: str = Field(min_length=1, max_length=1000)
    test_case_ids: list[str] = Field(default_factory=list, max_length=100)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    environment: str = Field(default="default", min_length=1, max_length=120)
    prerequisites: list[ExecutionPrerequisite] = Field(default_factory=list, max_length=30)

    @field_validator("summary", "expected_behavior", "environment")
    @classmethod
    def non_sensitive(cls, value):
        KnowledgeDraft(text=value, stages=["test_cases"])
        return value


class QualityFindingAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["apply", "verify", "propose_lesson", "classify"]
    base_project_revision: int = Field(ge=0)
    prerequisites: list[ExecutionPrerequisite] = Field(default_factory=list, max_length=30)
    category: (
        Literal["unknown", "missing_data", "access", "environment", "ambiguous_requirement", "unsupported_assumption", "script_defect", "application_defect"]
        | None
    ) = None
    verification_snapshot_id: str | None = Field(default=None, max_length=160)
    lesson: KnowledgeDraft | None = None
