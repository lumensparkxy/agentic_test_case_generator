"""Explicit scope approval and read-only recovery for local scenario preparation."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from google.cloud.firestore_v1 import Query as FirestoreQuery
from pydantic import ValidationError

from ..auth.jwt_auth import get_current_user
from ..contracts.approval_continuations import (
    ApprovalContinuationReceipt,
    ApprovalContinuationStatus,
    ApprovalContinuationView,
    ApproveAndPrepareScenariosInput,
    ApproveAndPrepareScenariosResult,
)
from ..models import AuthUser
from ..services.approval_continuation import cancel_stage
from ..services.approval_scenario_worker import approval_scenario_client_allowed, approval_scenarios_enabled, scenario_continuation_options
from ..services.requirement_import_service import repository
from ..services.requirement_reconciliation import digest
from ..services.workflow_project_service import project_error_to_http


router = APIRouter()
_RUN_LIMIT = 20


def _require_enabled(client=None):
    allowed = approval_scenarios_enabled() if client is None else approval_scenario_client_allowed(client)
    if not allowed:
        raise HTTPException(409, "Scenario preparation is disabled in this environment.")


def _project_doc(repo, project_id, actor):
    doc = repo.project_doc(project_id)
    project = doc.get(field_paths=("project_id", "owner_user_id")).to_dict()
    if not project or project.get("project_id") != project_id or project.get("owner_user_id") != actor.sub:
        raise HTTPException(404, "Project not found")
    return doc


def _run_view(run, project_id, actor_id):
    if not run or run.get("project_id") != project_id or run.get("actor_user_id") != actor_id:
        return None
    intent = run.get("continuation")
    if not isinstance(intent, dict) or intent.get("schema_version") != 1 or intent.get("stage") != "prepare_scenarios":
        return None
    produced = run.get("produced_snapshot_ids")
    try:
        return ApprovalContinuationView(
            run_id=run.get("run_id"),
            state=intent.get("state"),
            stage=intent["stage"],
            project_revision=run.get("project_revision"),
            request_id=run.get("request_id"),
            reason=intent.get("reason"),
            result_snapshot_id=produced.get("use_cases") if isinstance(produced, dict) else None,
        )
    except ValidationError:
        return None


def _read_run(doc, actor, run_id):
    run = doc.collection("orchestrator_runs").document(run_id).get().to_dict()
    view = _run_view(run, doc.id, actor.sub)
    return view if view and view.run_id == run_id else None


def _read_receipt(doc, request_id):
    receipt = doc.collection("requirement_import_receipts").document(digest(request_id)).get().to_dict()
    if not receipt or not receipt.get("continuation_options_fingerprint"):
        return None
    run_id = receipt.get("continuation_run_id")
    blocked_reason = receipt.get("continuation_blocked_reason")
    if bool(run_id) == bool(blocked_reason):
        return None
    return ApprovalContinuationReceipt(request_id=request_id, run_id=run_id, blocked_reason=blocked_reason)


@router.get("/projects/{project_id}/approval-continuations", response_model=ApprovalContinuationStatus)
def list_approval_continuations(
    project_id: str,
    request_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    actor: AuthUser = Depends(get_current_user),
):
    try:
        repo = repository()
        doc = _project_doc(repo, project_id, actor)
        # Ordering by a private continuation field excludes ordinary orchestrator runs.
        snapshots = (
            doc.collection("orchestrator_runs")
            .order_by("continuation.approval_project_revision", direction=FirestoreQuery.DESCENDING)
            .limit(_RUN_LIMIT)
            .stream()
        )
        runs = [view for snapshot in snapshots if (view := _run_view(snapshot.to_dict(), project_id, actor.sub)) is not None]
        receipt = _read_receipt(doc, request_id) if request_id is not None else None
        if receipt and receipt.run_id and all(view.run_id != receipt.run_id for view in runs):
            # Keep an exact recovery target readable even after it falls out of the recent window.
            requested = _read_run(doc, actor, receipt.run_id)
            if requested is not None:
                runs = [*runs[: _RUN_LIMIT - 1], requested]
        return ApprovalContinuationStatus(enabled=approval_scenario_client_allowed(repo.client), runs=runs, receipt=receipt)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/requirements/approve-and-prepare-scenarios", response_model=ApproveAndPrepareScenariosResult)
def approve_and_prepare_scenarios(
    project_id: str,
    payload: ApproveAndPrepareScenariosInput,
    request: Request,
    actor: AuthUser = Depends(get_current_user),
):
    _require_enabled()
    request_id = payload.idempotency_key
    supplied_request_id = request.headers.get("X-Request-ID")
    if supplied_request_id is not None and supplied_request_id != request_id:
        raise HTTPException(422, "X-Request-ID must match the approval request identity.")
    try:
        repo = repository()
        _require_enabled(repo.client)
        project = repo.review(project_id, actor, payload, request_id, continuation=scenario_continuation_options())
        doc = _project_doc(repo, project_id, actor)
        receipt = _read_receipt(doc, request_id)
        continuation = _read_run(doc, actor, receipt.run_id) if receipt and receipt.run_id else None
        if not receipt or (receipt.run_id and not continuation):
            raise HTTPException(503, "The saved approval outcome could not be confirmed. Check its status before retrying.")
        return ApproveAndPrepareScenariosResult(project=project, continuation=continuation, blocked_reason=receipt.blocked_reason)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/approval-continuations/{run_id}/cancel", response_model=ApprovalContinuationView)
def cancel_approval_continuation(
    project_id: str,
    run_id: Annotated[str, Path(pattern=r"^continuation_[0-9a-f]{64}$")],
    actor: AuthUser = Depends(get_current_user),
):
    _require_enabled()
    try:
        repo = repository()
        _require_enabled(repo.client)
        doc = _project_doc(repo, project_id, actor)
        if _read_run(doc, actor, run_id) is None:
            raise HTTPException(404, "Scenario preparation run not found")
        cancel_stage(repo.client, project_id=project_id, run_id=run_id, actor_id=actor.sub)
        view = _read_run(doc, actor, run_id)
        if view is None:
            raise HTTPException(503, "The cancellation outcome could not be confirmed. Check its status before retrying.")
        return view
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc
