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
    delivery_intent: Literal["reviewed_design"] | None = None


class DesignDeliveryEvidence(BaseModel):
    schema_version: Literal[1] = 1
    delivery_kind: Literal["reviewed_design"] = "reviewed_design"
    legacy_approved: bool
    suite_snapshot_id: str
    source_freshness: Literal["current"] = "current"
    design_status: Literal["complete"] = "complete"
    scenario_decision: Literal["approved"] = "approved"
    scenario_snapshot_id: str
    scenario_review_id: str
    human_acceptance: Literal["approved", "unavailable"]
    declared_setup: Literal["blocked", "not_assessed", "unknown"]
    required_requirement_ids: list[str]
    deferred_requirement_ids: list[str]
    excluded_requirement_ids: list[str]
    active_case_count: int = Field(ge=0)
    deprecated_case_count: int = Field(ge=0)
    target_readiness: Literal["unknown"] = "unknown"


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
    design_delivery: DesignDeliveryEvidence | None = None


class ExportTestCasesDocument(BaseModel):
    export_format: Literal["test_cases_v1"] = "test_cases_v1"
    total_count: int
    test_cases: List[dict[str, Any]]
    audit_metadata: ExportAuditMetadata


FileReportFormat = Literal["json", "csv", "excel"]
FileReportDeliveryKind = Literal["reviewed_design", "approved", "draft", "unknown"]


class SavedFileReportCaseVersion(BaseModel):
    case_id: str
    case_version_id: str
    case_version_number: int


class SavedFileReportEvidence(BaseModel):
    source_snapshot_ids: dict[str, str]
    suite_snapshot_id: str | None
    exported_at: datetime
    design_delivery: DesignDeliveryEvidence | None
    human_review_status: Literal["approved", "changes_requested", "unavailable"]
    machine_review_status: Literal["approved", "failed", "unavailable"]
    draft_override_reason: str | None
    execution_status: str
    execution_run_ids: list[str]
    requirement_ids: list[str]
    case_versions: list[SavedFileReportCaseVersion]


class SavedFileReport(BaseModel):
    project_id: str
    report_id: str
    format: FileReportFormat
    created_at: datetime
    project_revision: int = Field(strict=True, ge=0)
    legacy_approved: bool | None
    delivery_kind: FileReportDeliveryKind
    historical: bool
    stale: bool
    evidence: SavedFileReportEvidence | None


__all__ = [
    "JiraExportInput",
    "JiraExportResponse",
    "ExportTestCasesInput",
    "DesignDeliveryEvidence",
    "ExportAuditMetadata",
    "ExportTestCasesDocument",
    "SavedFileReport",
    "SavedFileReportEvidence",
    "SavedFileReportCaseVersion",
]
