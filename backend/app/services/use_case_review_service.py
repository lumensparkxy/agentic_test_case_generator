from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from google.cloud.firestore_v1 import transactional
from pydantic import ValidationError

from ..models import (
    AuthUser,
    UseCaseReviewRecord,
    UseCaseReviewResponse,
)
from .approval_continuation import ContinuationOptions, enqueue_approval
from .requirement_reconciliation import canonical_requirements, digest
from .requirement_scope import eligible_generation_scope
from .scenario_review_state import current_scenario_reviews, scenario_key
from .audit_service import build_actor_snapshot
from .firestore_repository import get_required_firestore_client
from .orchestrator_service import build_orchestrator_status
from .workflow_project_service import (
    ProjectNotFoundError,
    ProjectPermissionError,
    get_project,
)


QA_PROJECTS_COLLECTION = "qa_projects"


class UseCaseReviewConflictError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        latest_revision: int,
        current_snapshot_id: Optional[str],
    ) -> None:
        super().__init__(message)
        self.latest_revision = latest_revision
        self.current_snapshot_id = current_snapshot_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _request_fingerprint(
    *,
    actor: AuthUser,
    snapshot_id: str,
    base_project_revision: int,
    decision: str,
    comment: Optional[str],
    scenario_reviews: Optional[list[dict]] = None,
) -> str:
    canonical = json.dumps(
        {
            "reviewer_user_id": actor.sub,
            "snapshot_id": snapshot_id,
            "base_project_revision": base_project_revision,
            "decision": decision,
            "comment": comment,
            **({"scenario_reviews": scenario_reviews} if scenario_reviews else {}),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _document_to_dict(document_snapshot: Any) -> Optional[dict[str, Any]]:
    if document_snapshot is None or not getattr(document_snapshot, "exists", False):
        return None
    return dict(document_snapshot.to_dict() or {})


def _require_owner(payload: dict[str, Any], actor: AuthUser) -> None:
    if payload.get("owner_user_id") != actor.sub:
        raise ProjectPermissionError(str(payload.get("project_id") or "project"))


def _current_source(transaction, project_doc, stage, state, *, field_paths=None):
    snapshot_id = state.get("current_snapshot_id")
    if state.get("stale") or not snapshot_id:
        return None
    snapshot = _document_to_dict(project_doc.collection("snapshots").document(snapshot_id).get(transaction=transaction, field_paths=field_paths))
    if not snapshot or snapshot.get("snapshot_id") != snapshot_id or snapshot.get("project_id") != project_doc.id or snapshot.get("stage") != stage:
        return None
    return snapshot


def recorded_use_case_context_matches(metadata: dict[str, Any], context_snapshot_id: str | None) -> bool:
    """Check exact saved Context lineage, including explicit absence."""
    if metadata.get("source_context_snapshot_id") != context_snapshot_id:
        return False
    omitted = metadata.get("source_context_omitted") is True
    if context_snapshot_id is None:
        return omitted or "source_context_snapshot_id" in metadata
    return not omitted


def _test_generation_intent(
    transaction,
    project_doc,
    *,
    client,
    project_payload,
    plan_snapshot,
    actor,
    request_id,
    review_id,
    resulting_revision,
    options,
):
    if project_payload.get("status") != "active":
        return {"continuation_blocked_reason": "project_inactive"}
    stage_state = project_payload.get("stage_state") or {}
    requirements_state = stage_state.get("requirements") or {}
    baseline = _current_source(transaction, project_doc, "requirements", requirements_state)
    if not baseline:
        return {"continuation_blocked_reason": "requirements_not_current"}
    try:
        rows, _ = canonical_requirements(project_doc.id, baseline)
    except AttributeError, TypeError, ValidationError:
        return {"continuation_blocked_reason": "requirements_not_eligible"}
    scope = eligible_generation_scope(rows, approved=bool(requirements_state.get("approved")))
    if not scope:
        return {"continuation_blocked_reason": "requirements_not_eligible"}

    context_state = stage_state.get("context") or {}
    context_id = context_state.get("current_snapshot_id")
    if context_state.get("stale") or (
        context_id and not _current_source(transaction, project_doc, "context", context_state, field_paths=("snapshot_id", "project_id", "stage"))
    ):
        return {"continuation_blocked_reason": "context_not_current"}
    requirements_id = baseline["snapshot_id"]
    metadata = plan_snapshot.get("metadata") or {}
    if (
        plan_snapshot.get("source_snapshot_id") != requirements_id
        or metadata.get("source_requirements_snapshot_id") != requirements_id
        or not recorded_use_case_context_matches(metadata, context_id)
    ):
        return {"continuation_blocked_reason": "use_cases_source_mismatch"}
    groups = plan_snapshot["payload"].get("coverage_plan") or []
    expected = {row.id: row.text for row in scope}
    if len(groups) != len(expected) or {group.get("requirement_id"): group.get("requirement_text") for group in groups if group.get("scenarios")} != expected:
        return {"continuation_blocked_reason": "use_cases_scope_mismatch"}

    return {
        "continuation_run_id": enqueue_approval(
            transaction,
            project_doc,
            client=client,
            stage="generate_tests",
            actor_id=actor.sub,
            approval_id=review_id,
            request_id=request_id,
            resulting_revision=resulting_revision,
            source_snapshot_ids={"requirements": requirements_id, "context": context_id, "use_cases": plan_snapshot["snapshot_id"]},
            requirement_ids=[row.id for row in scope],
            options=options,
        )
    }


def _apply_review_transaction(
    transaction: Any,
    *,
    client: Any,
    project_doc: Any,
    review_doc: Any,
    timeline_doc: Any,
    actor: AuthUser,
    request_id: str,
    snapshot_id: str,
    base_project_revision: int,
    decision: str,
    comment: Optional[str],
    review_id: str,
    timeline_event_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    decided_at: datetime,
    continuation: ContinuationOptions | None = None,
    scenario_reviews: Optional[list[dict]] = None,
) -> UseCaseReviewRecord:
    project_payload = _document_to_dict(project_doc.get(transaction=transaction))
    if project_payload is None:
        raise ProjectNotFoundError(str(getattr(project_doc, "id", "project")))
    _require_owner(project_payload, actor)

    existing_review = _document_to_dict(review_doc.get(transaction=transaction))
    current_revision = int(project_payload.get("current_revision") or 0)
    stage_state = dict(project_payload.get("stage_state") or {})
    use_cases_state = dict(stage_state.get("use_cases") or {})
    current_snapshot_id = use_cases_state.get("current_snapshot_id")
    continuation_fingerprint = digest(continuation.model_dump(mode="json")) if continuation is not None else None
    if existing_review is not None:
        if (
            existing_review.get("request_fingerprint") == request_fingerprint
            and existing_review.get("continuation_options_fingerprint") == continuation_fingerprint
        ):
            return UseCaseReviewRecord.model_validate(existing_review)
        raise UseCaseReviewConflictError(
            "This X-Request-ID was already used for a different Use Cases review. Reload and retry with a new request identity.",
            latest_revision=current_revision,
            current_snapshot_id=current_snapshot_id,
        )

    if current_revision != base_project_revision:
        raise UseCaseReviewConflictError(
            "Project state changed after this review loaded. Reload the current Use Cases snapshot before retrying.",
            latest_revision=current_revision,
            current_snapshot_id=current_snapshot_id,
        )
    if not current_snapshot_id or current_snapshot_id != snapshot_id:
        raise UseCaseReviewConflictError(
            "The reviewed Use Cases snapshot is no longer current. Reload before submitting a decision.",
            latest_revision=current_revision,
            current_snapshot_id=current_snapshot_id,
        )

    snapshot_doc = project_doc.collection("snapshots").document(snapshot_id)
    snapshot_fields = ("snapshot_id", "project_id", "stage", "payload")
    if continuation is not None:
        snapshot_fields += ("source_snapshot_id", "metadata")
    snapshot_payload = _document_to_dict(
        snapshot_doc.get(
            transaction=transaction,
            field_paths=snapshot_fields,
        )
    )
    if (
        snapshot_payload is None
        or snapshot_payload.get("snapshot_id") != snapshot_id
        or snapshot_payload.get("project_id") != project_payload.get("project_id")
        or snapshot_payload.get("stage") != "use_cases"
    ):
        raise UseCaseReviewConflictError(
            "The current Use Cases snapshot could not be verified. Reload before submitting a decision.",
            latest_revision=current_revision,
            current_snapshot_id=current_snapshot_id,
        )

    resulting_revision = current_revision + 1
    approved = decision == "approve"
    review_metadata = dict(use_cases_state.get("metadata") or {})
    try:
        items = current_scenario_reviews(snapshot_payload.get("payload") or {}, review_metadata, snapshot_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not items:
        raise HTTPException(status_code=422, detail="No scenarios are available for review.")
    if use_cases_state.get("stale") and (approved or any(item["status"] == "approved" for item in scenario_reviews or [])):
        raise UseCaseReviewConflictError(
            "This artifact is stale. Regenerate before approval.", latest_revision=current_revision, current_snapshot_id=current_snapshot_id
        )
    updates = scenario_reviews or []
    if decision in ("approve", "request_changes"):
        updates = [{**item, "status": "approved" if approved else "request_changes"} for item in items.values()]
    for update in updates:
        key = scenario_key(update["requirement_id"], update["scenario_id"])
        if key not in items:
            raise HTTPException(status_code=422, detail="Scenario does not belong to the current artifact.")
        items[key] = {
            **items[key],
            "status": update["status"],
            "quality_flags": update["quality_flags"],
            "reviewer_user_id": actor.sub,
            "reviewer_name": actor.name,
            "reviewed_at": decided_at.isoformat(),
        }
    approved = approved and all(item["status"] == "approved" for item in items.values())
    review_metadata["scenario_reviews"] = {"snapshot_id": snapshot_id, "items": items}
    review_metadata["latest_human_review"] = {
        "review_id": review_id,
        "snapshot_id": snapshot_id,
        "decision": decision,
        "comment": comment,
        "reviewer_user_id": actor.sub,
        "reviewer_name": actor.name,
        "reviewer_email": actor.email,
        "reviewed_at": decided_at.isoformat(),
        "resulting_project_revision": resulting_revision,
    }
    use_cases_state.update(
        {
            "approved": approved,
            "updated_at": decided_at,
            "metadata": review_metadata,
        }
    )
    stage_state["use_cases"] = use_cases_state

    review_payload = {
        "review_id": review_id,
        "project_id": project_payload["project_id"],
        "stage": "use_cases",
        "snapshot_id": snapshot_id,
        "decision": decision,
        "comment": comment,
        "reviewer_user_id": actor.sub,
        "reviewer_name": actor.name,
        "reviewer_email": actor.email,
        "scenario_reviews": [{key: item[key] for key in ("requirement_id", "scenario_id", "status", "quality_flags")} for item in updates],
        "reviewer": build_actor_snapshot(actor),
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
        "timeline_event_id": timeline_event_id,
        "base_project_revision": base_project_revision,
        "resulting_project_revision": resulting_revision,
        "decided_at": decided_at,
    }
    timeline_payload = {
        "event_id": timeline_event_id,
        "project_id": project_payload["project_id"],
        "event_type": "use_cases.scenarios_reviewed"
        if decision == "review_scenarios"
        else "use_cases.review_approved"
        if approved
        else "use_cases.changes_requested",
        "stage": "use_cases",
        "summary": "Scenario reviews saved" if decision == "review_scenarios" else "Use Cases approved" if approved else "Changes requested for Use Cases",
        "project_revision": resulting_revision,
        "snapshot_id": snapshot_id,
        "actor_user_id": actor.sub,
        "metadata": {
            "review_id": review_id,
            "decision": decision,
            "comment": comment,
            "request_id": request_id,
        },
        "occurred_at": decided_at,
    }

    if continuation is not None:
        review_payload["continuation_options_fingerprint"] = continuation_fingerprint
        if approved:
            review_payload.update(
                _test_generation_intent(
                    transaction,
                    project_doc,
                    client=client,
                    project_payload=project_payload,
                    plan_snapshot=snapshot_payload,
                    actor=actor,
                    request_id=request_id,
                    review_id=review_id,
                    resulting_revision=resulting_revision,
                    options=continuation,
                )
            )
        else:
            review_payload["continuation_blocked_reason"] = "use_cases_not_approved"

    transaction.create(review_doc, review_payload)
    transaction.update(
        project_doc,
        {
            "updated_at": decided_at,
            "current_revision": resulting_revision,
            "stage_state": stage_state,
            "latest_stage": "use_cases",
            "request_id": request_id,
        },
    )
    transaction.create(timeline_doc, timeline_payload)
    return UseCaseReviewRecord.model_validate(review_payload)


def review_use_case_snapshot(
    *,
    project_id: str,
    snapshot_id: str,
    base_project_revision: int,
    decision: str,
    comment: Optional[str],
    actor: AuthUser,
    request_id: str,
    scenario_reviews: Optional[list[dict]] = None,
    continuation: ContinuationOptions | None = None,
) -> UseCaseReviewResponse:
    client = get_required_firestore_client(
        unavailable_message="Firestore client unavailable for Use Cases review persistence",
    )
    project_doc = client.collection(QA_PROJECTS_COLLECTION).document(project_id)
    idempotency_key = f"use_cases.review:{request_id}"
    request_fingerprint = _request_fingerprint(
        actor=actor,
        snapshot_id=snapshot_id,
        base_project_revision=base_project_revision,
        decision=decision,
        comment=comment,
        scenario_reviews=scenario_reviews,
    )
    review_id = _stable_id("usecasereview", project_id, idempotency_key)
    timeline_event_id = _stable_id("timeline", project_id, idempotency_key)
    review_doc = project_doc.collection("use_case_reviews").document(review_id)
    timeline_doc = project_doc.collection("timeline").document(timeline_event_id)
    decided_at = _utcnow()

    def apply(transaction: Any) -> UseCaseReviewRecord:
        return _apply_review_transaction(
            transaction,
            client=client,
            project_doc=project_doc,
            review_doc=review_doc,
            timeline_doc=timeline_doc,
            actor=actor,
            request_id=request_id,
            snapshot_id=snapshot_id,
            base_project_revision=base_project_revision,
            decision=decision,
            comment=comment,
            review_id=review_id,
            timeline_event_id=timeline_event_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            decided_at=decided_at,
            continuation=continuation,
            scenario_reviews=scenario_reviews,
        )

    review = transactional(apply)(client.transaction())
    project = get_project(project_id, actor=actor)
    use_cases_state = project.stage_state.get("use_cases")
    if use_cases_state is None:  # pragma: no cover - transaction guarantees the state
        raise RuntimeError("Use Cases state disappeared after review persistence")
    from .knowledge_feedback import suggest_feedback

    suggestion = suggest_feedback(actor, project_id, "use_cases", comment, request_id)
    return UseCaseReviewResponse(
        knowledge_suggestion=suggestion,
        review=review,
        project_revision=project.current_revision,
        use_cases_state=use_cases_state,
        orchestrator_status=build_orchestrator_status(project),
    )


def use_case_review_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if isinstance(exc, ProjectPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project access denied")
    if isinstance(exc, UseCaseReviewConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "latest_revision": exc.latest_revision,
                "current_snapshot_id": exc.current_snapshot_id,
                "reload_required": True,
            },
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Use Cases review persistence is unavailable",
    )


__all__ = [
    "UseCaseReviewConflictError",
    "review_use_case_snapshot",
    "use_case_review_error_to_http",
]
