from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..models import (
    ImpactAnalysisResult,
    ImpactAnalysisSummary,
    ImpactChangedItem,
    ImpactImpactedTestCase,
    ImpactRecommendation,
    QaProjectStageSnapshot,
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "shall",
    "should",
    "system",
    "the",
    "to",
    "user",
    "with",
}


@dataclass
class _RequirementView:
    id: str
    text: str
    approved: bool
    aliases: set[str] = field(default_factory=set)


@dataclass
class _ScenarioView:
    id: str
    requirement_id: str
    title: str
    objective: str
    text: str


def _snapshot_payload(snapshot: Optional[QaProjectStageSnapshot]) -> dict[str, Any]:
    return dict(snapshot.payload or {}) if snapshot is not None else {}


def _snapshot_id(snapshot: Optional[QaProjectStageSnapshot]) -> Optional[str]:
    return snapshot.snapshot_id if snapshot is not None else None


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _tokens(value: Any) -> set[str]:
    return {token for token in _WORD_RE.findall(str(value or "").lower()) if token not in _STOPWORDS and len(token) > 2}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _requirement_aliases(requirement: dict[str, Any], index: int) -> set[str]:
    aliases = {
        str(requirement.get("id") or "").strip(),
        str(requirement.get("source_issue_key") or "").strip(),
        str(requirement.get("sync_target_issue_key") or "").strip(),
        str(requirement.get("artifact_item_id") or "").strip(),
    }
    return {alias for alias in aliases if alias}


def _requirements_from_payload(payload: dict[str, Any], *, fallback_coverage_plan: Optional[list[dict[str, Any]]] = None) -> list[_RequirementView]:
    raw_requirements = payload.get("requirements")
    if isinstance(raw_requirements, list) and raw_requirements:
        views: list[_RequirementView] = []
        for index, item in enumerate(raw_requirements):
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("id") or f"REQ-{index + 1:03d}")
            aliases = _requirement_aliases(item, index)
            views.append(
                _RequirementView(
                    id=req_id,
                    text=str(item.get("text") or ""),
                    approved=str(item.get("review_status") or "").lower() == "approved" or bool(item.get("approved")),
                    aliases=aliases | {req_id},
                )
            )
        return views

    views = []
    seen: set[str] = set()
    for index, item in enumerate(fallback_coverage_plan or []):
        if not isinstance(item, dict):
            continue
        req_id = str(item.get("requirement_id") or f"REQ-{index + 1:03d}")
        if req_id in seen:
            continue
        seen.add(req_id)
        views.append(
            _RequirementView(
                id=req_id,
                text=str(item.get("requirement_text") or ""),
                approved=True,
                aliases={req_id, f"REQ-{index + 1:03d}"},
            )
        )
    return views


def _coverage_plan_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    coverage_plan = payload.get("coverage_plan")
    return [item for item in coverage_plan if isinstance(item, dict)] if isinstance(coverage_plan, list) else []


def _scenario_text(scenario: dict[str, Any]) -> str:
    return " ".join(
        str(scenario.get(key) or "")
        for key in (
            "scenario_type",
            "title",
            "objective",
            "priority",
        )
    ).strip()


def _scenarios_from_coverage(coverage_plan: list[dict[str, Any]]) -> list[_ScenarioView]:
    scenarios: list[_ScenarioView] = []
    for plan_index, plan in enumerate(coverage_plan):
        requirement_id = str(plan.get("requirement_id") or f"REQ-{plan_index + 1:03d}")
        raw_scenarios = plan.get("scenarios")
        if not isinstance(raw_scenarios, list):
            continue
        for scenario_index, scenario in enumerate(raw_scenarios):
            if not isinstance(scenario, dict):
                continue
            scenario_id = str(scenario.get("id") or f"{requirement_id}-SCN-{scenario_index + 1:02d}")
            scenarios.append(
                _ScenarioView(
                    id=scenario_id,
                    requirement_id=requirement_id,
                    title=str(scenario.get("title") or scenario_id),
                    objective=str(scenario.get("objective") or ""),
                    text=_scenario_text(scenario),
                )
            )
    return scenarios


def _test_cases_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    test_cases = payload.get("test_cases")
    return [item for item in test_cases if isinstance(item, dict)] if isinstance(test_cases, list) else []


def _test_case_text(test_case: dict[str, Any]) -> str:
    step_text = " ".join(f"{step.get('action', '')} {step.get('expected', '')}" for step in test_case.get("steps", []) if isinstance(step, dict))
    return " ".join(
        [
            str(test_case.get("id") or ""),
            str(test_case.get("title") or ""),
            str(test_case.get("description") or ""),
            str(test_case.get("expected_result") or ""),
            step_text,
            " ".join(_string_list(test_case.get("tags"))),
        ]
    )


def _match_requirements(
    *,
    current: list[_RequirementView],
    baseline: list[_RequirementView],
) -> tuple[list[tuple[_RequirementView, Optional[_RequirementView]]], list[_RequirementView]]:
    matched_baseline_ids: set[str] = set()
    matches: list[tuple[_RequirementView, Optional[_RequirementView]]] = []
    for current_requirement in current:
        match = next(
            (
                baseline_requirement
                for baseline_requirement in baseline
                if baseline_requirement.id not in matched_baseline_ids and current_requirement.aliases.intersection(baseline_requirement.aliases)
            ),
            None,
        )
        if match is not None:
            matched_baseline_ids.add(match.id)
        matches.append((current_requirement, match))
    removed = [baseline_requirement for baseline_requirement in baseline if baseline_requirement.id not in matched_baseline_ids]
    return matches, removed


def _change_label(change_type: str) -> str:
    return change_type.replace("_", " ").title()


def _recommendation_id(action: str, item_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", item_id).strip("-") or _content_hash(item_id)[:12]
    return f"impact-{action}-{normalized}"


def _changed_requirement_aliases(
    changed_items: Iterable[ImpactChangedItem],
    requirement_aliases: dict[str, set[str]],
) -> set[str]:
    aliases: set[str] = set()
    for item in changed_items:
        if item.kind != "requirement":
            continue
        aliases.add(item.item_id)
        aliases.update(requirement_aliases.get(item.item_id, set()))
        if item.requirement_id:
            aliases.add(item.requirement_id)
            aliases.update(requirement_aliases.get(item.requirement_id, set()))
    return {alias for alias in aliases if alias}


def analyze_impact(
    *,
    current_requirements_snapshot: Optional[QaProjectStageSnapshot],
    current_use_cases_snapshot: Optional[QaProjectStageSnapshot],
    current_context_snapshot: Optional[QaProjectStageSnapshot],
    baseline_requirements_snapshot: Optional[QaProjectStageSnapshot],
    baseline_use_cases_snapshot: Optional[QaProjectStageSnapshot],
    baseline_context_snapshot: Optional[QaProjectStageSnapshot],
    test_cases_snapshot: Optional[QaProjectStageSnapshot],
) -> ImpactAnalysisResult:
    current_requirements_payload = _snapshot_payload(current_requirements_snapshot)
    current_use_cases_payload = _snapshot_payload(current_use_cases_snapshot)
    baseline_requirements_payload = _snapshot_payload(baseline_requirements_snapshot)
    baseline_use_cases_payload = _snapshot_payload(baseline_use_cases_snapshot)
    test_case_payload = _snapshot_payload(test_cases_snapshot)

    baseline_coverage = _coverage_plan_from_payload(baseline_use_cases_payload) or _coverage_plan_from_payload(test_case_payload)
    current_coverage = _coverage_plan_from_payload(current_use_cases_payload)
    current_requirements = _requirements_from_payload(current_requirements_payload)
    baseline_requirements = _requirements_from_payload(baseline_requirements_payload, fallback_coverage_plan=baseline_coverage)
    current_scenarios = _scenarios_from_coverage(current_coverage)
    baseline_scenarios = _scenarios_from_coverage(baseline_coverage)
    baseline_test_cases = _test_cases_from_payload(test_case_payload)

    requirement_aliases: dict[str, set[str]] = {}
    for requirement in [*current_requirements, *baseline_requirements]:
        requirement_aliases[requirement.id] = set(requirement.aliases) | {requirement.id}
        for alias in requirement.aliases:
            requirement_aliases[alias] = set(requirement.aliases) | {requirement.id}

    changed_items: list[ImpactChangedItem] = []
    unchanged_requirement_count = 0
    requirement_matches, removed_requirements = _match_requirements(current=current_requirements, baseline=baseline_requirements)
    for current_requirement, baseline_requirement in requirement_matches:
        if baseline_requirement is None:
            changed_items.append(
                ImpactChangedItem(
                    item_id=current_requirement.id,
                    kind="requirement",
                    change_type="added",
                    title=f"{current_requirement.id} added",
                    current_text=current_requirement.text,
                    approved=current_requirement.approved,
                    requirement_id=current_requirement.id,
                )
            )
            continue
        if _normalized_text(current_requirement.text) != _normalized_text(baseline_requirement.text):
            changed_items.append(
                ImpactChangedItem(
                    item_id=current_requirement.id,
                    kind="requirement",
                    change_type="modified",
                    title=f"{current_requirement.id} modified",
                    current_text=current_requirement.text,
                    previous_text=baseline_requirement.text,
                    approved=current_requirement.approved,
                    requirement_id=current_requirement.id,
                )
            )
        else:
            unchanged_requirement_count += 1

    for baseline_requirement in removed_requirements:
        changed_items.append(
            ImpactChangedItem(
                item_id=baseline_requirement.id,
                kind="requirement",
                change_type="removed",
                title=f"{baseline_requirement.id} removed",
                previous_text=baseline_requirement.text,
                approved=True,
                requirement_id=baseline_requirement.id,
            )
        )

    current_scenarios_by_id = {scenario.id: scenario for scenario in current_scenarios}
    baseline_scenarios_by_id = {scenario.id: scenario for scenario in baseline_scenarios}
    current_requirement_approval = {requirement.id: requirement.approved for requirement in current_requirements}
    for scenario_id, current_scenario in current_scenarios_by_id.items():
        baseline_scenario = baseline_scenarios_by_id.get(scenario_id)
        if baseline_scenario is None:
            changed_items.append(
                ImpactChangedItem(
                    item_id=scenario_id,
                    kind="use_case",
                    change_type="added",
                    title=f"{current_scenario.title} added",
                    current_text=current_scenario.text,
                    approved=current_requirement_approval.get(current_scenario.requirement_id, False),
                    requirement_id=current_scenario.requirement_id,
                    scenario_ids=[scenario_id],
                )
            )
            continue
        if _normalized_text(current_scenario.text) != _normalized_text(baseline_scenario.text):
            changed_items.append(
                ImpactChangedItem(
                    item_id=scenario_id,
                    kind="use_case",
                    change_type="modified",
                    title=f"{current_scenario.title} modified",
                    current_text=current_scenario.text,
                    previous_text=baseline_scenario.text,
                    approved=current_requirement_approval.get(current_scenario.requirement_id, False),
                    requirement_id=current_scenario.requirement_id,
                    scenario_ids=[scenario_id],
                )
            )
    for scenario_id, baseline_scenario in baseline_scenarios_by_id.items():
        if scenario_id in current_scenarios_by_id:
            continue
        changed_items.append(
            ImpactChangedItem(
                item_id=scenario_id,
                kind="use_case",
                change_type="removed",
                title=f"{baseline_scenario.title} removed",
                previous_text=baseline_scenario.text,
                approved=True,
                requirement_id=baseline_scenario.requirement_id,
                scenario_ids=[scenario_id],
            )
        )

    changed_requirement_aliases = _changed_requirement_aliases(changed_items, requirement_aliases)
    changed_scenario_ids = {scenario_id for item in changed_items for scenario_id in item.scenario_ids}
    removed_requirement_ids = {item.requirement_id or item.item_id for item in changed_items if item.kind == "requirement" and item.change_type == "removed"}
    changed_text_tokens = _tokens(" ".join(filter(None, [item.current_text or item.previous_text for item in changed_items])))

    impacted_test_cases: list[ImpactImpactedTestCase] = []
    direct_test_case_ids: set[str] = set()
    semantic_test_case_ids: set[str] = set()
    for test_case in baseline_test_cases:
        case_id = str(test_case.get("id") or "")
        if not case_id:
            continue
        linked_ids = _string_list(test_case.get("linked_requirement_ids"))
        scenario_refs = _string_list(test_case.get("scenario_refs"))
        linked_matches = set(linked_ids).intersection(changed_requirement_aliases)
        scenario_matches = set(scenario_refs).intersection(changed_scenario_ids)
        if linked_matches or scenario_matches:
            direct_test_case_ids.add(case_id)
            reasons = []
            if linked_matches:
                reasons.append(f"linked requirements: {', '.join(sorted(linked_matches))}")
            if scenario_matches:
                reasons.append(f"scenario refs: {', '.join(sorted(scenario_matches))}")
            impacted_test_cases.append(
                ImpactImpactedTestCase(
                    test_case_id=case_id,
                    title=str(test_case.get("title") or case_id),
                    impact_source="direct",
                    linked_requirement_ids=linked_ids,
                    scenario_refs=scenario_refs,
                    reason="Direct traceability match via " + "; ".join(reasons),
                )
            )
            continue

        case_tokens = _tokens(_test_case_text(test_case))
        overlap = changed_text_tokens.intersection(case_tokens)
        semantic_score = len(overlap) / max(1, min(len(changed_text_tokens), len(case_tokens)))
        if len(overlap) >= 2 and semantic_score >= 0.28:
            semantic_test_case_ids.add(case_id)
            impacted_test_cases.append(
                ImpactImpactedTestCase(
                    test_case_id=case_id,
                    title=str(test_case.get("title") or case_id),
                    impact_source="semantic_neighbor",
                    linked_requirement_ids=linked_ids,
                    scenario_refs=scenario_refs,
                    reason=f"Nearby impact candidate from shared terms: {', '.join(sorted(overlap)[:5])}",
                )
            )

    recommendations: list[ImpactRecommendation] = []
    impacted_by_id = {item.test_case_id: item for item in impacted_test_cases}
    changed_requirement_ids = {item.requirement_id or item.item_id for item in changed_items if item.kind == "requirement"}
    changed_scenarios_by_requirement: dict[str, list[str]] = {}
    for item in changed_items:
        if item.requirement_id:
            changed_scenarios_by_requirement.setdefault(item.requirement_id, []).extend(item.scenario_ids)

    for test_case in baseline_test_cases:
        case_id = str(test_case.get("id") or "")
        if not case_id:
            continue
        linked_ids = _string_list(test_case.get("linked_requirement_ids"))
        linked_aliases = {alias for linked_id in linked_ids for alias in requirement_aliases.get(linked_id, {linked_id})}
        removed_match = bool(linked_aliases.intersection(removed_requirement_ids) or set(linked_ids).intersection(removed_requirement_ids))
        has_remaining_links = bool(set(linked_ids) - removed_requirement_ids)
        if removed_match and not has_remaining_links:
            recommendations.append(
                ImpactRecommendation(
                    recommendation_id=_recommendation_id("deprecate", case_id),
                    action="deprecate",
                    title=f"Deprecate {case_id}",
                    reason="The linked requirement or use case was removed.",
                    confidence=0.88,
                    accepted=True,
                    impact_source="direct",
                    test_case_id=case_id,
                )
            )
        elif case_id in direct_test_case_ids:
            impact = impacted_by_id[case_id]
            recommendations.append(
                ImpactRecommendation(
                    recommendation_id=_recommendation_id("update", case_id),
                    action="update",
                    title=f"Update {case_id}",
                    reason=impact.reason,
                    confidence=0.82,
                    accepted=True,
                    impact_source="direct",
                    test_case_id=case_id,
                    scenario_refs=impact.scenario_refs,
                )
            )
        elif case_id in semantic_test_case_ids:
            impact = impacted_by_id[case_id]
            recommendations.append(
                ImpactRecommendation(
                    recommendation_id=_recommendation_id("neighbor", case_id),
                    action="update",
                    title=f"Review nearby impact on {case_id}",
                    reason=impact.reason,
                    confidence=0.46,
                    accepted=False,
                    impact_source="semantic_neighbor",
                    test_case_id=case_id,
                    scenario_refs=impact.scenario_refs,
                )
            )
        else:
            recommendations.append(
                ImpactRecommendation(
                    recommendation_id=_recommendation_id("keep", case_id),
                    action="keep",
                    title=f"Keep {case_id}",
                    reason="No direct or semantic impact detected.",
                    confidence=0.91,
                    accepted=True,
                    impact_source="direct",
                    test_case_id=case_id,
                )
            )

    directly_covered_requirements = {
        alias
        for test_case in baseline_test_cases
        if str(test_case.get("id") or "") in direct_test_case_ids
        for linked_id in _string_list(test_case.get("linked_requirement_ids"))
        for alias in requirement_aliases.get(linked_id, {linked_id})
    }
    for item in changed_items:
        if item.change_type == "removed":
            continue
        requirement_id = item.requirement_id or item.item_id
        item_aliases = requirement_aliases.get(requirement_id, {requirement_id}) | {requirement_id}
        if item_aliases.intersection(directly_covered_requirements):
            continue
        recommendations.append(
            ImpactRecommendation(
                recommendation_id=_recommendation_id("add", item.item_id),
                action="add",
                title=f"Add coverage for {item.title}",
                reason=f"{_change_label(item.change_type)} {item.kind.replace('_', ' ')} has no direct existing test coverage.",
                confidence=0.79,
                accepted=True,
                impact_source="direct",
                requirement_id=requirement_id,
                use_case_id=item.item_id if item.kind == "use_case" else None,
                scenario_refs=item.scenario_ids,
            )
        )

    recommendation_counts = Counter(recommendation.action for recommendation in recommendations)
    change_counts = Counter(item.change_type for item in changed_items)
    summary = ImpactAnalysisSummary(
        changed_item_count=len(changed_items),
        added_count=change_counts["added"],
        modified_count=change_counts["modified"],
        removed_count=change_counts["removed"],
        unchanged_requirement_count=unchanged_requirement_count,
        directly_impacted_test_case_count=len(direct_test_case_ids),
        semantic_neighbor_count=len(semantic_test_case_ids),
        recommendation_counts=dict(recommendation_counts),
    )
    return ImpactAnalysisResult(
        baseline_snapshot_ids={
            "requirements": _snapshot_id(baseline_requirements_snapshot),
            "context": _snapshot_id(baseline_context_snapshot),
            "use_cases": _snapshot_id(baseline_use_cases_snapshot),
            "test_cases": _snapshot_id(test_cases_snapshot),
        },
        current_snapshot_ids={
            "requirements": _snapshot_id(current_requirements_snapshot),
            "context": _snapshot_id(current_context_snapshot),
            "use_cases": _snapshot_id(current_use_cases_snapshot),
            "test_cases": _snapshot_id(test_cases_snapshot),
        },
        changed_items=changed_items,
        impacted_test_cases=impacted_test_cases,
        recommendations=recommendations,
        summary=summary,
    )
