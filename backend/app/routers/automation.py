import sys
from typing import Any, Callable, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from ..agents.automation_agent import generate_playwright_pom
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
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.execution_service import preview_execution, run_execution
from ..services.workflow_project_service import append_stage_snapshot, project_error_to_http, record_execution_run

router = APIRouter()


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


def _run_project_payload(response: ExecutionRunResponse, *, target_environment: str, target_base_url: Optional[str]) -> dict[str, Any]:
    return {
        "target_environment": target_environment,
        "target_base_url": target_base_url,
        "run_id": response.run_id,
        "status": response.status,
        "summary": _model_payload(response.summary),
        "warnings": list(response.warnings or []),
        "results": [
            {
                "id": item.id,
                "source_test_case_id": item.source_test_case_id,
                "title": item.title,
                "status": item.status,
                "returncode": item.returncode,
                "issues": _model_payload(item.issues),
            }
            for item in response.results
        ],
    }


@router.post("/automation/playwright", response_model=AutomationResponse)
async def automation_playwright(
    request: Request,
    payload: AutomationInput,
    current_user: AuthUser = Depends(get_current_user),
) -> AutomationResponse:
    request_id = _get_request_id(request)
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
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata={"file_count": len(response.files), "test_case_count": len(payload.test_cases)},
        )
        return response
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


@router.post("/automation/execution/preview", response_model=ExecutionPreviewResponse)
async def automation_execution_preview(
    request: Request,
    payload: ExecutionPreviewInput,
    current_user: AuthUser = Depends(get_current_user),
) -> ExecutionPreviewResponse:
    request_id = _get_request_id(request)
    workflow_run_id = _resolve_main_callable("start_workflow_run", start_workflow_run)(
        operation="automation.execution.preview",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        target_base_url = str(payload.target_base_url) if payload.target_base_url else None
        target_environment = _target_environment(payload)
        response = await run_in_threadpool(
            _resolve_main_callable("preview_execution", preview_execution),
            payload.test_cases,
            target_base_url=target_base_url,
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="automation.execution.preview",
            event_type="automation.execution.previewed",
            billing_key="automation.execution.preview",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata={
                "executable_count": response.summary.executable,
                "manual_count": response.summary.manual,
                "unsupported_count": response.summary.unsupported,
                "invalid_count": response.summary.invalid,
            },
        )
        if payload.project_id:
            try:
                append_stage_snapshot(
                    project_id=payload.project_id,
                    stage="execution",
                    payload=_preview_project_payload(response, target_environment=target_environment, target_base_url=target_base_url),
                    operation="automation.execution.preview",
                    actor=current_user,
                    request_id=request_id,
                    workflow_run_id=workflow_run_id,
                    source_event_id=event_id,
                    approved=True,
                    title=f"{target_environment} execution preview",
                    metadata={
                        "target_environment": target_environment,
                        "executable_count": response.summary.executable,
                        "manual_count": response.summary.manual,
                        "unsupported_count": response.summary.unsupported,
                        "invalid_count": response.summary.invalid,
                    },
                    base_project_revision=payload.base_project_revision,
                )
            except Exception as project_exc:
                raise project_error_to_http(project_exc) from project_exc
        return response
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="automation.execution.preview",
            event_type="automation.execution.previewed",
            billing_key="automation.execution.preview",
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise


@router.post("/automation/execution/run", response_model=ExecutionRunResponse)
async def automation_execution_run(
    request: Request,
    payload: ExecutionRunInput,
    current_user: AuthUser = Depends(get_current_user),
) -> ExecutionRunResponse:
    request_id = _get_request_id(request)
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
        target_base_url = str(payload.target_base_url) if payload.target_base_url else None
        target_environment = _target_environment(payload)
        response = await run_in_threadpool(
            _resolve_main_callable("run_execution", run_execution),
            payload.test_cases,
            selected_test_case_ids=payload.selected_test_case_ids,
            target_base_url=target_base_url,
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="automation.execution.run",
            event_type="automation.execution.ran",
            billing_key="automation.execution.run",
            quantity=len(response.results),
            unit="test_case",
            result_metadata={
                "status": response.status,
                "run_id": response.run_id,
                "passed_count": response.summary.passed,
                "failed_count": response.summary.failed,
                "invalid_count": response.summary.invalid,
                "skipped_count": response.summary.skipped,
            },
        )
        if payload.project_id:
            try:
                execution_snapshot = append_stage_snapshot(
                    project_id=payload.project_id,
                    stage="execution",
                    payload=_run_project_payload(response, target_environment=target_environment, target_base_url=target_base_url),
                    operation="automation.execution.run",
                    actor=current_user,
                    request_id=request_id,
                    workflow_run_id=workflow_run_id,
                    source_event_id=event_id,
                    approved=response.status == "passed",
                    title=f"{target_environment} execution run",
                    metadata={
                        "target_environment": target_environment,
                        "run_id": response.run_id,
                        "status": response.status,
                        "passed_count": response.summary.passed,
                        "failed_count": response.summary.failed,
                        "invalid_count": response.summary.invalid,
                    },
                    base_project_revision=payload.base_project_revision,
                )
                record_execution_run(
                    project_id=payload.project_id,
                    actor=current_user,
                    request_id=request_id,
                    run_id=response.run_id,
                    target_environment=target_environment,
                    status_value=response.status,
                    summary=_model_payload(response.summary),
                    test_case_count=len(payload.test_cases),
                    snapshot_id=execution_snapshot.snapshot_id,
                    workflow_run_id=workflow_run_id,
                    source_event_id=event_id,
                    project_revision=execution_snapshot.project_revision,
                )
                append_stage_snapshot(
                    project_id=payload.project_id,
                    stage="reports",
                    payload={
                        "source": "execution",
                        "run_id": response.run_id,
                        "target_environment": target_environment,
                        "status": response.status,
                        "summary": _model_payload(response.summary),
                    },
                    operation="reports.execution_summary",
                    actor=current_user,
                    request_id=request_id,
                    workflow_run_id=workflow_run_id,
                    source_event_id=event_id,
                    approved=response.status == "passed",
                    source_snapshot_id=execution_snapshot.snapshot_id,
                    title=f"{target_environment} execution report",
                    metadata={"run_id": response.run_id, "target_environment": target_environment, "status": response.status},
                )
            except Exception as project_exc:
                raise project_error_to_http(project_exc) from project_exc
        return response
    except Exception as exc:
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
        raise
