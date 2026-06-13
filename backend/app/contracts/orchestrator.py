from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


OrchestratorStageName = Literal[
    "requirements",
    "context",
    "use_cases",
    "impact_analysis",
    "test_cases",
    "automation",
    "execution",
    "review",
    "reports",
]


OrchestratorStageStatus = Literal[
    "not_started",
    "ready",
    "blocked",
    "completed",
    "stale",
    "failed",
    "attention_required",
]


OrchestratorActionId = Literal[
    "refine",
    "approve",
    "generate",
    "analyze_impact",
    "apply_update",
    "full_regenerate",
    "automate",
    "execute",
    "review",
    "report",
]


OrchestratorBlockerCode = Literal[
    "missing_project",
    "missing_requirements",
    "missing_use_cases",
    "missing_test_cases",
    "missing_approval",
    "stale_downstream_stage",
    "missing_baseline",
    "failed_execution",
    "unresolved_review",
    "missing_execution",
]


class OrchestratorBlocker(BaseModel):
    code: OrchestratorBlockerCode
    message: str
    stage: Optional[OrchestratorStageName] = None
    action: Optional[OrchestratorActionId] = None
    source_stage: Optional[OrchestratorStageName] = None
    severity: Literal["info", "warning", "blocking"] = "blocking"


class OrchestratorActionRecommendation(BaseModel):
    action: OrchestratorActionId
    label: str
    stage: OrchestratorStageName
    enabled: bool = True
    primary: bool = False
    secondary: bool = False
    reason: str
    blockers: List[OrchestratorBlocker] = Field(default_factory=list)
    agent_kind: Optional[str] = None
    agent_contract_version: Optional[str] = None
    agent_implementation: Optional[str] = None


class OrchestratorStageState(BaseModel):
    stage: OrchestratorStageName
    status: OrchestratorStageStatus = "not_started"
    current_snapshot_id: Optional[str] = None
    version: int = 0
    approved: bool = False
    stale: bool = False
    stale_reason: Optional[str] = None
    operation: Optional[str] = None
    updated_at: Optional[datetime] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    blockers: List[OrchestratorBlocker] = Field(default_factory=list)


class OrchestratorStatusResponse(BaseModel):
    project_id: Optional[str] = None
    project_revision: int = 0
    current_stage: OrchestratorStageName = "requirements"
    stages: Dict[OrchestratorStageName, OrchestratorStageState] = Field(default_factory=dict)
    next_actions: List[OrchestratorActionRecommendation] = Field(default_factory=list)
    blockers: List[OrchestratorBlocker] = Field(default_factory=list)
    has_baseline_test_suite: bool = False
    upstream_changed: bool = False
    changed_upstream_stages: List[OrchestratorStageName] = Field(default_factory=list)
    generated_at: datetime


OrchestratorRunStatus = Literal["running", "blocked", "completed", "failed", "cancelled"]


OrchestratorRunEventType = Literal[
    "run_started",
    "decision_recorded",
    "agent_invoked",
    "approval_recorded",
    "blocked",
    "retry_recorded",
    "checkpoint_saved",
    "action_completed",
    "run_failed",
]


class OrchestratorCheckpointRecord(BaseModel):
    checkpoint_id: str
    run_id: str
    project_id: str
    action: OrchestratorActionId
    stage: OrchestratorStageName
    project_revision: int = 0
    request_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    source_snapshot_ids: Dict[str, Optional[str]] = Field(default_factory=dict)
    output_snapshot_ids: Dict[str, Optional[str]] = Field(default_factory=dict)
    agent_output_refs: List[Dict[str, Any]] = Field(default_factory=list)
    execution_run_ids: List[str] = Field(default_factory=list)
    blockers: List[OrchestratorBlocker] = Field(default_factory=list)
    next_action: Optional[OrchestratorActionId] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class OrchestratorRunEvent(BaseModel):
    event_id: str
    run_id: str
    project_id: str
    event_type: OrchestratorRunEventType
    summary: str
    action: Optional[OrchestratorActionId] = None
    stage: Optional[OrchestratorStageName] = None
    project_revision: int = 0
    checkpoint_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class OrchestratorRunRecord(BaseModel):
    run_id: str
    project_id: str
    action: OrchestratorActionId
    status: OrchestratorRunStatus = "running"
    current_stage: OrchestratorStageName
    current_action: OrchestratorActionId
    project_revision: int = 0
    request_id: str
    actor_user_id: str
    idempotency_key: str
    current_checkpoint_id: Optional[str] = None
    produced_snapshot_ids: Dict[str, Optional[str]] = Field(default_factory=dict)
    execution_run_ids: List[str] = Field(default_factory=list)
    blockers: List[OrchestratorBlocker] = Field(default_factory=list)
    next_unblock_action: Optional[OrchestratorActionId] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class OrchestratorRunListResponse(BaseModel):
    runs: List[OrchestratorRunRecord] = Field(default_factory=list)
    events: List[OrchestratorRunEvent] = Field(default_factory=list)
    checkpoints: List[OrchestratorCheckpointRecord] = Field(default_factory=list)


__all__ = [
    "OrchestratorStageName",
    "OrchestratorStageStatus",
    "OrchestratorActionId",
    "OrchestratorBlockerCode",
    "OrchestratorBlocker",
    "OrchestratorActionRecommendation",
    "OrchestratorStageState",
    "OrchestratorStatusResponse",
    "OrchestratorRunStatus",
    "OrchestratorRunEventType",
    "OrchestratorCheckpointRecord",
    "OrchestratorRunEvent",
    "OrchestratorRunRecord",
    "OrchestratorRunListResponse",
]
