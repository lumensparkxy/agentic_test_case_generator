from ..services.knowledge_service import project_for
from ..services.knowledge_feedback import finish_generation
from ..services.guidance_dependency import generation_guidance
import logging
import re
import sys
from typing import Any, Callable, Optional
from uuid import uuid4

from fastapi import HTTPException, APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from ..agents.automation_agent import generate_playwright_pom
from ..config import get_execution_settings
from ..services.execution_readiness import PREVIEW_SOURCE_BLOCKER_CODES, assess_execution, preview_has_verified_receipt, selected_candidates
from ..services.execution_prerequisites import execution_reference_scope
from ..services.execution_target import saved_execution_source_url
from ..services.execution_request_service import (
    claim_execution_request,
    execution_request_fingerprint,
    mark_execution_unconfirmed,
    read_execution_request,
    reject_execution_replay,
)
from ..services.execution_run_publication import ExecutionPublicationUnconfirmed, execution_run_counts, publish_execution_outcome
from ..auth.jwt_auth import get_current_user
from ..models import (
    AuthUser,
    AutomationInput,
    AutomationResponse,
    ExecutionPreviewInput,
    ExecutionPreviewResponse,
    ExecutionRunInput,
    ExecutionRunResponse,
)
from ..observability.metrics import record_workflow_completed
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.execution_service import preview_execution, run_execution
from ..services.firestore_repository import get_required_firestore_client
from ..services.project_draft_admission import draft_admission_client, draft_refresh_deferred
from ..services.requirement_reconciliation import digest
from ..services.workflow_project_service import (
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectPermissionError,
    append_stage_snapshot,
    find_idempotent_stage_snapshot,
    get_project,
    project_error_to_http,
)

router = APIRouter()
logger = logging.getLogger(__name__)
_PREVIEW_OPERATION = "automation.execution.preview"
_PREVIEW_EVENT = "automation.execution.previewed"


class _PreviewPublicationUnconfirmed(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "execution_preview_unconfirmed", "message": "The execution preview could not be confirmed. Retry this request."})


def _resolve_main_callable(name: str, fallback: Callable[..., Any]) -> Callable[..., Any]:
    main_module = sys.modules.get("app.main")
    return getattr(main_module, name, fallback) if main_module else fallback


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


def _log_success(
    *,
    current_user: AuthUser,
    request: Request,
    workflow_run_id: str,
    operation: str,
    event_type: str,
    billing_key: str,
    quantity: int,
    unit: str,
    result_metadata: dict[str, Any],
) -> str:
    request_id = _get_request_id(request)
    _resolve_main_callable("complete_workflow_run", complete_workflow_run)(
        workflow_run_id,
        status="completed",
        metadata=result_metadata,
    )
    return _resolve_main_callable("record_usage_event", record_usage_event)(
        event_type=event_type,
        billing_key=billing_key,
        quantity=quantity,
        unit=unit,
        actor=current_user,
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        status="completed",
        metadata={"operation": operation, **result_metadata},
    )


def _log_failure(
    *,
    current_user: AuthUser,
    request: Request,
    workflow_run_id: str,
    operation: str,
    event_type: str,
    billing_key: str,
    error_message: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    request_id = _get_request_id(request)
    failure_metadata = {"operation": operation, **(metadata or {})}
    _resolve_main_callable("complete_workflow_run", complete_workflow_run)(
        workflow_run_id,
        status="failed",
        metadata=failure_metadata,
        error_message=error_message,
    )
    _resolve_main_callable("record_usage_event", record_usage_event)(
        event_type=event_type,
        billing_key=billing_key,
        quantity=0,
        unit="request",
        actor=current_user,
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        status="failed",
        metadata={**failure_metadata, "error_message": error_message},
    )


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_payload(item) for key, item in value.items()}
    return value


def _target_environment(payload: ExecutionPreviewInput) -> str:
    configured = str(payload.target_environment or "").strip()
    if configured:
        return configured
    if payload.target_base_url:
        return str(payload.target_base_url.host or "default")
    return "default"


def _preview_project_payload(response: ExecutionPreviewResponse, *, target_environment: str, target_base_url: Optional[str]) -> dict[str, Any]:
    return {
        "target_environment": target_environment,
        "target_base_url": target_base_url,
        "summary": _model_payload(response.summary),
        "warnings": list(response.warnings or []),
        "readiness": _model_payload(response.readiness),
        "candidate_counts": {
            "executable": len(response.executable),
            "manual": len(response.manual),
            "unsupported": len(response.unsupported),
            "invalid": len(response.invalid),
        },
        "candidates": {
            "executable": [
                {"id": candidate.id, "source_test_case_id": candidate.source_test_case_id, "title": candidate.title, "status": candidate.status}
                for candidate in response.executable
            ],
            "manual": [
                {"id": candidate.id, "source_test_case_id": candidate.source_test_case_id, "title": candidate.title, "status": candidate.status}
                for candidate in response.manual
            ],
            "unsupported": [
                {"id": candidate.id, "source_test_case_id": candidate.source_test_case_id, "title": candidate.title, "status": candidate.status}
                for candidate in response.unsupported
            ],
            "invalid": [
                {"id": candidate.id, "source_test_case_id": candidate.source_test_case_id, "title": candidate.title, "status": candidate.status}
                for candidate in response.invalid
            ],
        },
    }


@router.post("/automation/playwright", response_model=AutomationResponse, dependencies=[Depends(generation_guidance("automation"))])
async def automation_playwright(
    request: Request,
    payload: AutomationInput,
    current_user: AuthUser = Depends(get_current_user),
) -> AutomationResponse:
    request_id = _get_request_id(request)
    if payload.project_id:
        draft_client = await run_in_threadpool(draft_admission_client)
        project = await run_in_threadpool(project_for, payload.project_id, current_user)
        if draft_client is not None:
            raise draft_refresh_deferred()
    workflow_run_id = _resolve_main_callable("start_workflow_run", start_workflow_run)(
        operation="automation.playwright.generate",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        response = await run_in_threadpool(_resolve_main_callable("generate_playwright_pom", generate_playwright_pom), payload)
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="automation.playwright.generate",
            event_type="automation.playwright.generated",
            billing_key="automation.playwright.generate",
            quantity=int(response.diagnostics.get("generated_case_count", 0)),
            unit="test_case",
            result_metadata={
                "file_count": len(response.files),
                "test_case_count": len(payload.test_cases),
                "generated_case_count": response.diagnostics.get("generated_case_count", 0),
                "status": response.status,
            },
        )
        if payload.project_id:
            try:
                await run_in_threadpool(
                    append_stage_snapshot,
                    project_id=payload.project_id,
                    stage="reports",
                    payload={
                        "source": "automation_generation",
                        "artifact_kind": "generated_code",
                        "execution_status": "not_executed",
                        "files": response.files,
                        "notes": response.notes,
                        "diagnostics": response.diagnostics,
                        "case_diagnostics": _model_payload(response.case_diagnostics),
                        "automation_input": payload.model_dump(mode="json"),
                    },
                    operation="automation.playwright.generate",
                    actor=current_user,
                    request_id=request_id,
                    approved=False,
                    title="Generated automation artifacts (not executed)",
                    base_project_revision=project.current_revision,
                    metadata={"file_count": len(response.files)},
                )
            except Exception as project_exc:
                raise project_error_to_http(project_exc) from project_exc
        return finish_generation(response, current_user, payload.project_id, "automation", None, request_id)
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="automation.playwright.generate",
            event_type="automation.playwright.generated",
            billing_key="automation.playwright.generate",
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise


def _execution_project(payload: ExecutionPreviewInput, actor: AuthUser):
    if not payload.project_id:
        return None
    try:
        return get_project(payload.project_id, actor=actor)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


def _call_with_execution_references(function, payload, actor, *, project=None, **kwargs):
    if source_url := saved_execution_source_url(project):
        kwargs["source_app_url"] = source_url
    with execution_reference_scope(actor.sub, payload.project_id):
        return function(payload.test_cases, **kwargs)


async def _execution_with_references(function, payload, actor, *, project=None, **kwargs):
    return await run_in_threadpool(_call_with_execution_references, function, payload, actor, project=project, **kwargs)


def _require_execution_ready(readiness) -> None:
    if not readiness.run_allowed:
        raise HTTPException(status_code=409, detail={"message": "Execution prerequisites are not satisfied.", "readiness": readiness.model_dump(mode="json")})


def _execution_preview_rejected() -> HTTPException:
    return HTTPException(
        409, {"code": "execution_preview_rejected", "message": "The saved execution preview changed. Preview the current target and suite again."}
    )


def _saved_preview_for_run(payload, project, readiness):
    preview = project.current_snapshots.get("execution")
    state = project.stage_state.get("execution")
    if (
        not preview
        or not state
        or state.stale
        or state.current_snapshot_id != preview.snapshot_id
        or preview.operation != _PREVIEW_OPERATION
        or preview.request_id != payload.preview_request_id
        or preview.project_revision != readiness.project_revision
        or preview.source_snapshot_id != readiness.source_snapshot_id
        or preview.payload.get("target_base_url") != readiness.target_base_url
        or preview.payload.get("target_environment") != _target_environment(payload)
        or not preview_has_verified_receipt(preview.metadata)
    ):
        raise _execution_preview_rejected()
    return preview


def _require_verified_preview_inputs(readiness):
    conflicts = [blocker for blocker in readiness.blockers if blocker.code in PREVIEW_SOURCE_BLOCKER_CODES]
    if conflicts:
        raise HTTPException(409, {"message": conflicts[0].message, "readiness": readiness.model_dump(mode="json")})


def _find_preview_receipt(project_id, actor, key, *, require_current=True):
    try:
        return find_idempotent_stage_snapshot(project_id, "execution", _PREVIEW_OPERATION, key, actor=actor, require_current=require_current)
    except (ProjectConflictError, ProjectNotFoundError, ProjectPermissionError) as exc:
        raise project_error_to_http(exc) from exc
    except Exception as exc:
        raise _PreviewPublicationUnconfirmed() from exc


def _check_preview_fingerprint(receipt, fingerprint):
    if receipt.metadata.get("preview_request_fingerprint") != fingerprint:
        raise HTTPException(409, "This request already saved an execution preview for different inputs.")


def _compile_execution_preview(payload, actor, project, settings, target_base_url):
    response = _call_with_execution_references(
        _resolve_main_callable("preview_execution", preview_execution),
        payload,
        actor,
        project=project,
        target_base_url=target_base_url,
        settings=settings,
    )
    response.readiness = assess_execution(payload, project=project, settings=settings, preview=response)
    return response


def _replay_execution_preview(receipt, payload, actor, fingerprint, settings):
    _check_preview_fingerprint(receipt, fingerprint)
    project = _execution_project(payload, actor)
    state = project.stage_state.get("execution")
    if project.current_revision != receipt.project_revision or not state or state.stale or state.current_snapshot_id != receipt.snapshot_id:
        raise HTTPException(409, "The saved execution preview is no longer current. Preview again from the current project.")
    current = payload.model_copy(update={"base_project_revision": project.current_revision})
    readiness = assess_execution(current, project=project, settings=settings)
    _require_verified_preview_inputs(readiness)
    response = _compile_execution_preview(current, actor, project, settings, readiness.target_base_url)
    if digest(response.model_dump(mode="json")) != receipt.metadata.get("preview_response_fingerprint"):
        raise HTTPException(409, "Execution readiness has changed. Preview the saved suite again.")
    return response


def _publish_execution_preview(payload, response, actor, request_id, run_id, key, fingerprint, counts):
    readiness = response.readiness
    target_environment = _target_environment(payload)
    target_base_url = readiness.target_base_url
    try:
        client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
        writes = []
        event_id = _resolve_main_callable("record_usage_event", record_usage_event)(
            event_type=_PREVIEW_EVENT,
            billing_key=_PREVIEW_OPERATION,
            quantity=len(payload.test_cases),
            unit="test_case",
            actor=actor,
            request_id=request_id,
            workflow_run_id=run_id,
            status="completed",
            metadata={"operation": _PREVIEW_OPERATION, **counts},
            pending_writes=writes,
            firestore_client=client,
        )
        _resolve_main_callable("complete_workflow_run", complete_workflow_run)(
            run_id, status="completed", metadata=counts, pending_writes=writes, firestore_client=client
        )
        return append_stage_snapshot(
            project_id=payload.project_id,
            stage="execution",
            payload={
                **_preview_project_payload(response, target_environment=target_environment, target_base_url=target_base_url),
                "reviewed_full_suite": sum(counts.values()) == sum(case.status != "Deprecated" for case in payload.test_cases),
            },
            operation=_PREVIEW_OPERATION,
            actor=actor,
            request_id=request_id,
            workflow_run_id=run_id,
            source_event_id=event_id,
            approved=True,
            source_snapshot_id=readiness.source_snapshot_id,
            title=f"{target_environment} execution preview",
            metadata={
                "target_environment": target_environment,
                "target_base_url": target_base_url,
                "source_snapshot_id": readiness.source_snapshot_id,
                "preview_request_fingerprint": fingerprint,
                "preview_response_fingerprint": digest(response.model_dump(mode="json")),
                **counts,
            },
            base_project_revision=payload.base_project_revision,
            idempotency_key=key,
            pending_writes=writes,
        )
    except Exception as exc:
        # A lost commit reply may refer to an already superseded snapshot.
        receipt = _find_preview_receipt(payload.project_id, actor, key, require_current=False)
        if receipt:
            return receipt
        if isinstance(exc, HTTPException):
            raise
        raise project_error_to_http(exc) from exc


@router.post("/automation/execution/preview", response_model=ExecutionPreviewResponse)
def automation_execution_preview(
    request: Request,
    payload: ExecutionPreviewInput,
    current_user: AuthUser = Depends(get_current_user),
) -> ExecutionPreviewResponse:
    request_id = _get_request_id(request)
    settings = get_execution_settings()
    project = None
    if payload.project_id:
        key = digest(["execution-preview", current_user.sub, request_id])
        fingerprint = digest(payload.model_dump(mode="json"))
        receipt = _find_preview_receipt(payload.project_id, current_user, key)
        if receipt:
            return _replay_execution_preview(receipt, payload, current_user, fingerprint, settings)
        project = _execution_project(payload, current_user)
        if payload.base_project_revision != project.current_revision:
            receipt = _find_preview_receipt(payload.project_id, current_user, key)
            if receipt:
                return _replay_execution_preview(receipt, payload, current_user, fingerprint, settings)

    readiness = assess_execution(payload, project=project, settings=settings)
    if payload.project_id:
        _require_verified_preview_inputs(readiness)
    workflow_run_id = _resolve_main_callable("start_workflow_run", start_workflow_run)(
        operation=_PREVIEW_OPERATION,
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        response = _compile_execution_preview(payload, current_user, project, settings, readiness.target_base_url)
        counts = {f"{bucket}_count": count for bucket, count in response.summary.model_dump().items()}
        if payload.project_id:
            response.readiness.project_revision = project.current_revision + 1
            snapshot = _publish_execution_preview(payload, response, current_user, request_id, workflow_run_id, key, fingerprint, counts)
            _check_preview_fingerprint(snapshot, fingerprint)
        else:
            _log_success(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation=_PREVIEW_OPERATION,
                event_type=_PREVIEW_EVENT,
                billing_key=_PREVIEW_OPERATION,
                quantity=len(payload.test_cases),
                unit="test_case",
                result_metadata=counts,
            )
            return response
    except _PreviewPublicationUnconfirmed:
        raise
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation=_PREVIEW_OPERATION,
            event_type=_PREVIEW_EVENT,
            billing_key=_PREVIEW_OPERATION,
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise

    if snapshot.workflow_run_id == workflow_run_id:
        record_workflow_completed(workflow_run_id, "completed")
        return response
    _resolve_main_callable("complete_workflow_run", complete_workflow_run)(
        workflow_run_id, status="completed", metadata={"reused_snapshot_id": snapshot.snapshot_id}
    )
    return _replay_execution_preview(snapshot, payload, current_user, fingerprint, settings)


@router.post("/automation/execution/run", response_model=ExecutionRunResponse)
async def automation_execution_run(
    request: Request,
    payload: ExecutionRunInput,
    current_user: AuthUser = Depends(get_current_user),
) -> ExecutionRunResponse:
    request_id = _get_request_id(request)
    tracked = bool(payload.project_id and request.headers.get("X-Request-ID"))
    if payload.preview_request_id and not tracked:
        raise HTTPException(422, "A project and execution request identity are required to run from a saved preview.")
    fingerprint = execution_request_fingerprint(payload) if tracked else ""
    if tracked:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,159}", request_id):
            raise HTTPException(422, "Use a URL-safe execution request identity of up to 160 characters.")
        try:
            saved = await run_in_threadpool(read_execution_request, payload.project_id, request_id, actor=current_user)
        except Exception as exc:
            raise project_error_to_http(exc) from exc
        reject_execution_replay(saved, fingerprint)

    request_ref = None
    workflow_run_id = _resolve_main_callable("start_workflow_run", start_workflow_run)(
        operation="automation.execution.run",
        actor=current_user,
        request_id=request_id,
        metadata={
            "test_case_count": len(payload.test_cases),
            "selected_test_case_count": len(payload.selected_test_case_ids),
        },
    )
    try:
        project = await run_in_threadpool(_execution_project, payload, current_user)
        settings = get_execution_settings()
        readiness = assess_execution(payload, project=project, settings=settings)
        _require_execution_ready(readiness)
        saved_preview = _saved_preview_for_run(payload, project, readiness) if payload.preview_request_id else None
        target_base_url = readiness.target_base_url
        preview = await _execution_with_references(
            _resolve_main_callable("preview_execution", preview_execution),
            payload,
            current_user,
            project=project,
            target_base_url=target_base_url,
            settings=settings,
        )
        readiness = assess_execution(payload, project=project, settings=settings, preview=preview)
        if saved_preview:
            preview.readiness = readiness
            if digest(preview.model_dump(mode="json")) != saved_preview.metadata["preview_response_fingerprint"]:
                raise _execution_preview_rejected()
        _require_execution_ready(readiness)
        try:
            selected_ids = selected_candidates(preview, payload.selected_test_case_ids)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "execution_selection_rejected", "message": str(exc)}) from exc
        target_environment = _target_environment(payload)
        if tracked:
            try:
                request_ref = await run_in_threadpool(
                    claim_execution_request,
                    project_id=payload.project_id,
                    request_id=request_id,
                    actor=current_user,
                    revision=readiness.project_revision,
                    source_snapshot_id=readiness.source_snapshot_id,
                    target_environment=target_environment,
                    fingerprint=fingerprint,
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise project_error_to_http(exc) from exc
        response = await _execution_with_references(
            _resolve_main_callable("run_execution", run_execution),
            payload,
            current_user,
            project=project,
            selected_test_case_ids=selected_ids,
            target_base_url=target_base_url,
            settings=settings,
            verified_preview=preview if saved_preview else None,
        )
        response.preview.readiness = readiness
        if payload.project_id:
            try:
                run = await run_in_threadpool(
                    publish_execution_outcome,
                    response,
                    actor=current_user,
                    request_id=request_id,
                    request_ref=request_ref,
                    workflow_run_id=workflow_run_id,
                    target_environment=target_environment,
                    selected_ids=selected_ids,
                    test_case_count=len(payload.test_cases),
                )
            except HTTPException, ExecutionPublicationUnconfirmed:
                raise
            except Exception as exc:
                raise project_error_to_http(exc) from exc
            response.run_record_id = run.run_record_id
            response.historical = run.historical
        else:
            _log_success(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="automation.execution.run",
                event_type="automation.execution.ran",
                billing_key="automation.execution.run",
                quantity=len(response.results),
                unit="test_case",
                result_metadata=execution_run_counts(response),
            )
        return response
    except Exception as exc:
        if request_ref is not None:
            try:
                await run_in_threadpool(mark_execution_unconfirmed, request_ref)
            except Exception:
                logger.warning("Could not update interrupted execution receipt", exc_info=True)
        if not isinstance(exc, ExecutionPublicationUnconfirmed):
            _log_failure(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="automation.execution.run",
                event_type="automation.execution.ran",
                billing_key="automation.execution.run",
                error_message=str(exc),
                metadata={
                    "test_case_count": len(payload.test_cases),
                    "selected_test_case_count": len(payload.selected_test_case_ids),
                },
            )
        if request_ref is not None or isinstance(exc, ExecutionPublicationUnconfirmed):
            raise HTTPException(
                503,
                detail={"code": "execution_request_unconfirmed", "message": "The browser request was admitted. Check its saved status."},
            ) from exc
        raise
