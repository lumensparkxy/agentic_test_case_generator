from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from .requirements import (
    RequirementAnalysis,
    RequirementCoveragePlan,
    ReviewResult,
    WorkflowDiagnostics,
    WorkflowSettings,
)


ProjectStageName = Literal["requirements", "context", "use_cases", "impact_analysis", "test_cases", "execution", "reports"]


class QaProjectStageState(BaseModel):
    current_snapshot_id: Optional[str] = None
    version: int = 0
    approved: bool = False
    stale: bool = False
    stale_reason: Optional[str] = None
    updated_at: Optional[datetime] = None
    operation: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QaProjectSummary(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    status: Literal["active", "archived"] = "active"
    owner_user_id: str
    current_revision: int = 0
    created_at: datetime
    updated_at: datetime
    stage_state: Dict[ProjectStageName, QaProjectStageState] = Field(default_factory=dict)


class QaProjectStageSnapshot(BaseModel):
    snapshot_id: str
    project_id: str
    stage: ProjectStageName
    version: int
    project_revision: int
    operation: str
    approved: bool = False
    source_snapshot_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    source_event_id: Optional[str] = None
    request_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QaProjectTimelineEvent(BaseModel):
    event_id: str
    project_id: str
    event_type: str
    stage: Optional[ProjectStageName] = None
    summary: str
    project_revision: int
    snapshot_id: Optional[str] = None
    run_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class QaProjectExecutionRun(BaseModel):
    run_record_id: str
    project_id: str
    run_id: str
    target_environment: str
    target_base_url: Optional[str] = None
    project_revision: int
    test_case_count: int = 0
    status: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    snapshot_id: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    selected_test_case_ids: List[str] = Field(default_factory=list)
    workflow_run_id: Optional[str] = None
    source_event_id: Optional[str] = None
    request_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    created_at: datetime


class QaProjectDetail(QaProjectSummary):
    current_snapshots: Dict[ProjectStageName, QaProjectStageSnapshot] = Field(default_factory=dict)
    timeline: List[QaProjectTimelineEvent] = Field(default_factory=list)
    execution_runs: List[QaProjectExecutionRun] = Field(default_factory=list)


class QaProjectListResponse(BaseModel):
    projects: List[QaProjectSummary] = Field(default_factory=list)


class QaProjectCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=1000)


class QaProjectUpdateInput(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[Literal["active", "archived"]] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


class QaProjectUseCaseSnapshotInput(BaseModel):
    requirement_analysis: List[RequirementAnalysis] = Field(default_factory=list)
    coverage_plan: List[RequirementCoveragePlan] = Field(default_factory=list)
    review: ReviewResult = Field(default_factory=ReviewResult)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)
    approved: bool = False
    source_snapshot_id: Optional[str] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


__all__ = [
    "ProjectStageName",
    "QaProjectStageState",
    "QaProjectSummary",
    "QaProjectStageSnapshot",
    "QaProjectTimelineEvent",
    "QaProjectExecutionRun",
    "QaProjectDetail",
    "QaProjectListResponse",
    "QaProjectCreateInput",
    "QaProjectUpdateInput",
    "QaProjectUseCaseSnapshotInput",
]
