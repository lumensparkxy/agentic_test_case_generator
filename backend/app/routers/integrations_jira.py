from typing import Any, Optional
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from ..adapters.jira import JiraAdapterError
from ..auth.jwt_auth import get_current_user
from ..models import (
    AuthUser,
    JiraConnectionDeleteResponse,
    JiraConnectionInput,
    JiraConnectionStatusResponse,
    JiraImportInput,
    JiraIssueSearchResponse,
    JiraProjectIssueTypesResponse,
    JiraProjectsResponse,
    JiraSyncApplyInput,
    JiraSyncApplyResponse,
    JiraSyncPreviewInput,
    JiraSyncPreviewResponse,
    RequirementsWorkflowResponse,
)
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.billing_service import enforce_billing_access, record_billing_consumption
from ..services.jira_connection_service import (
    delete_jira_connection,
    get_jira_connection_status,
    upsert_jira_connection,
)
from ..services.jira_requirements_service import (
    import_requirements_from_jira,
    list_jira_project_issue_types,
    list_jira_projects,
    persist_jira_requirement_mappings,
    search_jira_issues,
)
from ..services.jira_sync_service import apply_jira_requirement_sync, preview_jira_requirement_sync
from ..services.versioning_service import persist_requirement_versions

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


def _record_billing_consumption_safe(
    *,
    current_user: AuthUser,
    billing_context,
    source_event_id: str,
    request: Request,
    workflow_run_id: str,
    billing_key: str,
    quantity: int,
    unit: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    try:
        record_billing_consumption(
            current_user=current_user,
            billing_context=billing_context,
            source_event_id=source_event_id,
            request_id=_get_request_id(request),
            workflow_run_id=workflow_run_id,
            billing_key=billing_key,
            quantity=quantity,
            unit=unit,
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - defensive safety after a successful workflow
        logging.exception("Billing consumption recording failed after JIRA workflow success")


@router.get("/integrations/jira/connection", response_model=JiraConnectionStatusResponse)
async def jira_connection_status(
    current_user: AuthUser = Depends(get_current_user),
) -> JiraConnectionStatusResponse:
    return await run_in_threadpool(get_jira_connection_status, current_user=current_user)


@router.post("/integrations/jira/connection", response_model=JiraConnectionStatusResponse)
async def jira_connection_upsert(
    request: Request,
    payload: JiraConnectionInput,
    current_user: AuthUser = Depends(get_current_user),
) -> JiraConnectionStatusResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="integrations.jira.connection.upsert",
        actor=current_user,
        request_id=request_id,
        metadata={"base_url": str(payload.base_url), "email": payload.email},
    )
    try:
        response = JiraConnectionStatusResponse.model_validate(
            await run_in_threadpool(upsert_jira_connection, current_user=current_user, payload=payload)
        )
        complete_workflow_run(
            workflow_run_id,
            status="completed",
            metadata={
                "base_url": str(response.connection.base_url) if response.connection else None,
                "email": response.connection.email if response.connection else None,
            },
        )
        return response
    except JiraAdapterError as exc:
        complete_workflow_run(
            workflow_run_id,
            status="failed",
            metadata={"base_url": str(payload.base_url), "email": payload.email},
            error_message=str(exc),
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("JIRA connection upsert failed")
        complete_workflow_run(
            workflow_run_id,
            status="failed",
            metadata={"base_url": str(payload.base_url), "email": payload.email},
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to store JIRA connection") from exc


@router.delete("/integrations/jira/connection", response_model=JiraConnectionDeleteResponse)
async def jira_connection_delete(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
) -> JiraConnectionDeleteResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="integrations.jira.connection.delete",
        actor=current_user,
        request_id=request_id,
        metadata={},
    )
    try:
        await run_in_threadpool(delete_jira_connection, current_user=current_user)
        complete_workflow_run(workflow_run_id, status="completed", metadata={"deleted": True})
        return JiraConnectionDeleteResponse()
    except Exception as exc:
        logging.exception("JIRA connection delete failed")
        complete_workflow_run(
            workflow_run_id,
            status="failed",
            metadata={"deleted": False},
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to delete JIRA connection") from exc


@router.get("/integrations/jira/projects", response_model=JiraProjectsResponse)
async def jira_projects(
    query: Optional[str] = None,
    max_results: int = Query(50, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
) -> JiraProjectsResponse:
    try:
        return await run_in_threadpool(
            list_jira_projects,
            current_user=current_user,
            query=query,
            max_results=max_results,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JiraAdapterError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.get("/integrations/jira/projects/{project_key}/issue-types", response_model=JiraProjectIssueTypesResponse)
async def jira_project_issue_types(
    project_key: str,
    current_user: AuthUser = Depends(get_current_user),
) -> JiraProjectIssueTypesResponse:
    try:
        return await run_in_threadpool(
            list_jira_project_issue_types,
            current_user=current_user,
            project_key=project_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JiraAdapterError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.get("/integrations/jira/issues/search", response_model=JiraIssueSearchResponse)
async def jira_issue_search(
    project_key: str = Query(..., min_length=1),
    query: Optional[str] = None,
    issue_type: Optional[str] = Query(None),
    max_results: int = Query(20, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
) -> JiraIssueSearchResponse:
    try:
        return await run_in_threadpool(
            search_jira_issues,
            current_user=current_user,
            project_key=project_key,
            query=query,
            issue_type=issue_type,
            max_results=max_results,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JiraAdapterError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.post("/integrations/jira/import", response_model=RequirementsWorkflowResponse)
async def jira_import_requirements_endpoint(
    request: Request,
    payload: JiraImportInput,
    current_user: AuthUser = Depends(get_current_user),
) -> RequirementsWorkflowResponse:
    request_id = _get_request_id(request)
    billing_context = await run_in_threadpool(
        enforce_billing_access,
        current_user=current_user,
        billing_key="requirements.parse",
    )
    workflow_run_id = start_workflow_run(
        operation="requirements.import.jira",
        actor=current_user,
        request_id=request_id,
        metadata={
            "epic_key": payload.epic_key,
            "issue_keys": payload.issue_keys,
            "jql": payload.jql,
            "include_children": payload.include_children,
        },
    )
    try:
        workflow = await run_in_threadpool(
            import_requirements_from_jira,
            current_user=current_user,
            payload=payload,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
        )
        response = RequirementsWorkflowResponse(
            source_name=workflow.get("source_name") or "JIRA import",
            source_names=workflow.get("source_names") or [],
            raw_text=workflow.get("raw_text") or "",
            requirements=workflow.get("requirements") or [],
            approved=workflow.get("approved", False),
            review=workflow.get("review") or {},
            iteration_history=workflow.get("iteration_history") or [],
            coverage_metrics=workflow.get("coverage_metrics") or {},
            workflow_settings=workflow.get("workflow_settings") or {},
            workflow_diagnostics=workflow.get("workflow_diagnostics") or {},
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.jira",
            event_type="requirements.imported.jira",
            billing_key="requirements.parse",
            quantity=len(response.requirements),
            unit="requirement",
            result_metadata={
                "requirements_generated_count": len(response.requirements),
                "source_issue_count": int(workflow.get("issue_count") or 0),
                "source_issue_keys": workflow.get("source_issue_keys") or [],
                "approved": response.approved,
            },
        )
        _record_billing_consumption_safe(
            current_user=current_user,
            billing_context=billing_context,
            source_event_id=event_id,
            request=request,
            workflow_run_id=workflow_run_id,
            billing_key="requirements.parse",
            quantity=len(response.requirements),
            unit="requirement",
            metadata={
                "approved": response.approved,
                "source": "jira",
                "source_issue_count": int(workflow.get("issue_count") or 0),
            },
        )
        response.requirements = persist_requirement_versions(
            current_requirements=response.requirements,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="requirements.import.jira",
            source_name=response.source_name,
            source_names=response.source_names,
            raw_text=response.raw_text,
            approved=response.approved,
        )
        response.requirements = await run_in_threadpool(
            persist_jira_requirement_mappings,
            requirements=response.requirements,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
        )
        return response
    except LookupError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.jira",
            event_type="requirements.imported.jira",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "jira"},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.jira",
            event_type="requirements.imported.jira",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "jira"},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraAdapterError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.jira",
            event_type="requirements.imported.jira",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "jira", "status_code": exc.status_code},
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("JIRA requirements import failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.jira",
            event_type="requirements.imported.jira",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "jira"},
        )
        raise HTTPException(status_code=500, detail="JIRA requirements import failed") from exc


@router.post("/integrations/jira/sync/preview", response_model=JiraSyncPreviewResponse)
async def jira_sync_preview_endpoint(
    request: Request,
    payload: JiraSyncPreviewInput,
    current_user: AuthUser = Depends(get_current_user),
) -> JiraSyncPreviewResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="requirements.sync_preview.jira",
        actor=current_user,
        request_id=request_id,
        metadata={"requirement_count": len(payload.requirements)},
    )
    try:
        response = JiraSyncPreviewResponse.model_validate(
            await run_in_threadpool(preview_jira_requirement_sync, current_user=current_user, payload=payload)
        )
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.jira",
            event_type="requirements.sync_previewed.jira",
            billing_key="requirements.refine",
            quantity=0,
            unit="request",
            result_metadata={
                "ready_issue_count": response.ready_issue_count,
                "conflict_count": response.conflict_count,
                "skipped_requirement_count": len(response.skipped_requirement_ids),
            },
        )
        return response
    except LookupError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.jira",
            event_type="requirements.sync_previewed.jira",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JiraAdapterError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.jira",
            event_type="requirements.sync_previewed.jira",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("JIRA sync preview failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.jira",
            event_type="requirements.sync_previewed.jira",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=500, detail="JIRA sync preview failed") from exc


@router.post("/integrations/jira/sync", response_model=JiraSyncApplyResponse)
async def jira_sync_apply_endpoint(
    request: Request,
    payload: JiraSyncApplyInput,
    current_user: AuthUser = Depends(get_current_user),
) -> JiraSyncApplyResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="requirements.sync.jira",
        actor=current_user,
        request_id=request_id,
        metadata={
            "requirement_count": len(payload.requirements),
            "allow_conflicts": payload.allow_conflicts,
        },
    )
    try:
        response = JiraSyncApplyResponse.model_validate(
            await run_in_threadpool(apply_jira_requirement_sync, current_user=current_user, payload=payload)
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.jira",
            event_type="requirements.synced.jira",
            billing_key="requirements.refine",
            quantity=0,
            unit="request",
            result_metadata={
                "updated_issue_count": response.updated_issue_count,
                "skipped_issue_count": response.skipped_issue_count,
                "conflict_count": response.conflict_count,
                "warning_count": len(response.warnings),
            },
        )
        if response.updated_issue_count > 0:
            response.requirements = await run_in_threadpool(
                persist_jira_requirement_mappings,
                requirements=response.requirements,
                actor=current_user,
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                source_event_id=event_id,
            )
        return response
    except LookupError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.jira",
            event_type="requirements.synced.jira",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JiraAdapterError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.jira",
            event_type="requirements.synced.jira",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("JIRA sync apply failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.jira",
            event_type="requirements.synced.jira",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=500, detail="JIRA sync failed") from exc
