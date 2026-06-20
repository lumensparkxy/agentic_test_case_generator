from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from .grounding import EnrichInput
from .requirements import (
    Requirement,
    RequirementAnalysis,
    RequirementCoveragePlan,
    ReviewResult,
    WorkflowDiagnostics,
    WorkflowIteration,
    WorkflowSettings,
)


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
    linked_requirement_ids: List[str] = Field(default_factory=list)  # Structured requirement traceability
    scenario_refs: List[str] = Field(default_factory=list)  # Coverage-plan scenario IDs implemented by this case
    source_refs: Optional[List[str]] = None  # Grounded context artifact IDs used by this test case
    artifact_set_id: Optional[str] = None
    artifact_item_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    artifact_version_number: Optional[int] = None


class TestCasesOutput(BaseModel):
    test_cases: List[TestCase] = Field(default_factory=list)


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
    project_id: Optional[str] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)
    requirement_analysis: List[RequirementAnalysis] = Field(default_factory=list)
    coverage_plan: List[RequirementCoveragePlan] = Field(default_factory=list)


class RefineTestCasesInput(BaseModel):
    requirements: List[Requirement]
    test_cases: List[TestCase]
    template: TestCaseTemplate
    context: Optional[EnrichInput] = None
    feedback: str
    workflow_settings: Optional[WorkflowSettings] = None
    project_id: Optional[str] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


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


__all__ = [
    "TestStep",
    "TestCase",
    "TestCasesOutput",
    "TestCaseTemplate",
    "GenerateTestCasesInput",
    "RefineTestCasesInput",
    "GenerateTestCasesResponse",
]
