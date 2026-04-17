from datetime import datetime
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
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    is_org_admin: bool = False


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


class UsageReportUserSummary(BaseModel):
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    provider: Optional[str] = None
    total_events: int = 0
    requirements_generated_count: int = 0
    requirements_modified_count: int = 0
    test_cases_generated_count: int = 0
    test_cases_modified_count: int = 0
    latest_event_at: Optional[datetime] = None


class UsageReportGroup(BaseModel):
    scope_type: Literal["organization", "individual"] = "organization"
    scope_key: str
    display_name: str
    organization_domain: Optional[str] = None
    total_events: int = 0
    unique_user_count: int = 0
    requirements_generated_count: int = 0
    requirements_modified_count: int = 0
    test_cases_generated_count: int = 0
    test_cases_modified_count: int = 0
    event_breakdown: Dict[str, int] = Field(default_factory=dict)
    latest_event_at: Optional[datetime] = None
    users: List[UsageReportUserSummary] = Field(default_factory=list)


class UsageReportResponse(BaseModel):
    generated_at: datetime
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    total_groups: int = 0
    total_events: int = 0
    groups: List[UsageReportGroup] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class BillingCatalogEntry(BaseModel):
    billing_key: str
    display_name: str
    unit: Literal["requirement", "test_case", "artifact_source", "request"] = "request"
    units_per_item: int = 0
    billable: bool = True


class BillingUserProfile(BaseModel):
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    provider: Optional[str] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    billing_account_id: Optional[str] = None
    plan_tier: Literal["pilot", "premium", "enterprise"] = "pilot"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BillingAccount(BaseModel):
    account_id: str
    scope_type: Literal["individual", "organization"] = "individual"
    scope_key: str
    owner_user_id: Optional[str] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    plan_tier: Literal["pilot", "premium", "enterprise"] = "pilot"
    account_state: Literal["active", "exhausted", "suspended"] = "active"
    pilot_started_at: Optional[datetime] = None
    pilot_requirement_limit: int = 200
    pilot_requirement_used: int = 0
    pilot_test_case_limit: int = 200
    pilot_test_case_used: int = 0
    pricing_version: str = "pilot-v1"
    balance_units: int = 0
    support_contact_email: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BillingLedgerEntry(BaseModel):
    entry_id: str
    account_id: str
    entry_type: Literal["grant", "purchase", "allocation", "deallocation", "consumption", "reversal", "adjustment"]
    units_delta: int
    reason: Optional[str] = None
    actor_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    request_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    source_event_id: Optional[str] = None
    billing_key: Optional[str] = None
    pricing_version: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class BillingConsumptionRecord(BaseModel):
    consumption_id: str
    account_id: str
    actor_user_id: str
    source_event_id: str
    request_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    billing_key: str
    quantity: int = 0
    unit: str = "request"
    units_charged: int = 0
    pricing_version: Optional[str] = None
    applied: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class BillingAllocation(BaseModel):
    allocation_id: str
    account_id: str
    user_id: str
    allocated_units: int = 0
    consumed_units: int = 0
    granted_by_user_id: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BillingAllocationSummary(BaseModel):
    allocation_id: str
    account_id: str
    user_id: str
    allocated_units: int = 0
    consumed_units: int = 0
    remaining_units: int = 0
    granted_by_user_id: Optional[str] = None
    reason: Optional[str] = None
    updated_at: Optional[datetime] = None


class BillingQuotaSummary(BaseModel):
    limit: int = 0
    used: int = 0
    remaining: int = 0
    exhausted: bool = False


class BillingWalletSummary(BaseModel):
    balance_units: int = 0
    token_unit_size: int = 4
    balance_token_display: str = "0"
    can_spend: bool = False


class BillingEntitlementResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    requirements: BillingQuotaSummary
    test_cases: BillingQuotaSummary
    wallet: BillingWalletSummary
    allocation: Optional[BillingAllocationSummary] = None
    pricing: List[BillingCatalogEntry] = Field(default_factory=list)
    shadow_mode: bool = False
    warnings: List[str] = Field(default_factory=list)


class BillingErrorDetail(BaseModel):
    code: str
    message: str
    contact_email: Optional[str] = None
    plan_tier: Optional[str] = None
    account_id: Optional[str] = None
    requirements_remaining: Optional[int] = None
    test_cases_remaining: Optional[int] = None
    balance_units: Optional[int] = None
    allocation_remaining_units: Optional[int] = None


class BillingCreditGrantRequest(BaseModel):
    scope_type: Literal["individual", "organization"] = "individual"
    target_user_id: Optional[str] = None
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    plan_tier: Literal["pilot", "premium", "enterprise"] = "premium"
    token_quantity: int = Field(default=0, ge=0)
    reason: str = Field(default="Manual credit grant", min_length=1)


class BillingCreditGrantResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    granted_units: int = 0
    granted_token_quantity: str = "0"
    ledger_entry: Optional[BillingLedgerEntry] = None


class BillingAllocationRequest(BaseModel):
    member_user_id: str
    organization_domain: Optional[str] = None
    tenant_id: Optional[str] = None
    token_quantity: int = Field(default=0, ge=0)
    reason: str = Field(default="Manual organization allocation", min_length=1)


class BillingAllocationResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    allocation: BillingAllocationSummary
    ledger_entry: Optional[BillingLedgerEntry] = None


class BillingLedgerResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    entries: List[BillingLedgerEntry] = Field(default_factory=list)


class BillingOrgSummaryResponse(BaseModel):
    generated_at: datetime
    account: BillingAccount
    allocations: List[BillingAllocationSummary] = Field(default_factory=list)
    recent_ledger: List[BillingLedgerEntry] = Field(default_factory=list)
