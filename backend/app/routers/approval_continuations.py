"""Explicit approval actions and read-only recovery for local generation."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from google.cloud.firestore_v1 import Query as FirestoreQuery
from pydantic import ValidationError

from ..auth.jwt_auth import get_current_user
from ..contracts.approval_continuations import (
    ApprovalContinuationReceipt,
    ApprovalContinuationStage,
    ApprovalContinuationStatus,
    ApprovalContinuationView,
    ApproveAndGenerateTestsInput,
    ApproveAndGenerateTestsResult,
    ApproveAndPrepareScenariosInput,
    ApproveAndPrepareScenariosResult,
    SavedReviewAdmissionView,
    SavedReviewReleaseInput,
    SavedReviewReleaseResult,
)
from ..models import AuthUser
from ..services import use_case_review_service
from ..services.approval_continuation import (
    ContinuationConflict,
    ContinuationOptions,
    can_retry_undispatched_stage,
    cancel_stage,
    retry_undispatched_stage,
)
from ..services.approval_scenario_worker import (
    approval_scenario_client_allowed,
    approval_scenarios_enabled,
    approval_test_cases_enabled,
    scenario_continuation_options,
    test_case_continuation_options,
)
from ..services.project_draft_admission import draft_admission_client, read_draft_attempt, release_draft_attempt
from ..services.requirement_import_service import repository
from ..services.requirement_reconciliation import digest
from ..services.workflow_project_service import project_error_to_http


router = APIRouter()
_RUN_LIMIT = 20
_STAGE_ARTIFACTS = {
    "prepare_scenarios": ("requirements", "use_cases"),
    "generate_tests": ("use_cases", "test_cases"),
}


def _require_enabled(client=None, *, stage: ApprovalContinuationStage = "prepare_scenarios"):
    scenarios_enabled = approval_scenarios_enabled() if client is None else approval_scenario_client_allowed(client)
    if stage == "generate_tests":
        if not (scenarios_enabled and approval_test_cases_enabled()):
            raise HTTPException(409, "Test Cases generation is disabled in this environment.")
    elif not scenarios_enabled:
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
    if not isinstance(intent, dict) or intent.get("schema_version") != 1 or intent.get("stage") not in _STAGE_ARTIFACTS:
        return None
    sources = intent.get("source_snapshot_ids")
    if not isinstance(sources, dict):
        return None
    source_stage, result_stage = _STAGE_ARTIFACTS[intent["stage"]]
    produced = run.get("produced_snapshot_ids")
    try:
        return ApprovalContinuationView(
            run_id=run.get("run_id"),
            state=intent.get("state"),
            stage=intent["stage"],
            project_revision=run.get("project_revision"),
            request_id=run.get("request_id"),
            reason=intent.get("reason"),
            source_snapshot_id=sources.get(source_stage),
            result_snapshot_id=produced.get(result_stage) if isinstance(produced, dict) else None,
            retry_available=can_retry_undispatched_stage(run),
        )
    except ValidationError:
        return None


def _read_run(doc, actor, run_id, *, stage=None):
    run = doc.collection("orchestrator_runs").document(run_id).get().to_dict()
    view = _run_view(run, doc.id, actor.sub)
    return view if view and view.run_id == run_id and (stage is None or view.stage == stage) else None


def _read_receipt(doc, actor, request_id, stage):
    if stage == "generate_tests":
        review_id = use_case_review_service.use_case_review_id(doc.id, request_id)
        receipt = doc.collection("use_case_reviews").document(review_id).get().to_dict()
        if not receipt or receipt.get("review_id") != review_id or receipt.get("project_id") != doc.id or receipt.get("reviewer_user_id") != actor.sub:
            return None
        if receipt.get("request_id") != request_id or receipt.get("decision") != "approve" or receipt.get("require_prepared_journey") is not True:
            return None
    else:
        receipt = doc.collection("requirement_import_receipts").document(digest(request_id)).get().to_dict()
    if not receipt or not receipt.get("continuation_options_fingerprint"):
        return None
    run_id = receipt.get("continuation_run_id")
    blocked_reason = receipt.get("continuation_blocked_reason")
    if bool(run_id) == bool(blocked_reason):
        return None
    return ApprovalContinuationReceipt(request_id=request_id, run_id=run_id, blocked_reason=blocked_reason)


def _approval_outcome(doc, actor, request_id, stage):
    receipt = _read_receipt(doc, actor, request_id, stage)
    continuation = _read_run(doc, actor, receipt.run_id, stage=stage) if receipt and receipt.run_id else None
    if not receipt or (receipt.run_id and not continuation):
        raise HTTPException(503, "The saved approval outcome could not be confirmed. Check its status before retrying.")
    return continuation, receipt.blocked_reason


@router.get("/projects/{project_id}/approval-continuations", response_model=ApprovalContinuationStatus)
def list_approval_continuations(
    project_id: str,
    request_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    stage: Annotated[ApprovalContinuationStage, Query()] = "prepare_scenarios",
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
        receipt = _read_receipt(doc, actor, request_id, stage) if request_id is not None else None
        if receipt and receipt.run_id and all(view.run_id != receipt.run_id for view in runs):
            # Keep an exact recovery target readable even after it falls out of the recent window.
            requested = _read_run(doc, actor, receipt.run_id, stage=stage)
            if requested is not None:
                runs = [*runs[: _RUN_LIMIT - 1], requested]
        scenarios_enabled = approval_scenario_client_allowed(repo.client)
        draft_client = draft_admission_client(repo.client, read_only=True)
        attempt = read_draft_attempt(draft_client, project_id, actor.sub) if draft_client is not None else None
        return ApprovalContinuationStatus(
            enabled=scenarios_enabled,
            tests_enabled=scenarios_enabled and approval_test_cases_enabled(),
            runs=runs,
            receipt=receipt,
            saved_review_enabled=draft_client is not None,
            saved_review_attempt=SavedReviewAdmissionView.model_validate(attempt) if attempt and attempt["operation"] == "test_cases.review" else None,
        )
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
        continuation, blocked_reason = _approval_outcome(doc, actor, request_id, "prepare_scenarios")
        return ApproveAndPrepareScenariosResult(project=project, continuation=continuation, blocked_reason=blocked_reason)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/use-cases/approve-and-generate-tests", response_model=ApproveAndGenerateTestsResult)
def approve_and_generate_tests(
    project_id: str,
    payload: ApproveAndGenerateTestsInput,
    request: Request,
    actor: AuthUser = Depends(get_current_user),
):
    _require_enabled(stage="generate_tests")
    request_id = request.state.request_id
    if len(request_id) > 160 or not request_id.strip():
        raise HTTPException(422, "X-Request-ID must be nonblank and at most 160 characters.")
    try:
        repo = repository()
        _require_enabled(repo.client, stage="generate_tests")
        doc = _project_doc(repo, project_id, actor)
        response = use_case_review_service.review_use_case_snapshot(
            project_id=project_id,
            snapshot_id=payload.snapshot_id,
            base_project_revision=payload.base_project_revision,
            decision=payload.decision,
            comment=payload.comment,
            actor=actor,
            request_id=request_id,
            continuation=test_case_continuation_options(payload.template),
            require_prepared_journey=True,
        )
        continuation, blocked_reason = _approval_outcome(doc, actor, request_id, "generate_tests")
        return ApproveAndGenerateTestsResult(**response.model_dump(), continuation=continuation, blocked_reason=blocked_reason)
    except HTTPException:
        raise
    except Exception as exc:
        raise use_case_review_service.use_case_review_error_to_http(exc) from exc


@router.post(
    "/projects/{project_id}/approval-continuations/saved-review/{operation_id}/release",
    response_model=SavedReviewReleaseResult,
)
def release_saved_review_admission(
    project_id: str,
    operation_id: Annotated[str, Path(pattern=r"^draft_[0-9a-f]{64}$")],
    payload: SavedReviewReleaseInput,
    actor: AuthUser = Depends(get_current_user),
):
    try:
        repo = repository()
        client = draft_admission_client(repo.client)
        if client is None:
            raise HTTPException(409, "Saved-suite review recovery is disabled in this environment.")
        _project_doc(repo, project_id, actor)
        release_draft_attempt(client, project_id, actor.sub, operation_id, payload.fence)
        return SavedReviewReleaseResult()
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
        current = _read_run(doc, actor, run_id)
        if current is None:
            raise HTTPException(404, "Approval continuation run not found")
        _require_enabled(repo.client, stage=current.stage)
        cancel_stage(repo.client, project_id=project_id, run_id=run_id, actor_id=actor.sub)
        view = _read_run(doc, actor, run_id)
        if view is None:
            raise HTTPException(503, "The cancellation outcome could not be confirmed. Check its status before retrying.")
        return view
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/approval-continuations/{run_id}/retry", response_model=ApprovalContinuationView)
def retry_approval_continuation(
    project_id: str,
    run_id: Annotated[str, Path(pattern=r"^continuation_[0-9a-f]{64}$")],
    actor: AuthUser = Depends(get_current_user),
):
    _require_enabled()
    try:
        repo = repository()
        _require_enabled(repo.client)
        doc = _project_doc(repo, project_id, actor)
        run = doc.collection("orchestrator_runs").document(run_id).get().to_dict()
        current = _run_view(run, project_id, actor.sub)
        if current is None or current.run_id != run_id:
            raise HTTPException(404, "Approval continuation run not found")
        _require_enabled(repo.client, stage=current.stage)
        receipt = _read_receipt(doc, actor, current.request_id, current.stage)
        if not receipt or receipt.run_id != run_id:
            raise HTTPException(409, "The original approval receipt does not match this stage.")
        saved = ContinuationOptions.model_validate(run["continuation"]["options"])
        options = scenario_continuation_options() if current.stage == "prepare_scenarios" else test_case_continuation_options(saved.template)
        retry_undispatched_stage(repo.client, project_id=project_id, run_id=run_id, actor_id=actor.sub, options=options)
        view = _read_run(doc, actor, run_id)
        if view is None:
            raise HTTPException(503, "The retry could not be confirmed. Check stage status before trying again.")
        return view
    except ContinuationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc
