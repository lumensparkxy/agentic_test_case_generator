from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from ..models import (
    AutomationResponse,
    EnrichInput,
    ExecutionPreviewInput,
    ExecutionPreviewResponse,
    ExecutionRunInput,
    ExecutionRunResponse,
    ImpactAnalysisResult,
    QaProjectStageSnapshot,
    Requirement,
    RequirementAnalysis,
    RequirementCoveragePlan,
    ReviewResult,
    TestCase,
    TestCaseTemplate,
    WorkflowDiagnostics,
    WorkflowIteration,
    WorkflowSettings,
)

SPECIALIST_AGENT_CONTRACT_VERSION = "2026-06-13.v1"

SpecialistAgentKind = Literal[
    "requirements",
    "use_cases",
    "impact",
    "test_cases",
    "automation",
    "execution",
    "review",
    "report",
]
SpecialistAgentImplementation = Literal["local", "adk", "external"]
SpecialistTaskStatus = Literal["completed", "failed", "skipped"]
SpecialistDiagnosticSeverity = Literal["info", "warning", "error"]
SpecialistArtifactRole = Literal["source", "baseline", "current", "input", "output", "evidence"]
SpecialistStageName = Literal[
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
ExecutionTaskMode = Literal["preview", "run"]
ReportTaskFormat = Literal["json", "csv", "excel", "jira", "execution_summary"]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpecialistArtifactRef(StrictContractModel):
    role: SpecialistArtifactRole = "input"
    stage: Optional[SpecialistStageName] = None
    snapshot_id: Optional[str] = Field(default=None, min_length=1)
    artifact_set_id: Optional[str] = Field(default=None, min_length=1)
    artifact_item_id: Optional[str] = Field(default=None, min_length=1)
    artifact_version_id: Optional[str] = Field(default=None, min_length=1)
    artifact_version_number: Optional[int] = Field(default=None, ge=1)
    item_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_reference(self):
        if not any(
            [
                self.snapshot_id,
                self.artifact_set_id,
                self.artifact_item_id,
                self.artifact_version_id,
                self.item_ids,
            ]
        ):
            raise ValueError("Artifact references require a snapshot id, artifact id, version id, or item id.")
        return self


class SpecialistTaskTrace(StrictContractModel):
    request_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    actor_user_id: str = Field(min_length=1)
    actor_email: Optional[str] = None
    project_id: Optional[str] = None
    project_revision: Optional[int] = Field(default=None, ge=0)
    source_event_id: Optional[str] = None
    source_snapshot_ids: Dict[str, str] = Field(default_factory=dict)
    artifact_refs: List[SpecialistArtifactRef] = Field(default_factory=list)


class SpecialistDiagnostic(StrictContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: SpecialistDiagnosticSeverity = "error"
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class SpecialistOutputBase(StrictContractModel):
    output_artifact_refs: List[SpecialistArtifactRef] = Field(default_factory=list)


class RequirementTaskInput(StrictContractModel):
    text: Optional[str] = None
    document_count: int = Field(default=1, ge=1)
    existing_requirements: List[Requirement] = Field(default_factory=list)
    feedback: Optional[str] = None
    workflow_settings: Optional[WorkflowSettings] = None

    @model_validator(mode="after")
    def require_parse_or_refine_payload(self):
        if self.feedback and self.existing_requirements:
            return self
        if (self.text or "").strip():
            return self
        raise ValueError("Requirement tasks require source text or existing requirements with feedback.")


class RequirementTaskOutput(SpecialistOutputBase):
    requirements: List[Requirement]
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)


class UseCaseTaskInput(StrictContractModel):
    requirements: List[Requirement] = Field(min_length=1)
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    feedback: Optional[str] = None
    workflow_settings: Optional[WorkflowSettings] = None


class UseCaseTaskOutput(SpecialistOutputBase):
    requirement_analysis: List[RequirementAnalysis] = Field(default_factory=list)
    coverage_plan: List[RequirementCoveragePlan] = Field(default_factory=list)
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)


class ImpactTaskInput(StrictContractModel):
    current_requirements_snapshot: Optional[QaProjectStageSnapshot] = None
    current_use_cases_snapshot: Optional[QaProjectStageSnapshot] = None
    current_context_snapshot: Optional[QaProjectStageSnapshot] = None
    baseline_requirements_snapshot: Optional[QaProjectStageSnapshot] = None
    baseline_use_cases_snapshot: Optional[QaProjectStageSnapshot] = None
    baseline_context_snapshot: Optional[QaProjectStageSnapshot] = None
    test_cases_snapshot: Optional[QaProjectStageSnapshot] = None


class ImpactTaskOutput(SpecialistOutputBase):
    analysis: ImpactAnalysisResult


class TestCaseTaskInput(StrictContractModel):
    requirements: List[Requirement] = Field(min_length=1)
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    existing_test_cases: List[TestCase] = Field(default_factory=list)
    feedback: Optional[str] = None
    workflow_settings: Optional[WorkflowSettings] = None


class TestCaseTaskOutput(SpecialistOutputBase):
    test_cases: List[TestCase]
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_plan: List[RequirementCoveragePlan] = Field(default_factory=list)
    requirement_analysis: List[RequirementAnalysis] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)


class AutomationTaskInput(StrictContractModel):
    test_cases: List[TestCase]
    target_base_url: Optional[HttpUrl] = None


class AutomationTaskOutput(SpecialistOutputBase):
    automation: AutomationResponse


class ExecutionTaskInput(StrictContractModel):
    mode: ExecutionTaskMode = "preview"
    preview: Optional[ExecutionPreviewInput] = None
    run: Optional[ExecutionRunInput] = None

    @model_validator(mode="after")
    def require_matching_execution_payload(self):
        if self.mode == "preview" and self.preview is not None:
            return self
        if self.mode == "run" and self.run is not None:
            return self
        raise ValueError("Execution tasks require preview payload for preview mode or run payload for run mode.")


class ExecutionTaskOutput(SpecialistOutputBase):
    mode: ExecutionTaskMode
    preview: Optional[ExecutionPreviewResponse] = None
    run: Optional[ExecutionRunResponse] = None


class ReviewTaskInput(StrictContractModel):
    stage: SpecialistStageName
    review: ReviewResult
    artifact_payload: Dict[str, Any] = Field(default_factory=dict)
    traceability_ids: List[str] = Field(default_factory=list)


class ReviewTaskOutput(SpecialistOutputBase):
    approved: bool = False
    review: ReviewResult
    blockers: List[str] = Field(default_factory=list)
    traceability_ids: List[str] = Field(default_factory=list)


class ReportTaskInput(StrictContractModel):
    format: ReportTaskFormat = "json"
    test_cases: List[TestCase] = Field(default_factory=list)
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    execution_run: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[SpecialistArtifactRef] = Field(default_factory=list)


class ReportTaskOutput(SpecialistOutputBase):
    format: ReportTaskFormat
    status: Literal["generated", "skipped", "stubbed"] = "generated"
    content_length: Optional[int] = Field(default=None, ge=0)
    message: Optional[str] = None
    evidence_refs: List[SpecialistArtifactRef] = Field(default_factory=list)
    traceability_ids: List[str] = Field(default_factory=list)


class SpecialistTaskResult(StrictContractModel):
    task_id: str = Field(min_length=1)
    agent_kind: SpecialistAgentKind
    implementation: SpecialistAgentImplementation = "local"
    contract_version: str = SPECIALIST_AGENT_CONTRACT_VERSION
    status: SpecialistTaskStatus
    trace: SpecialistTaskTrace
    output_type: Optional[str] = None
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    output_artifact_refs: List[SpecialistArtifactRef] = Field(default_factory=list)
    diagnostics: List[SpecialistDiagnostic] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
