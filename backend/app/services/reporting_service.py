from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Literal, Optional

from ..models import AuthUser, UsageReportGroup, UsageReportResponse, UsageReportUserSummary
from ..auth.identity import extract_email_domain, is_public_email_domain, normalize_email, resolve_organization_domain
from .usage_event_repository import FirestoreUsageEventRepository, UsageEventRepository

_USAGE_EVENT_REPOSITORY: UsageEventRepository = FirestoreUsageEventRepository()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_usage_event_repository() -> UsageEventRepository:
    return _USAGE_EVENT_REPOSITORY


def set_usage_event_repository_for_testing(repository: UsageEventRepository) -> None:
    global _USAGE_EVENT_REPOSITORY
    _USAGE_EVENT_REPOSITORY = repository


def reset_usage_event_repository_for_testing() -> None:
    set_usage_event_repository_for_testing(FirestoreUsageEventRepository())


def _normalize_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_event_tenant_id(event: Dict[str, Any]) -> Optional[str]:
    actor = dict(event.get("actor") or {})
    return str(event.get("tenant_id") or actor.get("tenant_id") or "").strip() or None


def _extract_event_domain(event: Dict[str, Any]) -> Optional[str]:
    actor = dict(event.get("actor") or {})
    explicit_domain = str(event.get("organization_domain") or actor.get("organization_domain") or "").strip().lower()
    if explicit_domain:
        return explicit_domain
    return extract_email_domain(normalize_email(actor.get("email")))


def _viewer_can_access_event(
    event: Dict[str, Any],
    current_user: Optional[AuthUser],
    scope: Literal["all", "self", "organization"],
) -> bool:
    if current_user is None or scope == "all":
        return True

    viewer_user_id = str(current_user.sub or "").strip()
    viewer_email = normalize_email(current_user.email)
    actor = dict(event.get("actor") or {})
    actor_user_id = str(actor.get("user_id") or event.get("actor_user_id") or "").strip()
    actor_email = normalize_email(actor.get("email"))

    if scope == "self":
        return (bool(viewer_user_id) and actor_user_id == viewer_user_id) or (bool(viewer_email) and actor_email == viewer_email)

    viewer_tenant_id = str(current_user.tenant_id or "").strip() or None
    event_tenant_id = _extract_event_tenant_id(event)
    if viewer_tenant_id and event_tenant_id:
        return viewer_tenant_id == event_tenant_id

    viewer_domain = resolve_organization_domain(current_user)
    if not viewer_domain:
        return False

    return _extract_event_domain(event) == viewer_domain


def _event_metric_totals(event: Dict[str, Any]) -> Dict[str, int]:
    metadata = event.get("metadata") or {}
    event_type = str(event.get("event_type") or "").strip()
    quantity = _coerce_int(event.get("quantity"))

    return {
        "requirements_generated_count": _coerce_int(metadata.get("requirements_generated_count")) if event_type == "requirements.parsed" else 0,
        "requirements_modified_count": _coerce_int(metadata.get("requirements_modified_count")) if event_type == "requirements.refined" else 0,
        "test_cases_generated_count": _coerce_int(metadata.get("test_cases_generated_count")) if event_type == "testcases.generated" else 0,
        "test_cases_modified_count": _coerce_int(metadata.get("test_cases_modified_count")) if event_type == "testcases.refined" else 0,
        "fallback_quantity": quantity,
    }


def _derive_scope(actor: Dict[str, Any], fallback_user_id: str, *, force_individual: bool = False) -> Dict[str, Optional[str]]:
    email = normalize_email(actor.get("email"))
    name = str(actor.get("name") or "").strip() or None
    provider = str(actor.get("provider") or "").strip().lower() or None
    user_id = str(actor.get("user_id") or fallback_user_id or "unknown-user").strip() or "unknown-user"
    domain = str(actor.get("organization_domain") or "").strip().lower() or extract_email_domain(email)

    if force_individual or is_public_email_domain(domain):
        return {
            "scope_type": "individual",
            "scope_key": f"user:{user_id}",
            "display_name": email or name or user_id,
            "organization_domain": None,
            "user_id": user_id,
            "email": email or None,
            "name": name,
            "provider": provider,
        }

    return {
        "scope_type": "organization",
        "scope_key": f"org:{domain}",
        "display_name": domain,
        "organization_domain": domain,
        "user_id": user_id,
        "email": email or None,
        "name": name,
        "provider": provider,
    }


def _iter_usage_events() -> tuple[Iterable[Any], list[str]]:
    return get_usage_event_repository().iter_usage_events()


def build_usage_report(
    *,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    current_user: Optional[AuthUser] = None,
    scope: Literal["all", "self", "organization"] = "all",
) -> UsageReportResponse:
    if start_at and start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    if end_at and end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)

    documents, warnings = _iter_usage_events()
    groups: dict[str, dict[str, Any]] = {}
    total_events = 0

    for document in documents:
        event = document.to_dict() or {}
        occurred_at = _normalize_datetime(event.get("occurred_at"))
        if start_at and occurred_at and occurred_at < start_at:
            continue
        if end_at and occurred_at and occurred_at > end_at:
            continue
        if not _viewer_can_access_event(event, current_user, scope):
            continue

        actor = dict(event.get("actor") or {})
        scope_details = _derive_scope(actor, str(event.get("actor_user_id") or ""), force_individual=scope == "self")
        scope_key = str(scope_details["scope_key"])
        group = groups.setdefault(
            scope_key,
            {
                "scope_type": scope_details["scope_type"],
                "scope_key": scope_key,
                "display_name": scope_details["display_name"],
                "organization_domain": scope_details["organization_domain"],
                "total_events": 0,
                "requirements_generated_count": 0,
                "requirements_modified_count": 0,
                "test_cases_generated_count": 0,
                "test_cases_modified_count": 0,
                "event_breakdown": defaultdict(int),
                "latest_event_at": None,
                "users": {},
            },
        )

        user_id = str(scope_details["user_id"] or "unknown-user")
        user_summary = group["users"].setdefault(
            user_id,
            {
                "user_id": user_id,
                "email": scope_details["email"],
                "name": scope_details["name"],
                "provider": scope_details["provider"],
                "total_events": 0,
                "requirements_generated_count": 0,
                "requirements_modified_count": 0,
                "test_cases_generated_count": 0,
                "test_cases_modified_count": 0,
                "latest_event_at": None,
            },
        )

        metrics = _event_metric_totals(event)
        event_type = str(event.get("event_type") or "unknown")
        group["total_events"] += 1
        group["event_breakdown"][event_type] += 1
        user_summary["total_events"] += 1
        total_events += 1

        for metric_name in (
            "requirements_generated_count",
            "requirements_modified_count",
            "test_cases_generated_count",
            "test_cases_modified_count",
        ):
            group[metric_name] += metrics[metric_name]
            user_summary[metric_name] += metrics[metric_name]

        if occurred_at and (group["latest_event_at"] is None or occurred_at > group["latest_event_at"]):
            group["latest_event_at"] = occurred_at
        if occurred_at and (user_summary["latest_event_at"] is None or occurred_at > user_summary["latest_event_at"]):
            user_summary["latest_event_at"] = occurred_at

    report_groups: list[UsageReportGroup] = []
    for group in groups.values():
        users = [UsageReportUserSummary(**user) for user in group["users"].values()]
        users.sort(key=lambda user: ((user.latest_event_at or datetime.min.replace(tzinfo=timezone.utc)), user.email or user.user_id), reverse=True)
        report_groups.append(
            UsageReportGroup(
                scope_type=group["scope_type"],
                scope_key=group["scope_key"],
                display_name=group["display_name"],
                organization_domain=group["organization_domain"],
                total_events=group["total_events"],
                unique_user_count=len(users),
                requirements_generated_count=group["requirements_generated_count"],
                requirements_modified_count=group["requirements_modified_count"],
                test_cases_generated_count=group["test_cases_generated_count"],
                test_cases_modified_count=group["test_cases_modified_count"],
                event_breakdown=dict(sorted(group["event_breakdown"].items())),
                latest_event_at=group["latest_event_at"],
                users=users,
            )
        )

    report_groups.sort(
        key=lambda group: ((group.latest_event_at or datetime.min.replace(tzinfo=timezone.utc)), group.display_name),
        reverse=True,
    )

    return UsageReportResponse(
        generated_at=_utcnow(),
        start_at=start_at,
        end_at=end_at,
        total_groups=len(report_groups),
        total_events=total_events,
        groups=report_groups,
        warnings=warnings,
    )
