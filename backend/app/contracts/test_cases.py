from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal
from uuid import uuid4

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
    generation_source: Optional[
        Literal[
            "model",
            "model_recovered",
            "parallel_retry",
            "deterministic_full_fallback",
            "deterministic_coverage_completion",
        ]
    ] = None
    generation_pass_id: Optional[str] = None
    source_shard_id: Optional[str] = None
    source_case_id: Optional[str] = None
    coverage_completion_reason: Optional[str] = None
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


class TestCaseGenerationShardEvidence(BaseModel):
    shard_id: str
    requirement_count: int = 0
    planned_scenario_count: int = 0
    raw_output_count: int = 0
    fallback_case_count: int = 0
    parser_status: Literal["not_run", "clean", "recovered", "failed"] = "not_run"
    review_status: Literal["not_run", "approved", "rejected", "fallback"] = "not_run"
    used_fallback: bool = False
    failed: bool = False
    failure_reason: Optional[str] = None
    parser_failure_count: int = 0
    parser_recovery_count: int = 0
    warning_count: int = 0


class TestCaseGenerationPassEvidence(BaseModel):
    pass_id: str = Field(default_factory=lambda: str(uuid4()))
    pass_type: Literal[
        "sequential",
        "parallel_direct",
        "parallel_retry",
        "deterministic_full_fallback",
        "deterministic_coverage_completion",
        "refinement",
    ]
    model_name: Optional[str] = None
    requirement_count: int = 0
    coverage_plan_count: int = 0
    planned_scenario_count: int = 0
    prompt_metadata: Dict[str, Any] = Field(default_factory=dict)
    raw_output_summary: Dict[str, Any] = Field(default_factory=dict)
    model_case_count_before_review: int = 0
    model_case_count_after_review: int = 0
    merged_case_count: int = 0
    deterministic_additions_total: int = 0
    deterministic_must_have_additions: int = 0
    deterministic_optional_additions: int = 0
    parser_failure_count: int = 0
    parser_recovery_count: int = 0
    review_status: Literal["not_run", "approved", "rejected", "fallback"] = "not_run"
    review_score: Optional[int] = None
    review_threshold: Optional[int] = None
    approved: bool = False
    used_fallback: bool = False
    failure_reason: Optional[str] = None
    shards: List[TestCaseGenerationShardEvidence] = Field(default_factory=list)


class TestCaseGenerationEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    operation: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_name: Optional[str] = None
    generation_settings: Dict[str, Any] = Field(default_factory=dict)
    requirement_count: int = 0
    coverage_plan_count: int = 0
    planned_scenario_count: int = 0
    model_case_count_before_review: int = 0
    model_case_count_after_merge: int = 0
    final_test_case_count: int = 0
    deterministic_additions_total: int = 0
    deterministic_must_have_additions: int = 0
    deterministic_optional_additions: int = 0
    parser_failure_count: int = 0
    parser_recovery_count: int = 0
    final_status: Optional[str] = None
    recovery_reason: Optional[str] = None
    warning_count: int = 0
    payload_strategy: str = "Raw prompts and raw model outputs are not stored; evidence keeps counts, pass status, shard status, and bounded diagnostics."
    passes: List[TestCaseGenerationPassEvidence] = Field(default_factory=list)


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
    generation_evidence: TestCaseGenerationEvidence = Field(default_factory=TestCaseGenerationEvidence)


__all__ = [
    "TestStep",
    "TestCase",
    "TestCasesOutput",
    "TestCaseTemplate",
    "TestCaseGenerationShardEvidence",
    "TestCaseGenerationPassEvidence",
    "TestCaseGenerationEvidence",
    "GenerateTestCasesInput",
    "RefineTestCasesInput",
    "GenerateTestCasesResponse",
]
