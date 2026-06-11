from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import unquote, urlparse

from ..agents.requirements_agent import extract_requirements
from ..config import get_azure_devops_settings
from ..models import (
    AuthUser,
    AzureDevOpsImportInput,
    AzureDevOpsProjectWorkItemTypesResponse,
    AzureDevOpsProjectsResponse,
    AzureDevOpsWorkItemSearchResponse,
    AzureDevOpsWorkItemSummary,
    Requirement,
)
from .firebase_admin import get_firestore_client
from .azure_devops_connection_service import get_azure_devops_adapter_for_user

AZURE_DEVOPS_REQUIREMENT_MAPPINGS_COLLECTION = "azure_devops_requirement_mappings"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _get_collection() -> Optional[object]:
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime state
        logging.warning("Firestore unavailable for Azure DevOps requirement mapping writes: %s", exc)
        return None
    return client.collection(AZURE_DEVOPS_REQUIREMENT_MAPPINGS_COLLECTION)


def list_azure_devops_projects(
    *,
    current_user: AuthUser,
    query: Optional[str] = None,
    max_results: Optional[int] = None,
) -> AzureDevOpsProjectsResponse:
    settings = get_azure_devops_settings()
    adapter = get_azure_devops_adapter_for_user(current_user=current_user)
    projects = adapter.list_projects(
        query=query,
        max_results=max_results or settings.project_page_size,
    )
    return AzureDevOpsProjectsResponse(projects=projects)


def list_azure_devops_project_work_item_types(
    *,
    current_user: AuthUser,
    project: str,
) -> AzureDevOpsProjectWorkItemTypesResponse:
    adapter = get_azure_devops_adapter_for_user(current_user=current_user)
    normalized_project = str(project or "").strip()
    if not normalized_project:
        raise ValueError("Project is required to load Azure DevOps work item types")

    work_item_types = adapter.get_project_work_item_types(normalized_project)
    return AzureDevOpsProjectWorkItemTypesResponse(project=normalized_project, work_item_types=work_item_types)


def search_azure_devops_work_items(
    *,
    current_user: AuthUser,
    project: str,
    query: Optional[str] = None,
    work_item_type: Optional[str] = None,
    max_results: Optional[int] = None,
) -> AzureDevOpsWorkItemSearchResponse:
    settings = get_azure_devops_settings()
    adapter = get_azure_devops_adapter_for_user(current_user=current_user)
    normalized_project = str(project or "").strip()
    if not normalized_project:
        raise ValueError("Project is required to search Azure DevOps work items")

    total, work_items = adapter.search_work_items(
        project=normalized_project,
        query=query,
        work_item_type=work_item_type,
        max_results=max_results or settings.work_item_page_size,
    )
    return AzureDevOpsWorkItemSearchResponse(work_items=work_items, total=total)


def import_requirements_from_azure_devops(
    *,
    current_user: AuthUser,
    payload: AzureDevOpsImportInput,
    request_id: str | None = None,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    settings = get_azure_devops_settings()
    adapter = get_azure_devops_adapter_for_user(current_user=current_user)
    project = _resolve_project(adapter=adapter, project=payload.project)
    work_items = _resolve_import_work_items(
        adapter=adapter,
        payload=payload,
        project=project,
        page_size=settings.work_item_page_size,
    )
    if not work_items:
        raise ValueError("No Azure DevOps work items matched the requested import selection")

    raw_sections: list[str] = []
    item_workflows: list[tuple[AzureDevOpsWorkItemSummary, dict[str, Any]]] = []
    aggregated_requirements: list[Requirement] = []

    for work_item in work_items:
        item_text = _build_work_item_source_text(work_item)
        raw_sections.append(f"--- SOURCE: Azure DevOps #{work_item.work_item_id} ({work_item.work_item_type}) ---\n{item_text}")
        workflow = extract_requirements(
            item_text,
            1,
            payload.workflow_settings,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.azure_devops",
        )
        item_workflows.append((work_item, workflow))

        for requirement in workflow.get("requirements") or []:
            normalized = requirement if isinstance(requirement, Requirement) else Requirement.model_validate(requirement)
            aggregated_requirements.append(
                normalized.model_copy(
                    update={
                        "source_system": "azure_devops",
                        "source_issue_key": str(work_item.work_item_id),
                        "source_issue_type": work_item.work_item_type,
                        "source_parent_key": str(work_item.parent_id) if work_item.parent_id else None,
                        "source_parent_title": str(work_item.parent_id) if work_item.parent_id else None,
                        "source_issue_url": work_item.web_url,
                        "source_issue_updated_at": work_item.changed_at,
                        "source_path": _build_work_item_source_path(work_item),
                        "source_section": work_item.title,
                        "source_excerpt": _truncate_source_excerpt(item_text),
                        "source_hierarchy": _build_work_item_source_hierarchy(work_item),
                        "sync_target_issue_key": str(work_item.work_item_id),
                    }
                )
            )

    requirements = _renumber_requirements(aggregated_requirements)
    merged_workflow = _merge_work_item_workflows(item_workflows=item_workflows, requirements=requirements)
    source_names = [str(item.work_item_id) for item in work_items]
    source_name = source_names[0] if len(source_names) == 1 else f"{len(source_names)} Azure DevOps work items"

    return {
        **merged_workflow,
        "requirements": requirements,
        "raw_text": "\n\n".join(raw_sections),
        "source_name": source_name,
        "source_names": source_names,
        "work_item_count": len(work_items),
        "source_work_item_ids": source_names,
        "source_project": project,
    }


def persist_azure_devops_requirement_mappings(
    *,
    requirements: Sequence[Requirement],
    actor: Optional[AuthUser],
    request_id: str,
    workflow_run_id: Optional[str],
    source_event_id: Optional[str],
) -> list[Requirement]:
    collection = _get_collection()
    if collection is None:
        return list(requirements)

    now = _utcnow()
    persisted: list[Requirement] = []
    for requirement in requirements:
        if requirement.source_system != "azure_devops" or not requirement.source_issue_key or not requirement.artifact_item_id:
            persisted.append(requirement)
            continue

        payload = {
            "mapping_id": requirement.artifact_item_id,
            "requirement_id": requirement.id,
            "artifact_set_id": requirement.artifact_set_id,
            "artifact_item_id": requirement.artifact_item_id,
            "artifact_version_id": requirement.artifact_version_id,
            "artifact_version_number": requirement.artifact_version_number,
            "azure_work_item_id": requirement.source_issue_key,
            "azure_work_item_type": requirement.source_issue_type,
            "azure_parent_work_item_id": requirement.source_parent_key,
            "azure_work_item_url": str(requirement.source_issue_url) if requirement.source_issue_url else None,
            "azure_project": _extract_project_from_work_item_url(str(requirement.source_issue_url)) if requirement.source_issue_url else None,
            "azure_work_item_changed_at": requirement.source_issue_updated_at,
            "sync_target_work_item_id": requirement.sync_target_issue_key or requirement.source_issue_key,
            "actor_user_id": actor.sub if actor else None,
            "request_id": request_id,
            "workflow_run_id": workflow_run_id,
            "source_event_id": source_event_id,
            "content_hash": _hash_payload(
                {
                    "text": requirement.text,
                    "source_issue_key": requirement.source_issue_key,
                    "sync_target_issue_key": requirement.sync_target_issue_key,
                }
            ),
            "updated_at": now,
            **({"created_at": now} if requirement.artifact_version_number == 1 else {}),
        }
        try:
            collection.document(requirement.artifact_item_id).set(payload, merge=True)
        except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
            logging.warning("Failed to persist Azure DevOps requirement mapping for %s: %s", requirement.id, exc)
        persisted.append(requirement)

    return persisted


def _resolve_project(*, adapter, project: Optional[str]) -> str:
    normalized_project = str(project or adapter.default_project or "").strip()
    if not normalized_project:
        raise ValueError("Project is required to import Azure DevOps work items")
    return normalized_project


def _resolve_import_work_items(
    *,
    adapter,
    payload: AzureDevOpsImportInput,
    project: str,
    page_size: int,
) -> list[AzureDevOpsWorkItemSummary]:
    work_items: list[AzureDevOpsWorkItemSummary] = []
    selected_ids = [int(item_id) for item_id in ([payload.work_item_id] if payload.work_item_id else [])]
    selected_ids.extend(int(item_id) for item_id in (payload.work_item_ids or []) if int(item_id) > 0)
    selected_ids = list(dict.fromkeys(selected_ids))

    if selected_ids:
        for work_item_id in selected_ids:
            if payload.include_children:
                work_items.extend(adapter.get_work_item_with_children(project, work_item_id))
            else:
                work_items.append(adapter.get_work_item(project, work_item_id))
    else:
        ids = adapter.query_work_item_ids(
            project=project,
            wiql=payload.wiql or "",
            max_results=max(page_size * 4, page_size),
        )
        fetched = adapter.get_work_items(project, ids) if ids else []
        if payload.include_children:
            for item in fetched:
                work_items.extend(adapter.get_work_item_with_children(project, item.work_item_id))
        else:
            work_items.extend(fetched)

    deduped: list[AzureDevOpsWorkItemSummary] = []
    seen: set[int] = set()
    for item in sorted(work_items, key=lambda entry: (entry.parent_id or entry.work_item_id, 0 if not entry.parent_id else 1, entry.work_item_id)):
        if item.work_item_id in seen:
            continue
        seen.add(item.work_item_id)
        deduped.append(item)
    return deduped


def _build_work_item_source_text(work_item: AzureDevOpsWorkItemSummary) -> str:
    sections = [
        f"Work Item ID: {work_item.work_item_id}",
        f"Work Item Type: {work_item.work_item_type}",
        f"Title: {work_item.title}",
    ]
    if work_item.state:
        sections.append(f"State: {work_item.state}")
    if work_item.project:
        sections.append(f"Project: {work_item.project}")
    if work_item.parent_id:
        sections.append(f"Parent Work Item: {work_item.parent_id}")
    if work_item.area_path:
        sections.append(f"Area Path: {work_item.area_path}")
    if work_item.iteration_path:
        sections.append(f"Iteration Path: {work_item.iteration_path}")
    if work_item.tags:
        sections.append(f"Tags: {', '.join(work_item.tags)}")
    if work_item.description_text:
        sections.append(f"Description:\n{work_item.description_text}")
    if work_item.acceptance_criteria_text:
        sections.append(f"Acceptance Criteria:\n{work_item.acceptance_criteria_text}")
    return "\n".join(sections)


def _build_work_item_source_path(work_item: AzureDevOpsWorkItemSummary) -> str:
    hierarchy = _build_work_item_source_hierarchy(work_item)
    return " > ".join(hierarchy) if hierarchy else str(work_item.work_item_id)


def _build_work_item_source_hierarchy(work_item: AzureDevOpsWorkItemSummary) -> list[str]:
    hierarchy: list[str] = []
    if work_item.project:
        hierarchy.append(work_item.project)
    if work_item.parent_id:
        hierarchy.append(f"#{work_item.parent_id}")
    hierarchy.append(f"#{work_item.work_item_id} · {work_item.work_item_type}: {work_item.title}")
    return hierarchy


def _truncate_source_excerpt(text: str, limit: int = 600) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _renumber_requirements(requirements: Sequence[Requirement]) -> list[Requirement]:
    renumbered: list[Requirement] = []
    for index, requirement in enumerate(requirements, start=1):
        renumbered.append(requirement.model_copy(update={"id": f"REQ-{index:03d}"}))
    return renumbered


def _merge_work_item_workflows(
    *,
    item_workflows: Sequence[tuple[AzureDevOpsWorkItemSummary, dict[str, Any]]],
    requirements: Sequence[Requirement],
) -> dict[str, Any]:
    review_scores: list[int] = []
    review_thresholds: list[int] = []
    blocking_issues: list[str] = []
    suggestions: list[str] = []
    unmet_criteria: list[str] = []
    warnings: list[str] = []
    parser_failures: list[str] = []
    history: list[dict[str, Any]] = []
    diagnostics_payloads: list[dict[str, Any]] = []
    approved = bool(item_workflows)

    for work_item, workflow in item_workflows:
        review = workflow.get("review") or {}
        diagnostics = dict(workflow.get("workflow_diagnostics") or {})
        diagnostics_payloads.append(diagnostics)
        approved = approved and bool(workflow.get("approved", False))
        review_scores.append(int(review.get("score") or 0))
        review_thresholds.append(int(review.get("threshold") or 0))
        blocking_issues.extend(str(item) for item in (review.get("blocking_issues") or []))
        suggestions.extend(str(item) for item in (review.get("suggestions") or []))
        unmet_criteria.extend(str(item) for item in (review.get("unmet_criteria") or []))
        warnings.extend(str(item) for item in (diagnostics.get("warnings") or []))
        parser_failures.extend(str(item) for item in (diagnostics.get("parser_failures") or []))
        for iteration in workflow.get("iteration_history") or []:
            item = dict(iteration or {})
            item["iteration"] = len(history) + 1
            actor = str(item.get("actor") or "AzureDevOpsImport")
            item["actor"] = f"{actor} [#{work_item.work_item_id}]"
            history.append(item)

    threshold = max(review_thresholds or [0])
    summary = (
        f"Imported {len(item_workflows)} Azure DevOps work item{'s' if len(item_workflows) != 1 else ''} "
        f"and extracted {len(requirements)} requirement{'s' if len(requirements) != 1 else ''}."
    )
    diagnostics_status = "completed"
    if not item_workflows:
        diagnostics_status = "failed"
    elif not approved or any((payload.get("status") or "completed") != "completed" for payload in diagnostics_payloads):
        diagnostics_status = "partial"

    workflow_settings = next(
        ((workflow.get("workflow_settings") or {}) for _, workflow in item_workflows if workflow.get("workflow_settings")),
        {},
    )

    return {
        "approved": approved,
        "review": {
            "approved": approved,
            "score": round(sum(review_scores) / len(review_scores)) if review_scores else 0,
            "threshold": threshold,
            "summary": summary,
            "blocking_issues": _dedupe_strings(blocking_issues),
            "suggestions": _dedupe_strings(suggestions),
            "unmet_criteria": _dedupe_strings(unmet_criteria),
        },
        "iteration_history": history,
        "coverage_metrics": {
            "document_count": len(item_workflows),
            "source_work_item_count": len(item_workflows),
            "total_requirements": len(requirements),
            "requirements_per_document": round(len(requirements) / max(1, len(item_workflows)), 2),
            "source_work_item_ids": [str(item.work_item_id) for item, _ in item_workflows],
        },
        "workflow_settings": dict(workflow_settings),
        "workflow_diagnostics": {
            "status": diagnostics_status,
            "used_fallback": any(bool(payload.get("used_fallback")) for payload in diagnostics_payloads),
            "failure_reason": None if item_workflows else "no_azure_devops_work_items_selected",
            "timed_out": any(bool(payload.get("timed_out")) for payload in diagnostics_payloads),
            "stalled": any(bool(payload.get("stalled")) for payload in diagnostics_payloads),
            "max_iterations_reached": any(bool(payload.get("max_iterations_reached")) for payload in diagnostics_payloads),
            "parser_failures": _dedupe_strings(parser_failures),
            "warnings": _dedupe_strings(warnings),
            "best_iteration": None,
            "attempt_count": sum(int(payload.get("attempt_count") or 1) for payload in diagnostics_payloads) if diagnostics_payloads else 0,
        },
    }


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


def _extract_project_from_work_item_url(raw_url: str) -> Optional[str]:
    parsed = urlparse(str(raw_url or ""))
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if parsed.netloc.lower() == "dev.azure.com" and len(segments) >= 2:
        return segments[1]
    if parsed.netloc.lower().endswith(".visualstudio.com") and segments:
        return segments[0]
    return None