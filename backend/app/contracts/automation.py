from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator

from .test_cases import TestCase


class AutomationLocatorEvidence(BaseModel):
    method: Literal["locator", "get_by_role", "get_by_label", "get_by_text", "get_by_test_id", "get_by_placeholder", "get_by_alt_text", "get_by_title"]
    value: str = Field(min_length=1, max_length=2000)
    name: Optional[str] = Field(default=None, max_length=2000)


class AutomationAssertionEvidence(BaseModel):
    step: int = Field(ge=0)
    method: Literal[
        "to_be_visible",
        "to_be_hidden",
        "to_be_enabled",
        "to_be_disabled",
        "to_be_checked",
        "to_have_text",
        "to_contain_text",
        "to_have_value",
        "to_have_count",
        "to_have_url",
        "to_have_title",
    ]
    locator: Optional[AutomationLocatorEvidence] = None
    expected: str | int | None = None

    @model_validator(mode="after")
    def assertion_value_type(self):
        if self.method == "to_have_count":
            if not isinstance(self.expected, int) or self.expected < 0:
                raise ValueError("to_have_count requires a nonnegative integer.")
        elif self.method in {"to_have_text", "to_contain_text", "to_have_value", "to_have_url", "to_have_title"}:
            if not isinstance(self.expected, str):
                raise ValueError("This assertion requires a string expected value.")
        elif self.expected is not None:
            raise ValueError("State assertions do not take an expected value.")
        if self.method in {"to_have_url", "to_have_title"} and self.locator is not None:
            raise ValueError("URL/title assertions apply to the page, not an element locator.")
        return self


class AutomationCaseEvidence(BaseModel):
    test_case_id: str
    source_reference: str = Field(min_length=1, max_length=2000)
    locators: List[AutomationLocatorEvidence] = Field(default_factory=list)
    assertions: List[AutomationAssertionEvidence] = Field(min_length=1)
    allowed_urls: List[HttpUrl] = Field(default_factory=list)
    input_values: List[str] = Field(default_factory=list)


class AutomationInput(BaseModel):
    project_id: Optional[str] = None
    test_cases: List[TestCase]
    target_base_url: Optional[HttpUrl] = None
    interface_evidence: List[AutomationCaseEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_evidence(self):
        case_ids = [case.id for case in self.test_cases]
        evidence_ids = [item.test_case_id for item in self.interface_evidence]
        if len(set(case_ids)) != len(case_ids) or len(set(evidence_ids)) != len(evidence_ids) or set(evidence_ids) - set(case_ids):
            raise ValueError("Case IDs and per-case evidence must be unique and refer to supplied cases.")
        return self


class AutomationCaseDiagnostic(BaseModel):
    test_case_id: str
    title: Optional[str] = None
    status: Literal["generated", "fallback", "manual", "unsupported"] = "generated"
    reason: str
    shard_id: Optional[str] = None
    source_expected_results: List[str] = Field(default_factory=list)


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
    "AutomationLocatorEvidence",
    "AutomationAssertionEvidence",
    "AutomationCaseEvidence",
    "AutomationCaseDiagnostic",
    "AutomationResponse",
]
