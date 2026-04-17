from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from ..models import UsageReportGroup, UsageReportResponse, UsageReportUserSummary
from .audit_service import USAGE_EVENTS_COLLECTION
from .firebase_admin import get_firestore_client

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "google.com",
    "live.com",
    "outlook.com",
    "hotmail.com",
    "msn.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_usage_events_collection():
    try:
        client = get_firestore_client()
    except Exception:
        return None

    return client.collection(USAGE_EVENTS_COLLECTION)


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


def _derive_scope(actor: Dict[str, Any], fallback_user_id: str) -> Dict[str, Optional[str]]:
    email = str(actor.get("email") or "").strip().lower()
    name = str(actor.get("name") or "").strip() or None
    provider = str(actor.get("provider") or "").strip().lower() or None
    user_id = str(actor.get("user_id") or fallback_user_id or "unknown-user").strip() or "unknown-user"
    domain = email.split("@", 1)[1] if "@" in email else None

    if not domain or domain in PUBLIC_EMAIL_DOMAINS:
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
    collection = _get_usage_events_collection()
    if collection is None:
        return [], ["Firestore usage report collection is unavailable."]

    try:
        return collection.stream(), []
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        return [], [f"Firestore usage report query failed: {exc}"]


def build_usage_report(
    *,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
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

        actor = dict(event.get("actor") or {})
        scope = _derive_scope(actor, str(event.get("actor_user_id") or ""))
        scope_key = str(scope["scope_key"])
        group = groups.setdefault(
            scope_key,
            {
                "scope_type": scope["scope_type"],
                "scope_key": scope_key,
                "display_name": scope["display_name"],
                "organization_domain": scope["organization_domain"],
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

        user_id = str(scope["user_id"] or "unknown-user")
        user_summary = group["users"].setdefault(
            user_id,
            {
                "user_id": user_id,
                "email": scope["email"],
                "name": scope["name"],
                "provider": scope["provider"],
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