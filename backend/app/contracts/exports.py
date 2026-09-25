from datetime import datetime
from typing import Any, List, Optional, Literal

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
    source_snapshot_id: Optional[str] = None


class ExportAuditMetadata(BaseModel):
    schema_version: Literal[1] = 1
    exported_at: datetime
    project_id: Optional[str] = None
    project_revision: Optional[int] = None
    snapshot_id: Optional[str] = None
    snapshot_version: Optional[int] = None
    artifact_set_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    case_versions: List[dict[str, Any]] = Field(default_factory=list)
    human_review_status: Literal["approved", "changes_requested", "unavailable"] = "unavailable"
    machine_review_status: Literal["approved", "failed", "unavailable"] = "unavailable"
    machine_review: Optional[ReviewResult] = None
    is_draft: bool = True
    draft_override_used: bool = False
    draft_override_reason: Optional[str] = None
    suite_complete: Optional[bool] = None
    delivered_case_count: int = 0
    unresolved_task_count: Optional[int] = None
    coverage: dict[str, Any] = Field(default_factory=dict)
    source_mappings: List[dict[str, Any]] = Field(default_factory=list)
    provenance_status: Literal["available", "partial", "unavailable"] = "unavailable"
    source_snapshot_ids: dict[str, str] = Field(default_factory=dict)
    execution_status: str = "unknown"
    execution_run_ids: List[str] = Field(default_factory=list)
    execution_runs: List[dict[str, Any]] = Field(default_factory=list)
    case_status_semantics: str = "Case Ready is a lifecycle value, not suite quality approval or execution readiness. Preview is not execution."


class ExportTestCasesDocument(BaseModel):
    export_format: Literal["test_cases_v1"] = "test_cases_v1"
    total_count: int
    test_cases: List[dict[str, Any]]
    audit_metadata: ExportAuditMetadata


__all__ = [
    "JiraExportInput",
    "JiraExportResponse",
    "ExportTestCasesInput",
    "ExportAuditMetadata",
    "ExportTestCasesDocument",
]
