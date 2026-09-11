"""Immutable run inputs; knowledge approval is separate from artifact approval."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

GuidanceStage = Literal["requirements", "use_cases", "test_cases", "automation"]


class FrozenGuidance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SkillGuidance(FrozenGuidance):
    id: str
    version: str
    stage: GuidanceStage
    description: str
    content_hash: str
    instructions: str


class MemoryGuidance(FrozenGuidance):
    stages: tuple[GuidanceStage, ...] = ("requirements", "use_cases", "test_cases", "automation")
    id: str
    revision: int
    scope: Literal["project", "personal"]
    text: str
    source: str
    requirement_ids: tuple[str, ...] = ()
    reason: str


class OmittedGuidance(FrozenGuidance):
    id: str
    revision: int = 0
    reason: str


class GuidanceManifest(FrozenGuidance):
    manifest_id: str
    stage: GuidanceStage
    model: str
    skills_enabled: bool = False
    memory_enabled: bool = False
    memory_bypassed: bool = False
    project_id: str | None = None
    knowledge_revision: str = ""
    skills: tuple[SkillGuidance, ...] = ()
    memories: tuple[MemoryGuidance, ...] = ()
    omitted: tuple[OmittedGuidance, ...] = ()


class GuidanceRunOptions(BaseModel):
    memory_bypass: bool = False
