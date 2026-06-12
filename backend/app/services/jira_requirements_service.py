from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from ..agents.requirements_agent import extract_requirements
from ..config import get_jira_settings
from ..models import (
    AuthUser,
    JiraImportInput,
    JiraIssueSearchResponse,
    JiraIssueSummary,
    JiraProjectIssueTypesResponse,
    JiraProjectsResponse,
    Requirement,
)
from .firestore_repository import get_optional_firestore_collection
from .jira_connection_service import get_jira_adapter_for_user

JIRA_REQUIREMENT_MAPPINGS_COLLECTION = "jira_requirement_mappings"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _get_collection() -> Optional[object]:
    return get_optional_firestore_collection(
        JIRA_REQUIREMENT_MAPPINGS_COLLECTION,
        unavailable_message="Firestore unavailable for JIRA requirement mapping writes",
    )


def list_jira_projects(
    *,
    current_user: AuthUser,
    query: Optional[str] = None,
    max_results: Optional[int] = None,
) -> JiraProjectsResponse:
    settings = get_jira_settings()
    adapter = get_jira_adapter_for_user(current_user=current_user)
    projects = adapter.list_projects(
        query=query,
        max_results=max_results or settings.project_page_size,
    )
    return JiraProjectsResponse(projects=projects)


def list_jira_project_issue_types(
    *,
    current_user: AuthUser,
    project_key: str,
) -> JiraProjectIssueTypesResponse:
    adapter = get_jira_adapter_for_user(current_user=current_user)
    normalized_project_key = str(project_key or "").strip()
    if not normalized_project_key:
        raise ValueError("Project key is required to load JIRA issue types")

    issue_types = adapter.get_project_issue_types(normalized_project_key)
    return JiraProjectIssueTypesResponse(project_key=normalized_project_key, issue_types=issue_types)


def search_jira_issues(
    *,
    current_user: AuthUser,
    project_key: str,
    query: Optional[str] = None,
    issue_type: Optional[str] = None,
    max_results: Optional[int] = None,
) -> JiraIssueSearchResponse:
    settings = get_jira_settings()
    adapter = get_jira_adapter_for_user(current_user=current_user)
    jql_clauses = [f'project = "{_escape_jql_value(project_key)}"']
    normalized_issue_type = str(issue_type or "").strip()
    if normalized_issue_type and normalized_issue_type.lower() not in {"any", "any issue type", "all"}:
        jql_clauses.append(f'issuetype = "{_escape_jql_value(normalized_issue_type)}"')
    normalized_query = str(query or "").strip()
    if normalized_query:
        jql_clauses.append(f'summary ~ "{_escape_jql_value(normalized_query)}"')

    jql = " AND ".join(jql_clauses)
    if jql:
        jql = f"{jql} ORDER BY updated DESC"

    total, issues = adapter.search_issue_summaries(
        jql,
        max_results=max_results or settings.issue_page_size,
    )
    return JiraIssueSearchResponse(issues=issues, total=total)


def import_requirements_from_jira(
    *,
    current_user: AuthUser,
    payload: JiraImportInput,
    request_id: str | None = None,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    settings = get_jira_settings()
    adapter = get_jira_adapter_for_user(current_user=current_user)
    issues = _resolve_import_issues(adapter=adapter, payload=payload, page_size=settings.issue_page_size)
    if not issues:
        raise ValueError("No JIRA issues matched the requested import selection")

    raw_sections: list[str] = []
    issue_workflows: list[tuple[JiraIssueSummary, dict[str, Any]]] = []
    aggregated_requirements: list[Requirement] = []

    for issue in issues:
        issue_text = _build_issue_source_text(issue)
        raw_sections.append(f"--- SOURCE: {issue.key} ({issue.issue_type}) ---\n{issue_text}")
        workflow = extract_requirements(
            issue_text,
            1,
            payload.workflow_settings,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.jira",
        )
        issue_workflows.append((issue, workflow))

        for requirement in workflow.get("requirements") or []:
            normalized = requirement if isinstance(requirement, Requirement) else Requirement.model_validate(requirement)
            aggregated_requirements.append(
                normalized.model_copy(
                    update={
                        "source_system": "jira",
                        "source_issue_key": issue.key,
                        "source_issue_type": issue.issue_type,
                        "source_parent_key": issue.parent_key,
                        "source_parent_title": issue.parent_key,
                        "source_issue_url": issue.web_url,
                        "source_issue_updated_at": issue.updated_at,
                        "source_path": _build_issue_source_path(issue),
                        "source_section": issue.summary,
                        "source_excerpt": _truncate_source_excerpt(issue_text),
                        "source_hierarchy": _build_issue_source_hierarchy(issue),
                        "sync_target_issue_key": issue.key,
                    }
                )
            )

    requirements = _renumber_requirements(aggregated_requirements)
    merged_workflow = _merge_issue_workflows(issue_workflows=issue_workflows, requirements=requirements)
    source_names = [issue.key for issue in issues]
    source_name = source_names[0] if len(source_names) == 1 else f"{len(source_names)} JIRA issues"

    return {
        **merged_workflow,
        "requirements": requirements,
        "raw_text": "\n\n".join(raw_sections),
        "source_name": source_name,
        "source_names": source_names,
        "issue_count": len(issues),
        "source_issue_keys": source_names,
    }


def persist_jira_requirement_mappings(
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
        if requirement.source_system != "jira" or not requirement.source_issue_key or not requirement.artifact_item_id:
            persisted.append(requirement)
            continue

        payload = {
            "mapping_id": requirement.artifact_item_id,
            "requirement_id": requirement.id,
            "artifact_set_id": requirement.artifact_set_id,
            "artifact_item_id": requirement.artifact_item_id,
            "artifact_version_id": requirement.artifact_version_id,
            "artifact_version_number": requirement.artifact_version_number,
            "jira_issue_key": requirement.source_issue_key,
            "jira_issue_type": requirement.source_issue_type,
            "jira_parent_key": requirement.source_parent_key,
            "jira_issue_url": str(requirement.source_issue_url) if requirement.source_issue_url else None,
            "jira_issue_updated_at": requirement.source_issue_updated_at,
            "sync_target_issue_key": requirement.sync_target_issue_key or requirement.source_issue_key,
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
            logging.warning("Failed to persist JIRA requirement mapping for %s: %s", requirement.id, exc)
        persisted.append(requirement)

    return persisted


def _resolve_import_issues(*, adapter, payload: JiraImportInput, page_size: int) -> list[JiraIssueSummary]:
    issues: list[JiraIssueSummary] = []
    normalized_issue_keys = [str(issue_key).strip() for issue_key in (payload.issue_keys or []) if str(issue_key).strip()]

    if normalized_issue_keys:
        for issue_key in normalized_issue_keys:
            issue = adapter.get_issue(issue_key)
            issues.extend(_expand_issue_selection(adapter=adapter, issue=issue, include_children=payload.include_children, page_size=page_size))
    elif (payload.epic_key or "").strip():
        epic = adapter.get_issue(payload.epic_key)
        issues.extend(_expand_issue_selection(adapter=adapter, issue=epic, include_children=payload.include_children, page_size=page_size))
    else:
        _, issues = adapter.search_issue_summaries_paginated(
            payload.jql or "",
            max_results=max(page_size * 4, page_size),
            page_size=page_size,
        )

    deduped: list[JiraIssueSummary] = []
    seen: set[str] = set()
    for issue in sorted(issues, key=lambda item: (item.parent_key or item.key, 0 if item.issue_type.lower() == "epic" else 1, item.key)):
        if issue.key in seen:
            continue
        seen.add(issue.key)
        deduped.append(issue)
    return deduped


def _expand_issue_selection(*, adapter, issue: JiraIssueSummary, include_children: bool, page_size: int) -> list[JiraIssueSummary]:
    if include_children and issue.issue_type.lower() == "epic":
        return adapter.get_epic_with_children(issue.key, page_size=page_size)
    return [issue]


def _build_issue_source_text(issue: JiraIssueSummary) -> str:
    sections = [
        f"Issue Key: {issue.key}",
        f"Issue Type: {issue.issue_type}",
        f"Summary: {issue.summary}",
    ]
    if issue.status:
        sections.append(f"Status: {issue.status}")
    if issue.parent_key:
        sections.append(f"Parent Issue: {issue.parent_key}")
    if issue.labels:
        sections.append(f"Labels: {', '.join(issue.labels)}")
    if issue.description_text:
        sections.append(f"Description:\n{issue.description_text}")
    return "\n".join(sections)


def _build_issue_source_path(issue: JiraIssueSummary) -> str:
    hierarchy = _build_issue_source_hierarchy(issue)
    return " > ".join(hierarchy) if hierarchy else issue.key


def _build_issue_source_hierarchy(issue: JiraIssueSummary) -> list[str]:
    hierarchy: list[str] = []
    if issue.parent_key:
        hierarchy.append(f"{issue.parent_key}")
    hierarchy.append(f"{issue.key} · {issue.issue_type}: {issue.summary}")
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


def _merge_issue_workflows(
    *,
    issue_workflows: Sequence[tuple[JiraIssueSummary, dict[str, Any]]],
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
    approved = bool(issue_workflows)

    for issue, workflow in issue_workflows:
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
            actor = str(item.get("actor") or "JiraImport")
            item["actor"] = f"{actor} [{issue.key}]"
            history.append(item)

    threshold = max(review_thresholds or [0])
    summary = (
        f"Imported {len(issue_workflows)} JIRA issue{'s' if len(issue_workflows) != 1 else ''} "
        f"and extracted {len(requirements)} requirement{'s' if len(requirements) != 1 else ''}."
    )
    diagnostics_status = "completed"
    if not issue_workflows:
        diagnostics_status = "failed"
    elif not approved or any((payload.get("status") or "completed") != "completed" for payload in diagnostics_payloads):
        diagnostics_status = "partial"

    workflow_settings = next(
        ((workflow.get("workflow_settings") or {}) for _, workflow in issue_workflows if workflow.get("workflow_settings")),
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
            "document_count": len(issue_workflows),
            "source_issue_count": len(issue_workflows),
            "total_requirements": len(requirements),
            "requirements_per_document": round(len(requirements) / max(1, len(issue_workflows)), 2),
            "source_issue_keys": [issue.key for issue, _ in issue_workflows],
        },
        "workflow_settings": dict(workflow_settings),
        "workflow_diagnostics": {
            "status": diagnostics_status,
            "used_fallback": any(bool(payload.get("used_fallback")) for payload in diagnostics_payloads),
            "failure_reason": None if issue_workflows else "no_jira_issues_selected",
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


def _escape_jql_value(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')
