from typing import List, Optional, Literal

from pydantic import BaseModel, Field, HttpUrl

from .requirements import Requirement


class ArtifactSource(BaseModel):
    id: str
    source_type: Literal["app", "prototype", "diagram", "image", "note"] = "note"
    label: str
    url: Optional[HttpUrl] = None
    status: Literal["Provided", "Analyzed", "Skipped", "Unavailable"] = "Provided"
    notes: Optional[str] = None


class GroundedUIElement(BaseModel):
    id: str
    source_id: Optional[str] = None
    name: str
    element_type: Literal[
        "Page",
        "Heading",
        "Form",
        "Field",
        "Button",
        "Message",
        "Filter",
        "Navigation",
        "Other",
    ] = "Other"
    description: str
    href: Optional[str] = None


class GroundedApiSurface(BaseModel):
    id: str
    source_id: Optional[str] = None
    name: str
    description: str
    method: Optional[str] = None
    path: Optional[str] = None
    auth_required: Optional[bool] = None


class GroundedWorkflow(BaseModel):
    id: str
    source_id: Optional[str] = None
    name: str
    description: str
    actors: List[str] = Field(default_factory=list)
    states: List[str] = Field(default_factory=list)
    transitions: List[str] = Field(default_factory=list)


class GroundedContext(BaseModel):
    artifact_sources: List[ArtifactSource] = Field(default_factory=list)
    ui_elements: List[GroundedUIElement] = Field(default_factory=list)
    api_surfaces: List[GroundedApiSurface] = Field(default_factory=list)
    workflows: List[GroundedWorkflow] = Field(default_factory=list)
    summary: Optional[str] = None


class EnrichInput(BaseModel):
    requirements: List[Requirement]
    app_link: Optional[HttpUrl] = None
    prototype_link: Optional[HttpUrl] = None
    diagram_links: Optional[List[HttpUrl]] = None
    image_links: Optional[List[HttpUrl]] = None
    notes: Optional[str] = None
    grounded_context: Optional[GroundedContext] = None
    project_id: Optional[str] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


class EnrichResponse(EnrichInput):
    grounded_context: GroundedContext = Field(default_factory=GroundedContext)


__all__ = [
    "ArtifactSource",
    "GroundedUIElement",
    "GroundedApiSurface",
    "GroundedWorkflow",
    "GroundedContext",
    "EnrichInput",
    "EnrichResponse",
]
