from typing import List, Optional

from pydantic import BaseModel, Field

from .requirements import ReviewResult
from .test_cases import TestCase


class JiraExportInput(BaseModel):
    project_key: str
    issue_type: str
    test_cases: List[TestCase]


class JiraExportResponse(BaseModel):
    status: str
    message: str


class ExportTestCasesInput(BaseModel):
    test_cases: List[TestCase]
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    draft_override_requested: bool = False
    draft_override_reason: Optional[str] = Field(default=None, max_length=1000)
    project_id: Optional[str] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


__all__ = [
    "JiraExportInput",
    "JiraExportResponse",
    "ExportTestCasesInput",
]
