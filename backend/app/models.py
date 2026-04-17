from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, HttpUrl


class Requirement(BaseModel):
    id: str
    text: str
    artifact_set_id: Optional[str] = None
    artifact_item_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    artifact_version_number: Optional[int] = None


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
    element_type: Literal["Page", "Form", "Field", "Button", "Message", "Filter", "Navigation", "Other"] = "Other"
    description: str


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


class AuthUser(BaseModel):
    sub: str
    email: Optional[str] = None
    name: str
    picture: Optional[str] = None
    provider: Optional[str] = None
    email_verified: Optional[bool] = None


class GoogleLoginRequest(BaseModel):
    credential: str
    client_id: Optional[str] = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: AuthUser


class LogoutResponse(BaseModel):
    status: str = "ok"


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


class EnrichInput(BaseModel):
    requirements: List[Requirement]
    app_link: Optional[HttpUrl] = None
    prototype_link: Optional[HttpUrl] = None
    diagram_links: Optional[List[HttpUrl]] = None
    image_links: Optional[List[HttpUrl]] = None
    notes: Optional[str] = None
    grounded_context: Optional[GroundedContext] = None


class EnrichResponse(EnrichInput):
    grounded_context: GroundedContext = Field(default_factory=GroundedContext)


class TestStep(BaseModel):
    step: int
    action: str
    expected: str
    test_data: Optional[str] = None  # Specific test data for this step


class TestCase(BaseModel):
    """
    Test case model based on industry standards (JIRA/Xray/TestRail).
    """
    id: str
    title: str
    description: Optional[str] = None  # What the test verifies
    priority: Literal["Critical", "High", "Medium", "Low"] = "Medium"
    type: Literal["Functional", "Integration", "E2E", "Regression", "Smoke", "Security", "Performance", "Usability", "UAT"] = "Functional"
    status: Literal["Draft", "Ready", "In Review", "Approved", "Deprecated"] = "Draft"
    preconditions: Optional[str] = None
    steps: List[TestStep]
    expected_result: Optional[str] = None  # Overall expected outcome
    test_data: Optional[str] = None  # Global test data needed
    estimated_time: Optional[str] = None  # e.g., "5 mins", "15 mins"
    automation_status: Literal["Manual", "Automated", "To Be Automated"] = "Manual"
    component: Optional[str] = None  # Module/feature area
    tags: Optional[List[str]] = None  # Includes linked requirement IDs
    source_refs: Optional[List[str]] = None  # Grounded context artifact IDs used by this test case
    artifact_set_id: Optional[str] = None
    artifact_item_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    artifact_version_number: Optional[int] = None


class TestCaseTemplate(BaseModel):
    name: str
    format: str
    fields: List[str]


class GenerateTestCasesInput(BaseModel):
    requirements: List[Requirement]
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    feedback: Optional[str] = None  # Human feedback for refinement
    workflow_settings: Optional[WorkflowSettings] = None


class RefineTestCasesInput(BaseModel):
    requirements: List[Requirement]
    test_cases: List[TestCase]
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    feedback: str
    workflow_settings: Optional[WorkflowSettings] = None


class GenerateTestCasesResponse(BaseModel):
    test_cases: List[TestCase]
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_plan: List[RequirementCoveragePlan] = Field(default_factory=list)
    requirement_analysis: List[RequirementAnalysis] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)


class RequirementsWorkflowResponse(ParseResponse):
    source_names: List[str] = Field(default_factory=list)
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    workflow_settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    workflow_diagnostics: WorkflowDiagnostics = Field(default_factory=WorkflowDiagnostics)


class JiraExportInput(BaseModel):
    project_key: str
    issue_type: str
    test_cases: List[TestCase]


class JiraExportResponse(BaseModel):
    status: str
    message: str


class AutomationInput(BaseModel):
    test_cases: List[TestCase]
    target_base_url: Optional[HttpUrl] = None


class AutomationResponse(BaseModel):
    status: str
    files: List[str]
    notes: Optional[str] = None
