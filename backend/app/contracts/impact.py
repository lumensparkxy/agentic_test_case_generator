from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from .test_cases import TestCase


ImpactItemKind = Literal["requirement", "use_case"]


ImpactChangeType = Literal["added", "modified", "removed"]


ImpactSource = Literal["direct", "semantic_neighbor"]


ImpactRecommendationAction = Literal["keep", "update", "add", "deprecate"]


class ImpactChangedItem(BaseModel):
    item_id: str
    kind: ImpactItemKind
    change_type: ImpactChangeType
    title: str
    current_text: Optional[str] = None
    previous_text: Optional[str] = None
    approved: bool = False
    requirement_id: Optional[str] = None
    scenario_ids: List[str] = Field(default_factory=list)


class ImpactImpactedTestCase(BaseModel):
    test_case_id: str
    title: str
    impact_source: ImpactSource = "direct"
    linked_requirement_ids: List[str] = Field(default_factory=list)
    scenario_refs: List[str] = Field(default_factory=list)
    reason: str


class ImpactRecommendation(BaseModel):
    recommendation_id: str
    action: ImpactRecommendationAction
    title: str
    reason: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    accepted: bool = False
    impact_source: ImpactSource = "direct"
    requirement_id: Optional[str] = None
    use_case_id: Optional[str] = None
    test_case_id: Optional[str] = None
    scenario_refs: List[str] = Field(default_factory=list)


class ImpactAnalysisSummary(BaseModel):
    changed_item_count: int = 0
    added_count: int = 0
    modified_count: int = 0
    removed_count: int = 0
    unchanged_requirement_count: int = 0
    directly_impacted_test_case_count: int = 0
    semantic_neighbor_count: int = 0
    recommendation_counts: Dict[str, int] = Field(default_factory=dict)


class ImpactAnalysisResult(BaseModel):
    baseline_snapshot_ids: Dict[str, Optional[str]] = Field(default_factory=dict)
    current_snapshot_ids: Dict[str, Optional[str]] = Field(default_factory=dict)
    changed_items: List[ImpactChangedItem] = Field(default_factory=list)
    impacted_test_cases: List[ImpactImpactedTestCase] = Field(default_factory=list)
    recommendations: List[ImpactRecommendation] = Field(default_factory=list)
    summary: ImpactAnalysisSummary = Field(default_factory=ImpactAnalysisSummary)


class ImpactAnalysisInput(BaseModel):
    base_project_revision: Optional[int] = Field(default=None, ge=0)


class ImpactUpdateApplyInput(BaseModel):
    accepted_recommendation_ids: Optional[List[str]] = None
    base_project_revision: Optional[int] = Field(default=None, ge=0)


class ImpactUpdateApplyResult(BaseModel):
    test_cases: List[TestCase] = Field(default_factory=list)
    applied_recommendation_ids: List[str] = Field(default_factory=list)
    preserved_count: int = 0
    updated_count: int = 0
    added_count: int = 0
    deprecated_count: int = 0


__all__ = [
    "ImpactItemKind",
    "ImpactChangeType",
    "ImpactSource",
    "ImpactRecommendationAction",
    "ImpactChangedItem",
    "ImpactImpactedTestCase",
    "ImpactRecommendation",
    "ImpactAnalysisSummary",
    "ImpactAnalysisResult",
    "ImpactAnalysisInput",
    "ImpactUpdateApplyInput",
    "ImpactUpdateApplyResult",
]
