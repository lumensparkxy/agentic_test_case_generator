from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, HttpUrl

from .test_cases import TestCase


class ExecutionIssue(BaseModel):
    path: str = "$"
    message: str
    code: str


class ExecutionUnsupportedStep(BaseModel):
    step: int
    action: str
    expected: Optional[str] = None
    test_data: Optional[str] = None
    reason_code: str
    suggested_next_action: str


class ExecutionCandidate(BaseModel):
    id: str
    source_test_case_id: str
    title: str
    status: Literal["executable", "manual", "unsupported", "invalid"]
    spec: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    unsupported_steps: List[ExecutionUnsupportedStep] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    traceability_ids: List[str] = Field(default_factory=list)


class ExecutionPreviewInput(BaseModel):
    test_cases: List[TestCase] = Field(default_factory=list)
    target_base_url: Optional[HttpUrl] = None
    target_environment: Optional[str] = Field(default=None, max_length=120)
    project_id: Optional[str] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


class ExecutionPreviewSummary(BaseModel):
    executable: int = 0
    manual: int = 0
    unsupported: int = 0
    invalid: int = 0


class ExecutionPreviewResponse(BaseModel):
    executable: List[ExecutionCandidate] = Field(default_factory=list)
    manual: List[ExecutionCandidate] = Field(default_factory=list)
    unsupported: List[ExecutionCandidate] = Field(default_factory=list)
    invalid: List[ExecutionCandidate] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    summary: ExecutionPreviewSummary = Field(default_factory=ExecutionPreviewSummary)


class ExecutionRunInput(ExecutionPreviewInput):
    selected_test_case_ids: List[str] = Field(default_factory=list)


class ExecutionRunItem(BaseModel):
    id: str
    source_test_case_id: str
    title: str
    status: Literal["passed", "failed", "invalid", "skipped"]
    spec_id: Optional[str] = None
    ir_path: Optional[str] = None
    generated_spec_path: Optional[str] = None
    artifacts_dir: Optional[str] = None
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    issues: List[ExecutionIssue] = Field(default_factory=list)


class ExecutionRunSummary(BaseModel):
    passed: int = 0
    failed: int = 0
    invalid: int = 0
    skipped: int = 0
    unsupported: int = 0
    manual: int = 0


class ExecutionRunResponse(BaseModel):
    status: Literal["passed", "failed", "disabled"]
    run_id: str
    artifacts_root: Optional[str] = None
    results: List[ExecutionRunItem] = Field(default_factory=list)
    preview: ExecutionPreviewResponse
    warnings: List[str] = Field(default_factory=list)
    summary: ExecutionRunSummary = Field(default_factory=ExecutionRunSummary)


__all__ = [
    "ExecutionIssue",
    "ExecutionUnsupportedStep",
    "ExecutionCandidate",
    "ExecutionPreviewInput",
    "ExecutionPreviewSummary",
    "ExecutionPreviewResponse",
    "ExecutionRunInput",
    "ExecutionRunItem",
    "ExecutionRunSummary",
    "ExecutionRunResponse",
]
