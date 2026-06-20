from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, HttpUrl


class Requirement(BaseModel):
    id: str
    text: str
    source_system: Optional[Literal["file", "jira", "azure_devops"]] = None
    source_issue_key: Optional[str] = None
    source_issue_type: Optional[str] = None
    source_parent_key: Optional[str] = None
    source_parent_title: Optional[str] = None
    source_issue_url: Optional[HttpUrl] = None
    source_issue_updated_at: Optional[datetime] = None
    source_path: Optional[str] = None
    source_section: Optional[str] = None
    source_excerpt: Optional[str] = None
    source_hierarchy: List[str] = Field(default_factory=list)
    parent_requirement_id: Optional[str] = None
    review_status: Literal["Draft", "Needs Review", "Approved", "Rejected"] = "Draft"
    quality_flags: List[str] = Field(default_factory=list)
    sync_target_issue_key: Optional[str] = None
    artifact_set_id: Optional[str] = None
    artifact_item_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    artifact_version_number: Optional[int] = None


class RequirementExtractionItem(BaseModel):
    id: str
    text: str
    source_path: Optional[str] = None
    source_section: Optional[str] = None
    source_excerpt: Optional[str] = None
    source_hierarchy: List[str] = Field(default_factory=list)
    parent_requirement_id: Optional[str] = None
    review_status: Literal["Draft", "Needs Review", "Approved", "Rejected"] = "Draft"
    quality_flags: List[str] = Field(default_factory=list)


class RequirementsOutput(BaseModel):
    requirements: List[RequirementExtractionItem] = Field(default_factory=list)


class BusinessRule(BaseModel):
    id: str
    requirement_id: str
    title: str
    description: str
    rule_type: Literal[
        "Business",
        "Validation",
        "Authorization",
        "State Transition",
        "Integration",
        "Notification",
        "Data",
        "Constraint",
        "Other",
    ] = "Business"


class FieldConstraint(BaseModel):
    id: str
    requirement_id: str
    field_name: str
    description: str
    constraint_type: Literal[
        "Required",
        "Format",
        "Length",
        "Range",
        "File Type",
        "File Size",
        "Allowed Values",
        "Uniqueness",
        "Dependency",
        "Other",
    ] = "Other"
    operator: Optional[str] = None
    value: Optional[str] = None
    negative_example: Optional[str] = None


class RolePermission(BaseModel):
    id: str
    requirement_id: str
    role: str
    action: str
    effect: Literal["Allow", "Deny", "Conditional"] = "Allow"
    conditions: Optional[str] = None


class StateTransition(BaseModel):
    id: str
    requirement_id: str
    entity: str
    from_state: str
    to_state: str
    trigger: Optional[str] = None
    guards: Optional[str] = None


class RiskSignal(BaseModel):
    id: str
    requirement_id: str
    title: str
    rationale: str
    category: Literal[
        "Security",
        "Data Integrity",
        "Availability",
        "Usability",
        "Compliance",
        "Workflow",
        "Validation",
        "Integration",
        "Other",
    ] = "Other"
    severity: Literal["Critical", "High", "Medium", "Low"] = "Medium"


class RequirementAnalysis(BaseModel):
    requirement_id: str
    requirement_text: str
    business_rules: List[BusinessRule] = Field(default_factory=list)
    field_constraints: List[FieldConstraint] = Field(default_factory=list)
    role_permissions: List[RolePermission] = Field(default_factory=list)
    state_transitions: List[StateTransition] = Field(default_factory=list)
    risk_signals: List[RiskSignal] = Field(default_factory=list)
    suggested_scenarios: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)


class ParseResponse(BaseModel):
    source_name: str
    raw_text: str
    requirements: List[Requirement]


class ReviewResult(BaseModel):
    approved: bool = False
    score: int = 0
    threshold: int = 0
    summary: str = ""
    blocking_issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    unmet_criteria: List[str] = Field(default_factory=list)


class WorkflowSettings(BaseModel):
    approval_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    max_iterations: Optional[int] = Field(default=None, ge=1, le=20)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=900)
    stall_iteration_limit: Optional[int] = Field(default=None, ge=1, le=20)
    retry_attempts: Optional[int] = Field(default=None, ge=0, le=5)


class WorkflowDiagnostics(BaseModel):
    status: Literal["completed", "partial", "fallback", "failed"] = "completed"
    used_fallback: bool = False
    failure_reason: Optional[str] = None
    timed_out: bool = False
    stalled: bool = False
    max_iterations_reached: bool = False
    parser_failures: List[str] = Field(default_factory=list)
    parser_recoveries: List[str] = Field(default_factory=list)
    recovery_reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    best_iteration: Optional[int] = None
    attempt_count: int = 1


class WorkflowIteration(BaseModel):
    iteration: int
    actor: str
    approved: bool = False
    score: int = 0
    threshold: int = 0
    summary: str = ""
    artifact_count: int = 0
    artifact_ids: List[str] = Field(default_factory=list)
    blocking_issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class ScenarioIntent(BaseModel):
    id: str
    requirement_id: str
    scenario_type: Literal[
        "Happy Path",
        "Negative",
        "Boundary",
        "Validation",
        "Authorization",
        "State Transition",
        "Integration",
        "Error Handling",
        "Data Variation",
    ] = "Happy Path"
    title: str
    objective: str
    priority: Literal["Critical", "High", "Medium", "Low"] = "Medium"
    must_have: bool = True


class RequirementCoveragePlan(BaseModel):
    requirement_id: str
    requirement_text: str
    scenarios: List[ScenarioIntent] = Field(default_factory=list)


class RequirementAnalysisOutput(BaseModel):
    requirement_analysis: List[RequirementAnalysis] = Field(default_factory=list)


class RequirementCoveragePlanOutput(BaseModel):
    coverage_plan: List[RequirementCoveragePlan] = Field(default_factory=list)


class RequirementsWorkflowResponse(ParseResponse):
    source_names: List[str] = Field(default_factory=list)
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)


__all__ = [
    "Requirement",
    "RequirementExtractionItem",
    "RequirementsOutput",
    "BusinessRule",
    "FieldConstraint",
    "RolePermission",
    "StateTransition",
    "RiskSignal",
    "RequirementAnalysis",
    "ParseResponse",
    "ReviewResult",
    "WorkflowSettings",
    "WorkflowDiagnostics",
    "WorkflowIteration",
    "ScenarioIntent",
    "RequirementCoveragePlan",
    "RequirementAnalysisOutput",
    "RequirementCoveragePlanOutput",
    "RequirementsWorkflowResponse",
]
