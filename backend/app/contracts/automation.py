from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl

from .test_cases import TestCase


class AutomationInput(BaseModel):
    project_id: Optional[str] = None
    test_cases: List[TestCase]
    target_base_url: Optional[HttpUrl] = None


class AutomationCaseDiagnostic(BaseModel):
    test_case_id: str
    title: Optional[str] = None
    status: Literal["generated", "fallback", "manual", "unsupported"] = "generated"
    reason: str
    shard_id: Optional[str] = None


class AutomationResponse(BaseModel):
    guidance: Optional[Dict[str, Any]] = None
    knowledge_suggestion: Optional[Dict[str, Any]] = None
    status: str
    files: List[str]
    notes: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    case_diagnostics: List[AutomationCaseDiagnostic] = Field(default_factory=list)


__all__ = [
    "AutomationInput",
    "AutomationCaseDiagnostic",
    "AutomationResponse",
]
