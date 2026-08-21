from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Iterable, Optional, Sequence

from ..adapters.jira import JiraAdapterError
from ..models import (
    AuthUser,
    JiraSyncApplyInput,
    JiraSyncApplyResponse,
    JiraSyncIssuePreview,
    JiraSyncIssueResult,
    JiraSyncPreviewInput,
    JiraSyncPreviewResponse,
    Requirement,
)
from .firestore_repository import get_optional_firestore_collection
from .jira_connection_service import get_jira_adapter_for_user

JIRA_REQUIREMENT_MAPPINGS_COLLECTION = "jira_requirement_mappings"
MANAGED_BLOCK_START = "[AGENTIC_REQUIREMENTS_START]"
MANAGED_BLOCK_END = "[AGENTIC_REQUIREMENTS_END]"
MANAGED_TARGET_FIELD = "description_managed_block"


@dataclass
class _SyncRequirementContext:
    requirement: Requirement
    mapping_payload: dict[str, Any]


@dataclass
class _SyncIssuePlan:
    issue_key: str
    issue_type: Optional[str]
    issue_url: Optional[str]
    requirements: list[Requirement]
    status: str
    live_issue_updated_at: Optional[datetime]
    mapped_issue_updated_at: Optional[datetime]
    existing_description_excerpt: Optional[str]
    rendered_description_excerpt: Optional[str]
    rendered_description_adf: Optional[dict[str, Any]]
    conflict_reason: Optional[str] = None
    warning: Optional[str] = None


def preview_jira_requirement_sync(*, current_user: AuthUser, payload: JiraSyncPreviewInput) -> JiraSyncPreviewResponse:
    adapter = get_jira_adapter_for_user(current_user=current_user)
    plans, skipped_requirement_ids, warnings = _build_sync_plans(
        adapter=adapter,
        requirements=payload.requirements,
        managed_section_title=payload.managed_section_title,
        conflict_strategy=payload.conflict_strategy,
    )

    previews = [
        JiraSyncIssuePreview(
            issue_key=plan.issue_key,
            issue_type=plan.issue_type,
            issue_url=plan.issue_url,
            status=plan.status,
            requirement_ids=[requirement.id for requirement in plan.requirements],
            target_field=MANAGED_TARGET_FIELD,
            live_issue_updated_at=plan.live_issue_updated_at,
            mapped_issue_updated_at=plan.mapped_issue_updated_at,
            existing_description_excerpt=plan.existing_description_excerpt,
            rendered_description_excerpt=plan.rendered_description_excerpt,
            conflict_reason=plan.conflict_reason,
            warning=plan.warning,
        )
        for plan in plans
    ]
    return JiraSyncPreviewResponse(
        issues=previews,
        ready_issue_count=sum(1 for preview in previews if preview.status == "ready"),
        conflict_count=sum(1 for preview in previews if preview.status == "conflict"),
        skipped_requirement_ids=skipped_requirement_ids,
        warnings=warnings,
    )


def apply_jira_requirement_sync(*, current_user: AuthUser, payload: JiraSyncApplyInput) -> JiraSyncApplyResponse:
    adapter = get_jira_adapter_for_user(current_user=current_user)
    plans, skipped_requirement_ids, warnings = _build_sync_plans(
        adapter=adapter,
        requirements=payload.requirements,
        managed_section_title=payload.managed_section_title,
        conflict_strategy="allow" if payload.allow_conflicts else payload.conflict_strategy,
    )

    results: list[JiraSyncIssueResult] = []
    updated_requirements_by_identity: dict[tuple[str, str, str], Requirement] = {}

    for plan in plans:
        requirement_ids = [requirement.id for requirement in plan.requirements]
        if plan.status == "missing":
            results.append(
                JiraSyncIssueResult(
                    issue_key=plan.issue_key,
                    status="failed",
                    requirement_ids=requirement_ids,
                    issue_url=plan.issue_url,
                    message=plan.conflict_reason or plan.warning or "Issue could not be loaded from JIRA",
                )
            )
            continue

        if plan.status == "conflict" and not payload.allow_conflicts:
            results.append(
                JiraSyncIssueResult(
                    issue_key=plan.issue_key,
                    status="conflict",
                    requirement_ids=requirement_ids,
                    issue_url=plan.issue_url,
                    updated_at=plan.live_issue_updated_at,
                    message=plan.conflict_reason or "The JIRA issue changed since it was imported.",
                )
            )
            continue

        if not plan.rendered_description_adf:
            results.append(
                JiraSyncIssueResult(
                    issue_key=plan.issue_key,
                    status="skipped",
                    requirement_ids=requirement_ids,
                    issue_url=plan.issue_url,
                    message=plan.warning or "No rendered JIRA update was produced for this issue.",
                )
            )
            continue

        try:
            adapter.update_issue_description(plan.issue_key, plan.rendered_description_adf)
            refreshed_issue = adapter.get_issue(plan.issue_key)
        except JiraAdapterError as exc:
            results.append(
                JiraSyncIssueResult(
                    issue_key=plan.issue_key,
                    status="failed",
                    requirement_ids=requirement_ids,
                    issue_url=plan.issue_url,
                    message=str(exc),
                )
            )
            continue

        for requirement in plan.requirements:
            updated = requirement.model_copy(
                update={
                    "source_issue_key": requirement.source_issue_key or plan.issue_key,
                    "source_issue_type": refreshed_issue.issue_type,
                    "source_issue_url": refreshed_issue.web_url,
                    "source_issue_updated_at": refreshed_issue.updated_at,
                    "sync_target_issue_key": requirement.sync_target_issue_key or plan.issue_key,
                }
            )
            updated_requirements_by_identity[_requirement_identity(requirement)] = updated

        results.append(
            JiraSyncIssueResult(
                issue_key=plan.issue_key,
                status="updated",
                requirement_ids=requirement_ids,
                issue_url=refreshed_issue.web_url,
                updated_at=refreshed_issue.updated_at,
                message=f"Updated managed requirements block in {plan.issue_key}.",
            )
        )

    final_requirements = [updated_requirements_by_identity.get(_requirement_identity(requirement), requirement) for requirement in payload.requirements]

    if skipped_requirement_ids:
        warnings.append(f"Skipped {len(skipped_requirement_ids)} requirement{'s' if len(skipped_requirement_ids) != 1 else ''} without a JIRA sync target.")

    return JiraSyncApplyResponse(
        results=results,
        updated_issue_count=sum(1 for result in results if result.status == "updated"),
        skipped_issue_count=sum(1 for result in results if result.status == "skipped"),
        conflict_count=sum(1 for result in results if result.status == "conflict"),
        warnings=_dedupe_strings(warnings),
        requirements=final_requirements,
    )


def _build_sync_plans(
    *,
    adapter,
    requirements: Sequence[Requirement],
    managed_section_title: str,
    conflict_strategy: str,
) -> tuple[list[_SyncIssuePlan], list[str], list[str]]:
    mapping_payloads = _load_mapping_payloads(requirements)
    grouped_context, skipped_requirement_ids = _group_requirements_by_issue(requirements, mapping_payloads)
    plans: list[_SyncIssuePlan] = []
    warnings: list[str] = []

    for issue_key in sorted(grouped_context):
        contexts = grouped_context[issue_key]
        grouped_requirements = [context.requirement for context in contexts]
        try:
            live_issue = adapter.get_issue(issue_key)
        except JiraAdapterError as exc:
            plans.append(
                _SyncIssuePlan(
                    issue_key=issue_key,
                    issue_type=None,
                    issue_url=_first_non_empty((context.requirement.source_issue_url for context in contexts), cast=str),
                    requirements=grouped_requirements,
                    status="missing",
                    live_issue_updated_at=None,
                    mapped_issue_updated_at=_resolve_group_baseline_updated_at(contexts),
                    existing_description_excerpt=None,
                    rendered_description_excerpt=None,
                    rendered_description_adf=None,
                    conflict_reason=str(exc),
                )
            )
            continue

        rendered_adf, render_warning = _upsert_managed_requirement_block(
            live_issue.description_adf,
            grouped_requirements,
            managed_section_title=managed_section_title,
        )
        rendered_excerpt = _truncate_text(_adf_to_text(rendered_adf), max_length=600)
        existing_excerpt = _truncate_text(live_issue.description_text or "", max_length=400) or None
        mapped_issue_updated_at = _resolve_group_baseline_updated_at(contexts)
        has_conflict = bool(
            mapped_issue_updated_at and live_issue.updated_at and live_issue.updated_at > mapped_issue_updated_at and conflict_strategy == "block"
        )
        conflict_reason = None
        if has_conflict:
            conflict_reason = (
                f"{issue_key} was updated in JIRA at {live_issue.updated_at.isoformat()} after the last imported baseline "
                f"{mapped_issue_updated_at.isoformat()}."
            )
        if render_warning:
            warnings.append(f"{issue_key}: {render_warning}")
        plans.append(
            _SyncIssuePlan(
                issue_key=issue_key,
                issue_type=live_issue.issue_type,
                issue_url=str(live_issue.web_url) if live_issue.web_url else None,
                requirements=grouped_requirements,
                status="conflict" if has_conflict else "ready",
                live_issue_updated_at=live_issue.updated_at,
                mapped_issue_updated_at=mapped_issue_updated_at,
                existing_description_excerpt=existing_excerpt,
                rendered_description_excerpt=rendered_excerpt,
                rendered_description_adf=rendered_adf,
                conflict_reason=conflict_reason,
                warning=render_warning,
            )
        )

    return plans, skipped_requirement_ids, _dedupe_strings(warnings)


def _group_requirements_by_issue(
    requirements: Sequence[Requirement],
    mapping_payloads: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[_SyncRequirementContext]], list[str]]:
    grouped: dict[str, list[_SyncRequirementContext]] = {}
    skipped_requirement_ids: list[str] = []
    for requirement in requirements:
        mapping_payload = mapping_payloads.get(requirement.artifact_item_id or "", {})
        if requirement.source_system != "jira" and not mapping_payload:
            skipped_requirement_ids.append(requirement.id)
            continue
        issue_key = (
            requirement.sync_target_issue_key
            or requirement.source_issue_key
            or str(mapping_payload.get("sync_target_issue_key") or mapping_payload.get("jira_issue_key") or "").strip()
        )
        if not issue_key:
            skipped_requirement_ids.append(requirement.id)
            continue
        grouped.setdefault(issue_key, []).append(_SyncRequirementContext(requirement=requirement, mapping_payload=mapping_payload))
    return grouped, skipped_requirement_ids


def _load_mapping_payloads(requirements: Sequence[Requirement]) -> dict[str, dict[str, Any]]:
    item_ids = [str(requirement.artifact_item_id or "").strip() for requirement in requirements if _requires_mapping_lookup(requirement)]
    if not item_ids:
        return {}
    collection = get_optional_firestore_collection(
        JIRA_REQUIREMENT_MAPPINGS_COLLECTION,
        unavailable_message="Firestore unavailable for JIRA sync mapping reads",
    )
    if collection is None:
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    for item_id in dict.fromkeys(item_ids):
        try:
            snapshot = collection.document(item_id).get()
        except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
            logging.warning("Could not read JIRA sync mapping %s: %s", item_id, exc)
            continue
        if getattr(snapshot, "exists", False):
            payloads[item_id] = snapshot.to_dict() or {}
    return payloads


def _requires_mapping_lookup(requirement: Requirement) -> bool:
    item_id = str(requirement.artifact_item_id or "").strip()
    if not item_id:
        return False

    has_jira_source = requirement.source_system == "jira"
    has_issue_target = bool(requirement.sync_target_issue_key or requirement.source_issue_key)
    has_baseline = requirement.source_issue_updated_at is not None
    return not (has_jira_source and has_issue_target and has_baseline)


def _resolve_group_baseline_updated_at(contexts: Sequence[_SyncRequirementContext]) -> Optional[datetime]:
    resolved_dates: list[datetime] = []
    for context in contexts:
        candidate = context.requirement.source_issue_updated_at or _coerce_datetime(context.mapping_payload.get("jira_issue_updated_at"))
        if candidate is not None:
            resolved_dates.append(candidate)
    return max(resolved_dates) if resolved_dates else None


def _upsert_managed_requirement_block(
    description_adf: Optional[dict[str, Any]],
    requirements: Sequence[Requirement],
    *,
    managed_section_title: str,
) -> tuple[dict[str, Any], Optional[str]]:
    document = _normalize_adf_document(description_adf)
    content = list(document.get("content") or [])
    start_index, end_index = _find_managed_block_range(content)
    warning = None
    managed_block = _build_managed_block_nodes(requirements, managed_section_title)

    if start_index is not None and end_index is not None and start_index <= end_index:
        content = content[:start_index] + managed_block + content[end_index + 1 :]
    else:
        if start_index is not None or end_index is not None:
            warning = "Existing managed block markers were incomplete, so a fresh managed block was appended."
            content = [node for node in content if _top_level_text(node) not in {MANAGED_BLOCK_START, MANAGED_BLOCK_END}]
        if content and _top_level_text(content[-1]):
            content.append(_paragraph_node(""))
        content.extend(managed_block)

    document["content"] = content
    return document, warning


def _build_managed_block_nodes(requirements: Sequence[Requirement], managed_section_title: str) -> list[dict[str, Any]]:
    sorted_requirements = sorted(requirements, key=lambda requirement: requirement.id)
    bullet_items = [
        {
            "type": "listItem",
            "content": [
                _paragraph_node(f"{requirement.id}: {requirement.text}"),
            ],
        }
        for requirement in sorted_requirements
    ]
    return [
        _paragraph_node(MANAGED_BLOCK_START),
        _paragraph_node(f"{managed_section_title} (managed by Test Engineer Agent)"),
        {"type": "bulletList", "content": bullet_items},
        _paragraph_node(MANAGED_BLOCK_END),
    ]


def _normalize_adf_document(description_adf: Optional[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(description_adf, dict) and description_adf.get("type") == "doc":
        document = deepcopy(description_adf)
        document.setdefault("version", 1)
        document.setdefault("type", "doc")
        document.setdefault("content", [])
        return document
    return {"version": 1, "type": "doc", "content": []}


def _find_managed_block_range(content: Sequence[dict[str, Any]]) -> tuple[Optional[int], Optional[int]]:
    start_index = None
    end_index = None
    for index, node in enumerate(content):
        text = _top_level_text(node)
        if text == MANAGED_BLOCK_START and start_index is None:
            start_index = index
        elif text == MANAGED_BLOCK_END and start_index is not None:
            end_index = index
            break
    return start_index, end_index


def _top_level_text(node: Any) -> str:
    return _adf_to_text(node).strip()


def _paragraph_node(text: str) -> dict[str, Any]:
    content = [] if not text else [{"type": "text", "text": text}]
    return {"type": "paragraph", "content": content}


def _adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for part in (_adf_to_text(item) for item in value) if part)
    if not isinstance(value, dict):
        return str(value).strip()
    if value.get("type") == "text":
        return str(value.get("text") or "")
    separator = "\n" if value.get("type") in {"doc", "paragraph", "heading", "listItem", "bulletList", "orderedList"} else " "
    fragments = [_adf_to_text(child) for child in (value.get("content") or [])]
    joined = separator.join(fragment for fragment in fragments if fragment)
    return "\n".join(part.strip() for part in joined.splitlines() if part.strip()).strip()


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _truncate_text(value: str, *, max_length: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max(0, max_length - 1)].rstrip() + "…"


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _first_non_empty(values: Iterable[Any], *, cast=str) -> Optional[Any]:
    for value in values:
        if value:
            return cast(value)
    return None


def _requirement_identity(requirement: Requirement) -> tuple[str, str, str]:
    return (
        str(requirement.artifact_item_id or ""),
        requirement.id,
        str(requirement.sync_target_issue_key or requirement.source_issue_key or ""),
    )
