from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

from .requirements import Requirement, WorkflowSettings


class JiraConnectionInput(BaseModel):
    base_url: HttpUrl
    email: str = Field(min_length=3)
    api_token: str = Field(min_length=1, repr=False)


class JiraStoredConnection(BaseModel):
    base_url: HttpUrl
    email: str
    api_token: str = Field(repr=False)
    account_id: Optional[str] = None
    display_name: Optional[str] = None
    api_token_hint: Optional[str] = None
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None


class JiraConnectionSummary(BaseModel):
    base_url: HttpUrl
    email: str
    account_id: Optional[str] = None
    display_name: Optional[str] = None
    api_token_hint: Optional[str] = None
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None


class JiraConnectionStatusResponse(BaseModel):
    connected: bool = False
    connection: Optional[JiraConnectionSummary] = None


class JiraConnectionDeleteResponse(BaseModel):
    status: str = "deleted"


class JiraProjectSummary(BaseModel):
    project_id: str
    key: str
    name: str


class JiraProjectsResponse(BaseModel):
    projects: List[JiraProjectSummary] = Field(default_factory=list)


class JiraIssueTypeSummary(BaseModel):
    issue_type_id: str
    name: str
    description: Optional[str] = None
    hierarchy_level: Optional[int] = None
    subtask: bool = False
    scope_type: Optional[str] = None


class JiraProjectIssueTypesResponse(BaseModel):
    project_key: str
    issue_types: List[JiraIssueTypeSummary] = Field(default_factory=list)


class JiraIssueSummary(BaseModel):
    issue_id: str
    key: str
    summary: str
    issue_type: str
    status: Optional[str] = None
    parent_key: Optional[str] = None
    web_url: Optional[HttpUrl] = None
    updated_at: Optional[datetime] = None
    labels: List[str] = Field(default_factory=list)
    description_text: Optional[str] = None
    description_adf: Optional[Dict[str, Any]] = Field(default=None, exclude=True)


class JiraIssueSearchResponse(BaseModel):
    issues: List[JiraIssueSummary] = Field(default_factory=list)
    total: int = 0


class JiraImportInput(BaseModel):
    epic_key: Optional[str] = None
    issue_keys: List[str] = Field(default_factory=list)
    jql: Optional[str] = None
    include_children: bool = True
    workflow_settings: Optional[WorkflowSettings] = None

    @model_validator(mode="after")
    def validate_selector(self):
        has_selector = bool((self.epic_key or "").strip() or self.issue_keys or (self.jql or "").strip())
        if not has_selector:
            raise ValueError("Provide at least one of epic_key, issue_keys, or jql")
        return self


class JiraSyncPreviewInput(BaseModel):
    requirements: List[Requirement]
    managed_section_title: str = Field(default="Agentic Requirements", min_length=1)
    conflict_strategy: Literal["block", "allow"] = "block"


class JiraSyncIssuePreview(BaseModel):
    issue_key: str
    issue_type: Optional[str] = None
    issue_url: Optional[HttpUrl] = None
    status: Literal["ready", "conflict", "missing"] = "ready"
    requirement_ids: List[str] = Field(default_factory=list)
    target_field: Literal["description_managed_block"] = "description_managed_block"
    live_issue_updated_at: Optional[datetime] = None
    mapped_issue_updated_at: Optional[datetime] = None
    existing_description_excerpt: Optional[str] = None
    rendered_description_excerpt: Optional[str] = None
    conflict_reason: Optional[str] = None
    warning: Optional[str] = None


class JiraSyncPreviewResponse(BaseModel):
    issues: List[JiraSyncIssuePreview] = Field(default_factory=list)
    ready_issue_count: int = 0
    conflict_count: int = 0
    skipped_requirement_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class JiraSyncApplyInput(JiraSyncPreviewInput):
    allow_conflicts: bool = False


class JiraSyncIssueResult(BaseModel):
    issue_key: str
    status: Literal["updated", "skipped", "conflict", "failed"] = "updated"
    requirement_ids: List[str] = Field(default_factory=list)
    issue_url: Optional[HttpUrl] = None
    updated_at: Optional[datetime] = None
    message: Optional[str] = None


class JiraSyncApplyResponse(BaseModel):
    results: List[JiraSyncIssueResult] = Field(default_factory=list)
    updated_issue_count: int = 0
    skipped_issue_count: int = 0
    conflict_count: int = 0
    warnings: List[str] = Field(default_factory=list)
    requirements: List[Requirement] = Field(default_factory=list)


class AzureDevOpsConnectionInput(BaseModel):
    organization_url: HttpUrl
    personal_access_token: str = Field(min_length=1, repr=False)
    display_name: Optional[str] = None
    account_email: Optional[str] = None


class AzureDevOpsStoredConnection(BaseModel):
    organization_url: HttpUrl
    organization: str
    default_project: Optional[str] = None
    personal_access_token: str = Field(repr=False)
    auth_type: Literal["pat"] = "pat"
    display_name: Optional[str] = None
    account_email: Optional[str] = None
    token_hint: Optional[str] = None
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None


class AzureDevOpsConnectionSummary(BaseModel):
    organization_url: HttpUrl
    organization: str
    default_project: Optional[str] = None
    auth_type: Literal["pat"] = "pat"
    display_name: Optional[str] = None
    account_email: Optional[str] = None
    token_hint: Optional[str] = None
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None


class AzureDevOpsConnectionStatusResponse(BaseModel):
    connected: bool = False
    connection: Optional[AzureDevOpsConnectionSummary] = None


class AzureDevOpsConnectionDeleteResponse(BaseModel):
    status: str = "deleted"


class AzureDevOpsProjectSummary(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    state: Optional[str] = None
    visibility: Optional[str] = None
    url: Optional[HttpUrl] = None


class AzureDevOpsProjectsResponse(BaseModel):
    projects: List[AzureDevOpsProjectSummary] = Field(default_factory=list)


class AzureDevOpsWorkItemTypeSummary(BaseModel):
    name: str
    reference_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None


class AzureDevOpsProjectWorkItemTypesResponse(BaseModel):
    project: str
    work_item_types: List[AzureDevOpsWorkItemTypeSummary] = Field(default_factory=list)


class AzureDevOpsWorkItemSummary(BaseModel):
    work_item_id: int
    title: str
    work_item_type: str
    state: Optional[str] = None
    project: Optional[str] = None
    area_path: Optional[str] = None
    iteration_path: Optional[str] = None
    assigned_to: Optional[str] = None
    changed_at: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    parent_id: Optional[int] = None
    web_url: Optional[HttpUrl] = None
    description_text: Optional[str] = None
    acceptance_criteria_text: Optional[str] = None
    rev: Optional[int] = None
    fields: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    relations: List[Dict[str, Any]] = Field(default_factory=list, exclude=True)


class AzureDevOpsWorkItemSearchResponse(BaseModel):
    work_items: List[AzureDevOpsWorkItemSummary] = Field(default_factory=list)
    total: int = 0


class AzureDevOpsImportInput(BaseModel):
    project: Optional[str] = None
    work_item_id: Optional[int] = None
    work_item_ids: List[int] = Field(default_factory=list)
    wiql: Optional[str] = None
    include_children: bool = True
    workflow_settings: Optional[WorkflowSettings] = None

    @model_validator(mode="after")
    def validate_selector(self):
        has_selector = bool(self.work_item_id or self.work_item_ids or (self.wiql or "").strip())
        if not has_selector:
            raise ValueError("Provide work_item_id, work_item_ids, or wiql")
        return self


class AzureDevOpsSyncPreviewInput(BaseModel):
    requirements: List[Requirement]
    managed_section_title: str = Field(default="Agentic Requirements", min_length=1)
    conflict_strategy: Literal["block", "allow"] = "block"


class AzureDevOpsSyncWorkItemPreview(BaseModel):
    work_item_id: int
    work_item_type: Optional[str] = None
    work_item_url: Optional[HttpUrl] = None
    project: Optional[str] = None
    status: Literal["ready", "conflict", "missing"] = "ready"
    requirement_ids: List[str] = Field(default_factory=list)
    target_field: Literal["system_description_managed_block"] = "system_description_managed_block"
    live_changed_at: Optional[datetime] = None
    mapped_changed_at: Optional[datetime] = None
    existing_description_excerpt: Optional[str] = None
    rendered_description_excerpt: Optional[str] = None
    conflict_reason: Optional[str] = None
    warning: Optional[str] = None


class AzureDevOpsSyncPreviewResponse(BaseModel):
    work_items: List[AzureDevOpsSyncWorkItemPreview] = Field(default_factory=list)
    ready_work_item_count: int = 0
    conflict_count: int = 0
    skipped_requirement_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class AzureDevOpsSyncApplyInput(AzureDevOpsSyncPreviewInput):
    allow_conflicts: bool = False


class AzureDevOpsSyncWorkItemResult(BaseModel):
    work_item_id: int
    status: Literal["updated", "skipped", "conflict", "failed"] = "updated"
    requirement_ids: List[str] = Field(default_factory=list)
    work_item_url: Optional[HttpUrl] = None
    updated_at: Optional[datetime] = None
    message: Optional[str] = None


class AzureDevOpsSyncApplyResponse(BaseModel):
    results: List[AzureDevOpsSyncWorkItemResult] = Field(default_factory=list)
    updated_work_item_count: int = 0
    skipped_work_item_count: int = 0
    conflict_count: int = 0
    warnings: List[str] = Field(default_factory=list)
    requirements: List[Requirement] = Field(default_factory=list)


__all__ = [
    "JiraConnectionInput",
    "JiraStoredConnection",
    "JiraConnectionSummary",
    "JiraConnectionStatusResponse",
    "JiraConnectionDeleteResponse",
    "JiraProjectSummary",
    "JiraProjectsResponse",
    "JiraIssueTypeSummary",
    "JiraProjectIssueTypesResponse",
    "JiraIssueSummary",
    "JiraIssueSearchResponse",
    "JiraImportInput",
    "JiraSyncPreviewInput",
    "JiraSyncIssuePreview",
    "JiraSyncPreviewResponse",
    "JiraSyncApplyInput",
    "JiraSyncIssueResult",
    "JiraSyncApplyResponse",
    "AzureDevOpsConnectionInput",
    "AzureDevOpsStoredConnection",
    "AzureDevOpsConnectionSummary",
    "AzureDevOpsConnectionStatusResponse",
    "AzureDevOpsConnectionDeleteResponse",
    "AzureDevOpsProjectSummary",
    "AzureDevOpsProjectsResponse",
    "AzureDevOpsWorkItemTypeSummary",
    "AzureDevOpsProjectWorkItemTypesResponse",
    "AzureDevOpsWorkItemSummary",
    "AzureDevOpsWorkItemSearchResponse",
    "AzureDevOpsImportInput",
    "AzureDevOpsSyncPreviewInput",
    "AzureDevOpsSyncWorkItemPreview",
    "AzureDevOpsSyncPreviewResponse",
    "AzureDevOpsSyncApplyInput",
    "AzureDevOpsSyncWorkItemResult",
    "AzureDevOpsSyncApplyResponse",
]
