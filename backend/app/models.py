from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, HttpUrl


class Requirement(BaseModel):
    id: str
    text: str


class AuthUser(BaseModel):
    sub: str
    email: str
    name: str
    picture: Optional[str] = None


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


class EnrichInput(BaseModel):
    requirements: List[Requirement]
    app_link: Optional[HttpUrl] = None
    prototype_link: Optional[HttpUrl] = None
    diagram_links: Optional[List[HttpUrl]] = None
    image_links: Optional[List[HttpUrl]] = None
    notes: Optional[str] = None


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


class TestCaseTemplate(BaseModel):
    name: str
    format: str
    fields: List[str]


class GenerateTestCasesInput(BaseModel):
    requirements: List[Requirement]
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    feedback: Optional[str] = None  # Human feedback for refinement


class RefineTestCasesInput(BaseModel):
    requirements: List[Requirement]
    test_cases: List[TestCase]
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    feedback: str


class GenerateTestCasesResponse(BaseModel):
    test_cases: List[TestCase]
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)


class RequirementsWorkflowResponse(ParseResponse):
    source_names: List[str] = Field(default_factory=list)
    approved: bool = False
    review: ReviewResult = Field(default_factory=ReviewResult)
    iteration_history: List[WorkflowIteration] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)


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
