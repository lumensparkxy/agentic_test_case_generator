"""Knowledge is a separately approved, versioned product input."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .guidance import GuidanceStage


class KnowledgeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    stages: list[GuidanceStage] = Field(min_length=1, max_length=4)
    kind: Literal["convention", "terminology", "business_fact"] = "convention"
    requirement_ids: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value):
        import re

        value = value.strip()
        if not value:
            raise ValueError("Guidance cannot be blank")
        if re.search(r"(?i)(-----BEGIN .*PRIVATE KEY|Bearer\s+\S+|AIza[\w-]{20,}|(?:password|api[_ -]?key|secret|token)\s*[:=]\s*\S+)", value):
            raise ValueError("Do not store credentials or secrets in knowledge")
        return value.replace("\x00", "")


class KnowledgeMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["propose", "revise", "approve", "dismiss", "retire", "delete", "promote", "select", "unselect"]
    entry_id: str | None = None
    base_revision: int = Field(default=0, ge=0)
    draft: KnowledgeDraft | None = None


class KnowledgeEntry(BaseModel):
    id: str
    scope: Literal["project", "personal"]
    project_id: str | None = None
    revision: int
    status: str
    active: dict | None = None
    pending: dict | None = None
    selected: bool = False
    selected_by_projects: list[str] = Field(default_factory=list)


class KnowledgeCollection(BaseModel):
    entries: list[KnowledgeEntry] = Field(default_factory=list)
    revision: str = ""
    skills: list[dict] = Field(default_factory=list)
    features: dict = Field(default_factory=dict)
