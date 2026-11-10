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
    TestCaseTemplate,
    UseCaseReviewRecord,
    UseCaseReviewResponse,
)
from ..contracts.approval_continuations import SavedPlanGenerationTarget
from . import provider_journey_budget as journeys
from .approval_continuation import ContinuationOptions, continuation_run_id, enqueue_approval
from .requirement_reconciliation import canonical_requirements, digest
from .requirement_scope import eligible_generation_scope
from .scenario_review_state import current_scenario_reviews, scenario_key
from .audit_service import build_actor_snapshot
from .firestore_repository import get_required_firestore_client
from .orchestrator_service import build_orchestrator_status
from .use_case_generation_service import _validate_output
from .workflow_project_service import (
    ProjectNotFoundError,
    ProjectPermissionError,
    get_project,
)


QA_PROJECTS_COLLECTION = "qa_projects"
_PREPARED_SCENARIO_RESERVATION = 7
_TEST_GENERATION_RESERVATION = 24
_JOURNEY_PROVIDER_REQUEST_LIMIT = _PREPARED_SCENARIO_RESERVATION + _TEST_GENERATION_RESERVATION
_SAVED_PLAN_ENROLLMENTS = "saved_plan_test_generation"


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


def use_case_review_id(project_id: str, request_id: str) -> str:
    """Return the durable review receipt document ID for this request."""
    return _stable_id("usecasereview", project_id, f"use_cases.review:{request_id}")


def _request_fingerprint(
    *,
    actor: AuthUser,
    snapshot_id: str,
    base_project_revision: int,
    decision: str,
    comment: Optional[str],
    scenario_reviews: Optional[list[dict]] = None,
    template: TestCaseTemplate | None = None,
) -> str:
    canonical = json.dumps(
        {
            "reviewer_user_id": actor.sub,
            "snapshot_id": snapshot_id,
            "base_project_revision": base_project_revision,
            "decision": decision,
            "comment": comment,
            **({"scenario_reviews": scenario_reviews} if scenario_reviews else {}),
            **({"template": template.model_dump(mode="json")} if template is not None else {}),
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


def _read_prepared_journey(
    transaction,
    project_doc,
    *,
    plan,
    actor_id,
    requirements_id,
    context_id,
    requirement_ids,
    options,
):
    """Find the exact completed preparation and determine if it can fund one child."""
    missing = "journey_budget_missing"
    exhausted = "journey_budget_exhausted"
    parent_id = continuation_run_id(project_doc.id, actor_id, "prepare_scenarios", requirements_id)
    plan_revision = plan.get("project_revision")
    if (
        plan.get("request_id") != parent_id
        or plan.get("actor_user_id") != actor_id
        or plan.get("operation") != "use_cases.generate"
        or type(plan_revision) is not int
        or plan_revision < 2
    ):
        return missing
    parent_doc = project_doc.collection("orchestrator_runs").document(parent_id)
    run = _document_to_dict(parent_doc.get(transaction=transaction)) or {}
    intent = run.get("continuation")
    if (
        run.get("run_id") != parent_id
        or run.get("project_id") != project_doc.id
        or run.get("actor_user_id") != actor_id
        or run.get("project_revision") != plan_revision
        or run.get("produced_snapshot_ids") != {"use_cases": plan["snapshot_id"]}
        or not isinstance(intent, dict)
        or type(intent.get("schema_version")) is not int
        or intent["schema_version"] != 1
        or intent.get("stage") != "prepare_scenarios"
        or intent.get("approval_id") != requirements_id
        or intent.get("approval_project_revision") != plan_revision - 1
        or intent.get("source_snapshot_ids") != {"requirements": requirements_id, "context": context_id}
        or intent.get("requirement_ids") != requirement_ids
    ):
        return missing
    if "journey_budget" in intent:
        return exhausted
    dispatch = intent.get("dispatch")
    reservation = intent.get("provider_requests_reserved")
    parent_options = intent.get("options")
    if (
        intent.get("state") != "awaiting_user"
        or intent.get("attempts") != 1
        or not isinstance(dispatch, dict)
        or type(dispatch.get("fence")) is not int
        or dispatch["fence"] < 1
        or not isinstance(dispatch.get("at"), datetime)
        or type(reservation) is not int
        or reservation < _PREPARED_SCENARIO_RESERVATION
        or not isinstance(parent_options, dict)
        or parent_options.get("max_attempts") != 1
    ):
        return missing
    if reservation + options.max_provider_requests > _JOURNEY_PROVIDER_REQUEST_LIMIT:
        return exhausted
    if (
        reservation != _PREPARED_SCENARIO_RESERVATION
        or parent_options.get("max_provider_requests") != reservation
        or options.max_provider_requests != _TEST_GENERATION_RESERVATION
        or options.max_attempts != 1
    ):
        return missing
    return parent_doc, intent


def _test_generation_scope(transaction, project_doc, project_payload, plan_snapshot, *, owner_id=None):
    if project_payload.get("status") != "active":
        return "project_inactive"
    stage_state = project_payload.get("stage_state") or {}
    requirements_state = stage_state.get("requirements") or {}
    baseline = _current_source(transaction, project_doc, "requirements", requirements_state)
    if not baseline:
        return "requirements_not_current"
    if owner_id is not None and (baseline.get("operation") != "requirements.review" or baseline.get("actor_user_id") != owner_id):
        return "requirements_not_eligible"
    try:
        rows, _ = canonical_requirements(project_doc.id, baseline)
    except AttributeError, TypeError, ValidationError:
        return "requirements_not_eligible"
    scope = eligible_generation_scope(rows, approved=bool(requirements_state.get("approved")))
    if not scope:
        return "requirements_not_eligible"

    context_state = stage_state.get("context") or {}
    context_id = context_state.get("current_snapshot_id")
    if context_state.get("stale") or (
        context_id and not _current_source(transaction, project_doc, "context", context_state, field_paths=("snapshot_id", "project_id", "stage"))
    ):
        return "context_not_current"
    requirements_id = baseline["snapshot_id"]
    metadata = plan_snapshot.get("metadata") or {}
    if (
        plan_snapshot.get("source_snapshot_id") != requirements_id
        or metadata.get("source_requirements_snapshot_id") != requirements_id
        or not recorded_use_case_context_matches(metadata, context_id)
    ):
        return "use_cases_source_mismatch"
    groups = plan_snapshot["payload"].get("coverage_plan") or []
    expected = {row.id: row.text for row in scope}
    if len(groups) != len(expected) or {group.get("requirement_id"): group.get("requirement_text") for group in groups if group.get("scenarios")} != expected:
        return "use_cases_scope_mismatch"
    return {"requirements": requirements_id, "context": context_id, "use_cases": plan_snapshot["snapshot_id"]}, scope


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
    require_prepared_journey,
    decided_at,
):
    scope = _test_generation_scope(transaction, project_doc, project_payload, plan_snapshot)
    if isinstance(scope, str):
        return {"continuation_blocked_reason": scope}
    sources, requirements = scope
    requirement_ids = [row.id for row in requirements]
    parent = None
    budget = None
    if require_prepared_journey:
        parent = _read_prepared_journey(
            transaction,
            project_doc,
            plan=plan_snapshot,
            actor_id=actor.sub,
            requirements_id=sources["requirements"],
            context_id=sources["context"],
            requirement_ids=requirement_ids,
            options=options,
        )
        if isinstance(parent, str):
            return {"continuation_blocked_reason": parent}
        journey_id = parent[1].get(journeys.BUDGET_FIELD)
        try:
            budget = journeys.budget_for_artifact(transaction, project_doc, actor.sub, "use_cases", sources["use_cases"], expected_id=journey_id)
        except HTTPException:
            return {"continuation_blocked_reason": journeys.UNAVAILABLE}
        if budget is not None:
            if journey_id != budget.id:
                return {"continuation_blocked_reason": journeys.UNAVAILABLE}
            if not journeys.available(budget, "generate_tests", options.max_provider_requests):
                return {"continuation_blocked_reason": journeys.EXHAUSTED}

    run_id = enqueue_approval(
        transaction,
        project_doc,
        client=client,
        stage="generate_tests",
        actor_id=actor.sub,
        approval_id=review_id,
        request_id=request_id,
        resulting_revision=resulting_revision,
        source_snapshot_ids=sources,
        requirement_ids=requirement_ids,
        options=options,
        provider_journey_id=budget.id if budget is not None else None,
    )
    if parent is not None:
        parent_doc, intent = parent
        transaction.update(
            parent_doc,
            {
                "status": "completed",
                "updated_at": decided_at,
                "continuation": {
                    **intent,
                    "state": "completed",
                    "journey_budget": {
                        "provider_request_limit": _JOURNEY_PROVIDER_REQUEST_LIMIT,
                        "scenario_requests_reserved": intent["provider_requests_reserved"],
                        "test_requests_allocated": options.max_provider_requests,
                        "child_run_id": run_id,
                        "scenario_review_id": review_id,
                    },
                },
            },
        )
    return {"continuation_run_id": run_id}


def _saved_plan_record(project_doc, kind, identity):
    return project_doc.collection(_SAVED_PLAN_ENROLLMENTS).document(f"{kind}_{digest(identity)}")


def _saved_plan_receipt_matches(receipt, project_doc, actor, request_id):
    return (
        receipt
        and receipt.get("project_id") == project_doc.id
        and receipt.get("actor_user_id") == actor.sub
        and receipt.get("request_id") == request_id
        and isinstance(receipt.get("run_id"), str)
    )


def saved_plan_generation_receipt(project_doc, actor, request_id):
    receipt = _document_to_dict(_saved_plan_record(project_doc, "request", request_id).get())
    return receipt["run_id"] if _saved_plan_receipt_matches(receipt, project_doc, actor, request_id) else None


def _saved_plan_generation_basis(transaction, project_doc, project, actor):
    """Verify the immutable ordinary plan and the owner's current whole decision."""
    stage_state = project.get("stage_state") or {}
    use_cases = stage_state.get("use_cases") or {}
    if project.get("status") != "active" or (stage_state.get("test_cases") or {}).get("current_snapshot_id") or use_cases.get("approved") is not True:
        return None
    plan = _current_source(transaction, project_doc, "use_cases", use_cases)
    if not plan or plan.get("operation") != "use_cases.generate" or plan.get("actor_user_id") != actor.sub:
        return None
    plan_request = plan.get("request_id")
    metadata = plan.get("metadata") or {}
    source_id = plan.get("source_snapshot_id")
    if not isinstance(plan_request, str) or not plan_request or not source_id or metadata.get("use_case_refresh"):
        return None
    prepared_id = continuation_run_id(project_doc.id, actor.sub, "prepare_scenarios", source_id)
    if plan_request == prepared_id:
        return None
    review_state = use_cases.get("metadata") or {}
    latest = review_state.get("latest_human_review") or {}
    plan_id = plan["snapshot_id"]
    revision = project.get("current_revision")
    if (
        type(revision) is not int
        or revision < 1
        or latest.get("snapshot_id") != plan_id
        or latest.get("decision") != "approve"
        or latest.get("reviewer_user_id") != actor.sub
        or latest.get("resulting_project_revision") != revision
    ):
        return None
    review_id = latest.get("review_id")
    if not isinstance(review_id, str) or not review_id or "/" in review_id:
        return None
    review = _document_to_dict(project_doc.collection("use_case_reviews").document(review_id).get(transaction=transaction))
    if (
        not review
        or review.get("review_id") != review_id
        or review.get("project_id") != project_doc.id
        or review.get("stage") != "use_cases"
        or review.get("snapshot_id") != plan_id
        or review.get("decision") != "approve"
        or review.get("reviewer_user_id") != actor.sub
        or review.get("resulting_project_revision") != revision
        or review.get("require_prepared_journey")
    ):
        return None
    try:
        items = current_scenario_reviews(plan["payload"], review_state, plan_id)
    except KeyError, TypeError, ValueError:
        return None
    if not items or any(item["status"] != "approved" for item in items.values()):
        return None
    scope = _test_generation_scope(transaction, project_doc, project, plan, owner_id=actor.sub)
    if isinstance(scope, str):
        return None
    prepared = _document_to_dict(
        project_doc.collection("orchestrator_runs").document(prepared_id).get(transaction=transaction, field_paths=("produced_snapshot_ids",))
    )
    if prepared and (prepared.get("produced_snapshot_ids") or {}).get("use_cases") == plan_id:
        return None
    try:
        _validate_output(plan["payload"], scope[1])
        target = SavedPlanGenerationTarget(base_project_revision=revision, snapshot_id=plan_id, review_id=review_id)
    except HTTPException, ValidationError:
        return None
    return target, scope


def saved_plan_generation_status(project_doc, actor):
    project = _document_to_dict(project_doc.get()) or {}
    if project.get("project_id") != project_doc.id or project.get("owner_user_id") != actor.sub:
        return None, None
    plan_id = ((project.get("stage_state") or {}).get("use_cases") or {}).get("current_snapshot_id")
    if not plan_id:
        return None, None
    enrollment = _document_to_dict(_saved_plan_record(project_doc, "plan", plan_id).get())
    if enrollment:
        return None, enrollment.get("run_id")
    basis = _saved_plan_generation_basis(None, project_doc, project, actor)
    return (basis[0], None) if basis else (None, None)


def enqueue_saved_plan_generation(*, client, project_doc, actor, target, request_id, options):
    if options.max_attempts != 1 or options.max_provider_requests != _TEST_GENERATION_RESERVATION:
        raise ValueError("Saved-plan generation requires one bounded Test Cases stage.")
    fingerprint = digest([target.model_dump(mode="json"), options.template.model_dump(mode="json") if options.template else None])

    @transactional
    def enroll(transaction):
        project = _document_to_dict(project_doc.get(transaction=transaction))
        if not project or project.get("project_id") != project_doc.id or project.get("owner_user_id") != actor.sub:
            raise ProjectNotFoundError(project_doc.id)
        request_doc = _saved_plan_record(project_doc, "request", request_id)
        receipt = _document_to_dict(request_doc.get(transaction=transaction))
        if receipt:
            if not _saved_plan_receipt_matches(receipt, project_doc, actor, request_id) or receipt.get("fingerprint") != fingerprint:
                raise HTTPException(409, "This request identity already belongs to another generation request.")
            return receipt["run_id"]
        if _document_to_dict(project_doc.collection("use_case_reviews").document(use_case_review_id(project_doc.id, request_id)).get(transaction=transaction)):
            raise HTTPException(409, "This request identity already belongs to a scenario review.")
        plan_doc = _saved_plan_record(project_doc, "plan", target.snapshot_id)
        if _document_to_dict(plan_doc.get(transaction=transaction)):
            raise HTTPException(409, {"code": "saved_plan_generation_exists", "message": "Check the existing Test Cases run for this saved plan."})
        basis = _saved_plan_generation_basis(transaction, project_doc, project, actor)
        if not basis or basis[0] != target:
            raise HTTPException(
                409, {"code": "saved_plan_generation_unavailable", "message": "Reload Review and verify the current approved scope and scenario plan."}
            )
        sources, requirements = basis[1]
        run_id = enqueue_approval(
            transaction,
            project_doc,
            client=client,
            stage="generate_tests",
            actor_id=actor.sub,
            approval_id=target.review_id,
            request_id=request_id,
            resulting_revision=target.base_project_revision,
            source_snapshot_ids=sources,
            requirement_ids=[row.id for row in requirements],
            options=options,
        )
        record = {"project_id": project_doc.id, "actor_user_id": actor.sub, "request_id": request_id, "run_id": run_id, "fingerprint": fingerprint}
        transaction.create(plan_doc, record)
        transaction.create(request_doc, record)
        return run_id

    return enroll(client.transaction())


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
    require_prepared_journey: bool = False,
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
            and existing_review.get("require_prepared_journey", False) == require_prepared_journey
            # The named path can replay after server settings change. Request data, including its template, must still match.
            and (require_prepared_journey or existing_review.get("continuation_options_fingerprint") == continuation_fingerprint)
        ):
            return UseCaseReviewRecord.model_validate(existing_review)
        raise UseCaseReviewConflictError(
            "This X-Request-ID was already used for a different Use Cases review. Reload and retry with a new request identity.",
            latest_revision=current_revision,
            current_snapshot_id=current_snapshot_id,
        )
    if _document_to_dict(_saved_plan_record(project_doc, "request", request_id).get(transaction=transaction)):
        raise UseCaseReviewConflictError(
            "This X-Request-ID belongs to a saved-plan generation request.",
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
    if require_prepared_journey:
        snapshot_fields += ("request_id", "operation", "actor_user_id", "project_revision")
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

    if require_prepared_journey and snapshot_payload.get("operation") == "project.sync.rebind":
        raise HTTPException(409, {"code": "sync_rebind_ordinary_approval_required", "message": "Approve this rebound plan in the ordinary scenario review."})

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
        if require_prepared_journey:
            review_payload["require_prepared_journey"] = True
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
                    require_prepared_journey=require_prepared_journey,
                    decided_at=decided_at,
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
    require_prepared_journey: bool = False,
) -> UseCaseReviewResponse:
    if require_prepared_journey and continuation is None:
        raise ValueError("Prepared journey approval requires continuation options")
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
        template=continuation.template if continuation is not None else None,
    )
    review_id = use_case_review_id(project_id, request_id)
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
            require_prepared_journey=require_prepared_journey,
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
    "use_case_review_id",
    "use_case_review_error_to_http",
]
