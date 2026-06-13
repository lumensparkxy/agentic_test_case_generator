from datetime import datetime
from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field


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


__all__ = [
    "UsageReportUserSummary",
    "UsageReportGroup",
    "UsageReportResponse",
]
