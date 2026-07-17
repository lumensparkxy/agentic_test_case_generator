from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .orchestrator import (
    OrchestratorActionId,
    OrchestratorStageName,
    OrchestratorStageStatus,
)


WorkspaceWorkItemKind = Literal["review", "action", "information"]
WorkspaceReportStatus = Literal["approved", "draft", "stale"]


class WorkspaceProjectSummary(BaseModel):
    project_id: str
    name: str
    project_revision: int = Field(ge=0)
    project_status: Literal["active", "archived"]
    current_stage: OrchestratorStageName
    current_status: OrchestratorStageStatus
    current_snapshot_id: Optional[str] = None
    completed_stage_count: int = Field(ge=0)
    total_stage_count: int = Field(ge=0)
    reason: Optional[str] = None
    updated_at: datetime


class WorkspaceWorkItem(BaseModel):
    work_item_id: str
    kind: WorkspaceWorkItemKind
    project_id: str
    project_name: str
    project_revision: int = Field(ge=0)
    stage: OrchestratorStageName
    status: OrchestratorStageStatus
    action: Optional[OrchestratorActionId] = None
    enabled: bool = False
    primary: bool = False
    count: Optional[int] = Field(default=None, ge=0)
    reason: str
    current_snapshot_id: Optional[str] = None
    updated_at: datetime


class WorkspaceRunSummary(BaseModel):
    run_record_id: str
    run_id: str
    project_id: str
    project_name: str
    project_revision: int = Field(ge=0)
    stage: Literal["execution"] = "execution"
    status: str
    target_environment: str
    selected_count: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    invalid_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    snapshot_id: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    updated_at: datetime


class WorkspaceReportSummary(BaseModel):
    report_id: str
    project_id: str
    project_name: str
    project_revision: int = Field(ge=0)
    stage: Literal["reports"] = "reports"
    status: WorkspaceReportStatus
    report_type: str
    format: Optional[str] = None
    operation: str
    approved: bool = False
    stale: bool = False
    count: Optional[int] = Field(default=None, ge=0)
    source_snapshot_id: Optional[str] = None
    execution_run_ids: List[str] = Field(default_factory=list)
    updated_at: datetime


class WorkspaceSummaryResponse(BaseModel):
    continue_working: Optional[WorkspaceWorkItem] = None
    projects: List[WorkspaceProjectSummary] = Field(default_factory=list)
    work_items: List[WorkspaceWorkItem] = Field(default_factory=list)
    recent_runs: List[WorkspaceRunSummary] = Field(default_factory=list)
    recent_reports: List[WorkspaceReportSummary] = Field(default_factory=list)
    generated_at: datetime


__all__ = [
    "WorkspaceWorkItemKind",
    "WorkspaceReportStatus",
    "WorkspaceProjectSummary",
    "WorkspaceWorkItem",
    "WorkspaceRunSummary",
    "WorkspaceReportSummary",
    "WorkspaceSummaryResponse",
]
