from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from ..agents.automation_agent import generate_playwright_pom
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, AutomationInput, AutomationResponse
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run

router = APIRouter()


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
    complete_workflow_run(workflow_run_id, status="completed", metadata=result_metadata)
    return record_usage_event(
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
    complete_workflow_run(
        workflow_run_id,
        status="failed",
        metadata=failure_metadata,
        error_message=error_message,
    )
    record_usage_event(
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


@router.post("/automation/playwright", response_model=AutomationResponse)
async def automation_playwright(
    request: Request,
    payload: AutomationInput,
    current_user: AuthUser = Depends(get_current_user),
) -> AutomationResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="automation.playwright.generate",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        response = await run_in_threadpool(generate_playwright_pom, payload)
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
