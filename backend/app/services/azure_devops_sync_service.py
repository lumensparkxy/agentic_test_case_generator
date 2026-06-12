from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import logging
import re
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import unquote, urlparse

from ..adapters.azure_devops import AzureDevOpsAdapterError
from ..models import (
    AuthUser,
    AzureDevOpsSyncApplyInput,
    AzureDevOpsSyncApplyResponse,
    AzureDevOpsSyncPreviewInput,
    AzureDevOpsSyncPreviewResponse,
    AzureDevOpsSyncWorkItemPreview,
    AzureDevOpsSyncWorkItemResult,
    Requirement,
)
from .azure_devops_connection_service import get_azure_devops_adapter_for_user
from .firestore_repository import get_optional_firestore_collection

AZURE_DEVOPS_REQUIREMENT_MAPPINGS_COLLECTION = "azure_devops_requirement_mappings"
MANAGED_BLOCK_START = "<!-- AGENTIC_REQUIREMENTS_START -->"
MANAGED_BLOCK_END = "<!-- AGENTIC_REQUIREMENTS_END -->"
MANAGED_TARGET_FIELD = "system_description_managed_block"


@dataclass
class _SyncRequirementContext:
    requirement: Requirement
    mapping_payload: dict[str, Any]


@dataclass
class _SyncWorkItemPlan:
    project: str
    work_item_id: int
    work_item_type: Optional[str]
    work_item_url: Optional[str]
    requirements: list[Requirement]
    status: str
    live_changed_at: Optional[datetime]
    mapped_changed_at: Optional[datetime]
    existing_description_excerpt: Optional[str]
    rendered_description_excerpt: Optional[str]
    rendered_description_html: Optional[str]
    rev: Optional[int]
    conflict_reason: Optional[str] = None
    warning: Optional[str] = None


def preview_azure_devops_requirement_sync(
    *,
    current_user: AuthUser,
    payload: AzureDevOpsSyncPreviewInput,
) -> AzureDevOpsSyncPreviewResponse:
    adapter = get_azure_devops_adapter_for_user(current_user=current_user)
    plans, skipped_requirement_ids, warnings = _build_sync_plans(
        adapter=adapter,
        requirements=payload.requirements,
        managed_section_title=payload.managed_section_title,
        conflict_strategy=payload.conflict_strategy,
    )

    previews = [
        AzureDevOpsSyncWorkItemPreview(
            work_item_id=plan.work_item_id,
            work_item_type=plan.work_item_type,
            work_item_url=plan.work_item_url,
            project=plan.project,
            status=plan.status,
            requirement_ids=[requirement.id for requirement in plan.requirements],
            target_field=MANAGED_TARGET_FIELD,
            live_changed_at=plan.live_changed_at,
            mapped_changed_at=plan.mapped_changed_at,
            existing_description_excerpt=plan.existing_description_excerpt,
            rendered_description_excerpt=plan.rendered_description_excerpt,
            conflict_reason=plan.conflict_reason,
            warning=plan.warning,
        )
        for plan in plans
    ]
    return AzureDevOpsSyncPreviewResponse(
        work_items=previews,
        ready_work_item_count=sum(1 for preview in previews if preview.status == "ready"),
        conflict_count=sum(1 for preview in previews if preview.status == "conflict"),
        skipped_requirement_ids=skipped_requirement_ids,
        warnings=warnings,
    )


def apply_azure_devops_requirement_sync(
    *,
    current_user: AuthUser,
    payload: AzureDevOpsSyncApplyInput,
) -> AzureDevOpsSyncApplyResponse:
    adapter = get_azure_devops_adapter_for_user(current_user=current_user)
    plans, skipped_requirement_ids, warnings = _build_sync_plans(
        adapter=adapter,
        requirements=payload.requirements,
        managed_section_title=payload.managed_section_title,
        conflict_strategy="allow" if payload.allow_conflicts else payload.conflict_strategy,
    )

    results: list[AzureDevOpsSyncWorkItemResult] = []
    updated_requirements_by_identity: dict[tuple[str, str, str], Requirement] = {}

    for plan in plans:
        requirement_ids = [requirement.id for requirement in plan.requirements]
        if plan.status == "missing":
            results.append(
                AzureDevOpsSyncWorkItemResult(
                    work_item_id=plan.work_item_id,
                    status="failed",
                    requirement_ids=requirement_ids,
                    work_item_url=plan.work_item_url,
                    message=plan.conflict_reason or plan.warning or "Work item could not be loaded from Azure DevOps",
                )
            )
            continue

        if plan.status == "conflict" and not payload.allow_conflicts:
            results.append(
                AzureDevOpsSyncWorkItemResult(
                    work_item_id=plan.work_item_id,
                    status="conflict",
                    requirement_ids=requirement_ids,
                    work_item_url=plan.work_item_url,
                    updated_at=plan.live_changed_at,
                    message=plan.conflict_reason or "The Azure DevOps work item changed since it was imported.",
                )
            )
            continue

        if not plan.rendered_description_html:
            results.append(
                AzureDevOpsSyncWorkItemResult(
                    work_item_id=plan.work_item_id,
                    status="skipped",
                    requirement_ids=requirement_ids,
                    work_item_url=plan.work_item_url,
                    message=plan.warning or "No rendered Azure DevOps update was produced for this work item.",
                )
            )
            continue

        try:
            adapter.update_work_item_description(
                project=plan.project,
                work_item_id=plan.work_item_id,
                html_description=plan.rendered_description_html,
                rev=plan.rev,
                history_note="Agentic Test Case Generator synced managed requirements.",
            )
            refreshed = adapter.get_work_item(plan.project, plan.work_item_id)
        except AzureDevOpsAdapterError as exc:
            results.append(
                AzureDevOpsSyncWorkItemResult(
                    work_item_id=plan.work_item_id,
                    status="failed",
                    requirement_ids=requirement_ids,
                    work_item_url=plan.work_item_url,
                    message=str(exc),
                )
            )
            continue

        for requirement in plan.requirements:
            updated = requirement.model_copy(
                update={
                    "source_issue_key": requirement.source_issue_key or str(plan.work_item_id),
                    "source_issue_type": refreshed.work_item_type,
                    "source_issue_url": refreshed.web_url,
                    "source_issue_updated_at": refreshed.changed_at,
                    "sync_target_issue_key": requirement.sync_target_issue_key or str(plan.work_item_id),
                }
            )
            updated_requirements_by_identity[_requirement_identity(requirement)] = updated

        results.append(
            AzureDevOpsSyncWorkItemResult(
                work_item_id=plan.work_item_id,
                status="updated",
                requirement_ids=requirement_ids,
                work_item_url=refreshed.web_url,
                updated_at=refreshed.changed_at,
                message=f"Updated managed requirements block in Azure DevOps work item #{plan.work_item_id}.",
            )
        )

    final_requirements = [updated_requirements_by_identity.get(_requirement_identity(requirement), requirement) for requirement in payload.requirements]

    if skipped_requirement_ids:
        warnings.append(
            f"Skipped {len(skipped_requirement_ids)} requirement{'s' if len(skipped_requirement_ids) != 1 else ''} without an Azure DevOps sync target."
        )

    return AzureDevOpsSyncApplyResponse(
        results=results,
        updated_work_item_count=sum(1 for result in results if result.status == "updated"),
        skipped_work_item_count=sum(1 for result in results if result.status == "skipped"),
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
) -> tuple[list[_SyncWorkItemPlan], list[str], list[str]]:
    mapping_payloads = _load_mapping_payloads(requirements)
    grouped_context, skipped_requirement_ids, group_warnings = _group_requirements_by_work_item(
        requirements,
        mapping_payloads,
        default_project=getattr(adapter, "default_project", None),
    )
    plans: list[_SyncWorkItemPlan] = []
    warnings: list[str] = list(group_warnings)

    for project, work_item_id in sorted(grouped_context):
        contexts = grouped_context[(project, work_item_id)]
        grouped_requirements = [context.requirement for context in contexts]
        try:
            live_item = adapter.get_work_item(project, work_item_id)
        except AzureDevOpsAdapterError as exc:
            plans.append(
                _SyncWorkItemPlan(
                    project=project,
                    work_item_id=work_item_id,
                    work_item_type=None,
                    work_item_url=_first_non_empty((context.requirement.source_issue_url for context in contexts), cast=str),
                    requirements=grouped_requirements,
                    status="missing",
                    live_changed_at=None,
                    mapped_changed_at=_resolve_group_baseline_changed_at(contexts),
                    existing_description_excerpt=None,
                    rendered_description_excerpt=None,
                    rendered_description_html=None,
                    rev=None,
                    conflict_reason=str(exc),
                )
            )
            continue

        existing_html = str(live_item.fields.get("System.Description") or live_item.description_text or "")
        rendered_html, render_warning = _upsert_managed_requirement_block(
            existing_html,
            grouped_requirements,
            managed_section_title=managed_section_title,
        )
        rendered_excerpt = _truncate_text(_html_to_text(rendered_html), max_length=600)
        existing_excerpt = _truncate_text(live_item.description_text or _html_to_text(existing_html), max_length=400) or None
        mapped_changed_at = _resolve_group_baseline_changed_at(contexts)
        has_conflict = bool(mapped_changed_at and live_item.changed_at and live_item.changed_at > mapped_changed_at and conflict_strategy == "block")
        conflict_reason = None
        if has_conflict:
            conflict_reason = (
                f"Azure DevOps work item #{work_item_id} was updated at {live_item.changed_at.isoformat()} after the last imported baseline "
                f"{mapped_changed_at.isoformat()}."
            )
        if render_warning:
            warnings.append(f"#{work_item_id}: {render_warning}")
        plans.append(
            _SyncWorkItemPlan(
                project=project,
                work_item_id=work_item_id,
                work_item_type=live_item.work_item_type,
                work_item_url=str(live_item.web_url) if live_item.web_url else None,
                requirements=grouped_requirements,
                status="conflict" if has_conflict else "ready",
                live_changed_at=live_item.changed_at,
                mapped_changed_at=mapped_changed_at,
                existing_description_excerpt=existing_excerpt,
                rendered_description_excerpt=rendered_excerpt,
                rendered_description_html=rendered_html,
                rev=live_item.rev,
                conflict_reason=conflict_reason,
                warning=render_warning,
            )
        )

    return plans, skipped_requirement_ids, _dedupe_strings(warnings)


def _group_requirements_by_work_item(
    requirements: Sequence[Requirement],
    mapping_payloads: dict[str, dict[str, Any]],
    *,
    default_project: Optional[str],
) -> tuple[dict[tuple[str, int], list[_SyncRequirementContext]], list[str], list[str]]:
    grouped: dict[tuple[str, int], list[_SyncRequirementContext]] = {}
    skipped_requirement_ids: list[str] = []
    warnings: list[str] = []
    for requirement in requirements:
        mapping_payload = mapping_payloads.get(requirement.artifact_item_id or "", {})
        if requirement.source_system != "azure_devops" and not mapping_payload:
            skipped_requirement_ids.append(requirement.id)
            continue
        raw_work_item_id = (
            requirement.sync_target_issue_key
            or requirement.source_issue_key
            or str(mapping_payload.get("sync_target_work_item_id") or mapping_payload.get("azure_work_item_id") or "").strip()
        )
        work_item_id = _coerce_optional_int(raw_work_item_id)
        if not work_item_id:
            skipped_requirement_ids.append(requirement.id)
            continue
        project = _resolve_project_for_requirement(requirement, mapping_payload, default_project=default_project)
        if not project:
            skipped_requirement_ids.append(requirement.id)
            warnings.append(f"{requirement.id}: Azure DevOps project could not be resolved from mapping or work item URL.")
            continue
        grouped.setdefault((project, work_item_id), []).append(_SyncRequirementContext(requirement=requirement, mapping_payload=mapping_payload))
    return grouped, skipped_requirement_ids, _dedupe_strings(warnings)


def _load_mapping_payloads(requirements: Sequence[Requirement]) -> dict[str, dict[str, Any]]:
    item_ids = [str(requirement.artifact_item_id or "").strip() for requirement in requirements if _requires_mapping_lookup(requirement)]
    if not item_ids:
        return {}
    collection = get_optional_firestore_collection(
        AZURE_DEVOPS_REQUIREMENT_MAPPINGS_COLLECTION,
        unavailable_message="Firestore unavailable for Azure DevOps sync mapping reads",
    )
    if collection is None:
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    for item_id in dict.fromkeys(item_ids):
        try:
            snapshot = collection.document(item_id).get()
        except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
            logging.warning("Could not read Azure DevOps sync mapping %s: %s", item_id, exc)
            continue
        if getattr(snapshot, "exists", False):
            payloads[item_id] = snapshot.to_dict() or {}
    return payloads


def _requires_mapping_lookup(requirement: Requirement) -> bool:
    item_id = str(requirement.artifact_item_id or "").strip()
    if not item_id:
        return False

    has_azure_source = requirement.source_system == "azure_devops"
    has_work_item_target = bool(_coerce_optional_int(requirement.sync_target_issue_key or requirement.source_issue_key))
    has_project = bool(_extract_project_from_work_item_url(str(requirement.source_issue_url or "")))
    has_baseline = requirement.source_issue_updated_at is not None
    return not (has_azure_source and has_work_item_target and has_project and has_baseline)


def _resolve_group_baseline_changed_at(contexts: Sequence[_SyncRequirementContext]) -> Optional[datetime]:
    resolved_dates: list[datetime] = []
    for context in contexts:
        candidate = context.requirement.source_issue_updated_at or _coerce_datetime(context.mapping_payload.get("azure_work_item_changed_at"))
        if candidate is not None:
            resolved_dates.append(candidate)
    return max(resolved_dates) if resolved_dates else None


def _resolve_project_for_requirement(
    requirement: Requirement,
    mapping_payload: dict[str, Any],
    *,
    default_project: Optional[str],
) -> Optional[str]:
    mapped_project = str(mapping_payload.get("azure_project") or "").strip()
    if mapped_project:
        return mapped_project
    for raw_url in (requirement.source_issue_url, mapping_payload.get("azure_work_item_url")):
        project = _extract_project_from_work_item_url(str(raw_url or ""))
        if project:
            return project
    return str(default_project or "").strip() or None


def _extract_project_from_work_item_url(raw_url: str) -> Optional[str]:
    parsed = urlparse(str(raw_url or ""))
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if parsed.netloc.lower() == "dev.azure.com" and len(segments) >= 2:
        return segments[1]
    if parsed.netloc.lower().endswith(".visualstudio.com") and segments:
        return segments[0]
    return None


def _upsert_managed_requirement_block(
    description_html: str,
    requirements: Sequence[Requirement],
    *,
    managed_section_title: str,
) -> tuple[str, Optional[str]]:
    existing = str(description_html or "")
    start_index = existing.find(MANAGED_BLOCK_START)
    end_index = existing.find(MANAGED_BLOCK_END)
    managed_block = _build_managed_block_html(requirements, managed_section_title)
    warning = None

    if start_index >= 0 and end_index >= start_index:
        end_index += len(MANAGED_BLOCK_END)
        return (existing[:start_index].rstrip() + "\n" + managed_block + "\n" + existing[end_index:].lstrip()).strip(), None

    cleaned = existing
    if start_index >= 0 or end_index >= 0:
        warning = "Existing managed block markers were incomplete, so a fresh managed block was appended."
        cleaned = cleaned.replace(MANAGED_BLOCK_START, "").replace(MANAGED_BLOCK_END, "").strip()

    if cleaned.strip():
        return f"{cleaned.rstrip()}\n<hr />\n{managed_block}", warning
    return managed_block, warning


def _build_managed_block_html(requirements: Sequence[Requirement], managed_section_title: str) -> str:
    sorted_requirements = sorted(requirements, key=lambda requirement: requirement.id)
    items = "".join(f"<li><strong>{escape(requirement.id)}:</strong> {escape(requirement.text)}</li>" for requirement in sorted_requirements)
    return (
        f"{MANAGED_BLOCK_START}\n"
        f'<section data-agentic-managed="requirements">'
        f"<h3>{escape(managed_section_title)} (managed by Agentic Test Case Generator)</h3>"
        f"<ul>{items}</ul>"
        f"</section>\n"
        f"{MANAGED_BLOCK_END}"
    )


def _html_to_text(value: Any) -> str:
    normalized = str(value or "")
    if not normalized.strip():
        return ""
    normalized = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", normalized)
    normalized = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section)\s*>", "\n", normalized)
    normalized = re.sub(r"(?s)<!--.*?-->", " ", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = re.sub(r"[ \t\r\f\v]+", " ", normalized)
    return "\n".join(part.strip() for part in normalized.splitlines() if part.strip()).strip()


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


def _coerce_optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
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
