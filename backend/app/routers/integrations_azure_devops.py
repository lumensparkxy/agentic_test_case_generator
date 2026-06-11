from typing import Any, Optional
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from ..adapters.azure_devops import AzureDevOpsAdapterError
from ..auth.jwt_auth import get_current_user
from ..models import (
    AuthUser,
    AzureDevOpsConnectionDeleteResponse,
    AzureDevOpsConnectionInput,
    AzureDevOpsConnectionStatusResponse,
    AzureDevOpsImportInput,
    AzureDevOpsProjectWorkItemTypesResponse,
    AzureDevOpsProjectsResponse,
    AzureDevOpsSyncApplyInput,
    AzureDevOpsSyncApplyResponse,
    AzureDevOpsSyncPreviewInput,
    AzureDevOpsSyncPreviewResponse,
    AzureDevOpsWorkItemSearchResponse,
    RequirementsWorkflowResponse,
)
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.azure_devops_connection_service import (
    delete_azure_devops_connection,
    get_azure_devops_connection_status,
    upsert_azure_devops_connection,
)
from ..services.azure_devops_requirements_service import (
    import_requirements_from_azure_devops,
    list_azure_devops_project_work_item_types,
    list_azure_devops_projects,
    persist_azure_devops_requirement_mappings,
    search_azure_devops_work_items,
)
from ..services.azure_devops_sync_service import apply_azure_devops_requirement_sync, preview_azure_devops_requirement_sync
from ..services.billing_service import enforce_billing_access, record_billing_consumption
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
        logging.exception("Billing consumption recording failed after Azure DevOps workflow success")


@router.get("/integrations/azure-devops/connection", response_model=AzureDevOpsConnectionStatusResponse)
async def azure_devops_connection_status(
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsConnectionStatusResponse:
    return await run_in_threadpool(get_azure_devops_connection_status, current_user=current_user)


@router.post("/integrations/azure-devops/connection", response_model=AzureDevOpsConnectionStatusResponse)
async def azure_devops_connection_upsert(
    request: Request,
    payload: AzureDevOpsConnectionInput,
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsConnectionStatusResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="integrations.azure_devops.connection.upsert",
        actor=current_user,
        request_id=request_id,
        metadata={"organization_url": str(payload.organization_url), "account_email": payload.account_email},
    )
    try:
        response = AzureDevOpsConnectionStatusResponse.model_validate(
            await run_in_threadpool(upsert_azure_devops_connection, current_user=current_user, payload=payload)
        )
        complete_workflow_run(
            workflow_run_id,
            status="completed",
            metadata={
                "organization_url": str(response.connection.organization_url) if response.connection else None,
                "organization": response.connection.organization if response.connection else None,
                "default_project": response.connection.default_project if response.connection else None,
                "account_email": response.connection.account_email if response.connection else None,
            },
        )
        return response
    except AzureDevOpsAdapterError as exc:
        complete_workflow_run(
            workflow_run_id,
            status="failed",
            metadata={"organization_url": str(payload.organization_url), "account_email": payload.account_email},
            error_message=str(exc),
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Azure DevOps connection upsert failed")
        complete_workflow_run(
            workflow_run_id,
            status="failed",
            metadata={"organization_url": str(payload.organization_url), "account_email": payload.account_email},
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to store Azure DevOps connection") from exc


@router.delete("/integrations/azure-devops/connection", response_model=AzureDevOpsConnectionDeleteResponse)
async def azure_devops_connection_delete(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsConnectionDeleteResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="integrations.azure_devops.connection.delete",
        actor=current_user,
        request_id=request_id,
        metadata={},
    )
    try:
        await run_in_threadpool(delete_azure_devops_connection, current_user=current_user)
        complete_workflow_run(workflow_run_id, status="completed", metadata={"deleted": True})
        return AzureDevOpsConnectionDeleteResponse()
    except Exception as exc:
        logging.exception("Azure DevOps connection delete failed")
        complete_workflow_run(
            workflow_run_id,
            status="failed",
            metadata={"deleted": False},
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to delete Azure DevOps connection") from exc


@router.get("/integrations/azure-devops/projects", response_model=AzureDevOpsProjectsResponse)
async def azure_devops_projects(
    query: Optional[str] = None,
    max_results: int = Query(50, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsProjectsResponse:
    try:
        return await run_in_threadpool(
            list_azure_devops_projects,
            current_user=current_user,
            query=query,
            max_results=max_results,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AzureDevOpsAdapterError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.get("/integrations/azure-devops/projects/{project}/work-item-types", response_model=AzureDevOpsProjectWorkItemTypesResponse)
async def azure_devops_project_work_item_types(
    project: str,
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsProjectWorkItemTypesResponse:
    try:
        return await run_in_threadpool(
            list_azure_devops_project_work_item_types,
            current_user=current_user,
            project=project,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AzureDevOpsAdapterError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.get("/integrations/azure-devops/work-items/search", response_model=AzureDevOpsWorkItemSearchResponse)
async def azure_devops_work_item_search(
    project: str = Query(..., min_length=1),
    query: Optional[str] = None,
    work_item_type: Optional[str] = Query(None),
    max_results: int = Query(20, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsWorkItemSearchResponse:
    try:
        return await run_in_threadpool(
            search_azure_devops_work_items,
            current_user=current_user,
            project=project,
            query=query,
            work_item_type=work_item_type,
            max_results=max_results,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AzureDevOpsAdapterError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.post("/integrations/azure-devops/import", response_model=RequirementsWorkflowResponse)
async def azure_devops_import_requirements_endpoint(
    request: Request,
    payload: AzureDevOpsImportInput,
    current_user: AuthUser = Depends(get_current_user),
) -> RequirementsWorkflowResponse:
    request_id = _get_request_id(request)
    billing_context = await run_in_threadpool(
        enforce_billing_access,
        current_user=current_user,
        billing_key="requirements.parse",
    )
    workflow_run_id = start_workflow_run(
        operation="requirements.import.azure_devops",
        actor=current_user,
        request_id=request_id,
        metadata={
            "project": payload.project,
            "work_item_id": payload.work_item_id,
            "work_item_ids": payload.work_item_ids,
            "has_wiql": bool((payload.wiql or "").strip()),
            "include_children": payload.include_children,
        },
    )
    try:
        workflow = await run_in_threadpool(
            import_requirements_from_azure_devops,
            current_user=current_user,
            payload=payload,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
        )
        response = RequirementsWorkflowResponse(
            source_name=workflow.get("source_name") or "Azure DevOps import",
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
            operation="requirements.import.azure_devops",
            event_type="requirements.imported.azure_devops",
            billing_key="requirements.parse",
            quantity=len(response.requirements),
            unit="requirement",
            result_metadata={
                "requirements_generated_count": len(response.requirements),
                "source_work_item_count": int(workflow.get("work_item_count") or 0),
                "source_work_item_ids": workflow.get("source_work_item_ids") or [],
                "source_project": workflow.get("source_project"),
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
                "source": "azure_devops",
                "source_work_item_count": int(workflow.get("work_item_count") or 0),
            },
        )
        response.requirements = persist_requirement_versions(
            current_requirements=response.requirements,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="requirements.import.azure_devops",
            source_name=response.source_name,
            source_names=response.source_names,
            raw_text=response.raw_text,
            approved=response.approved,
        )
        response.requirements = await run_in_threadpool(
            persist_azure_devops_requirement_mappings,
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
            operation="requirements.import.azure_devops",
            event_type="requirements.imported.azure_devops",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "azure_devops"},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.azure_devops",
            event_type="requirements.imported.azure_devops",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "azure_devops"},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AzureDevOpsAdapterError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.azure_devops",
            event_type="requirements.imported.azure_devops",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "azure_devops", "status_code": exc.status_code},
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Azure DevOps requirements import failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.import.azure_devops",
            event_type="requirements.imported.azure_devops",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"source": "azure_devops"},
        )
        raise HTTPException(status_code=500, detail="Azure DevOps requirements import failed") from exc


@router.post("/integrations/azure-devops/sync/preview", response_model=AzureDevOpsSyncPreviewResponse)
async def azure_devops_sync_preview_endpoint(
    request: Request,
    payload: AzureDevOpsSyncPreviewInput,
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsSyncPreviewResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="requirements.sync_preview.azure_devops",
        actor=current_user,
        request_id=request_id,
        metadata={"requirement_count": len(payload.requirements)},
    )
    try:
        response = AzureDevOpsSyncPreviewResponse.model_validate(
            await run_in_threadpool(preview_azure_devops_requirement_sync, current_user=current_user, payload=payload)
        )
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.azure_devops",
            event_type="requirements.sync_previewed.azure_devops",
            billing_key="requirements.refine",
            quantity=0,
            unit="request",
            result_metadata={
                "ready_work_item_count": response.ready_work_item_count,
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
            operation="requirements.sync_preview.azure_devops",
            event_type="requirements.sync_previewed.azure_devops",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AzureDevOpsAdapterError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.azure_devops",
            event_type="requirements.sync_previewed.azure_devops",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Azure DevOps sync preview failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync_preview.azure_devops",
            event_type="requirements.sync_previewed.azure_devops",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=500, detail="Azure DevOps sync preview failed") from exc


@router.post("/integrations/azure-devops/sync", response_model=AzureDevOpsSyncApplyResponse)
async def azure_devops_sync_apply_endpoint(
    request: Request,
    payload: AzureDevOpsSyncApplyInput,
    current_user: AuthUser = Depends(get_current_user),
) -> AzureDevOpsSyncApplyResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="requirements.sync.azure_devops",
        actor=current_user,
        request_id=request_id,
        metadata={
            "requirement_count": len(payload.requirements),
            "allow_conflicts": payload.allow_conflicts,
        },
    )
    try:
        response = AzureDevOpsSyncApplyResponse.model_validate(
            await run_in_threadpool(apply_azure_devops_requirement_sync, current_user=current_user, payload=payload)
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.azure_devops",
            event_type="requirements.synced.azure_devops",
            billing_key="requirements.refine",
            quantity=0,
            unit="request",
            result_metadata={
                "updated_work_item_count": response.updated_work_item_count,
                "skipped_work_item_count": response.skipped_work_item_count,
                "conflict_count": response.conflict_count,
                "warning_count": len(response.warnings),
            },
        )
        if response.updated_work_item_count > 0:
            response.requirements = await run_in_threadpool(
                persist_azure_devops_requirement_mappings,
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
            operation="requirements.sync.azure_devops",
            event_type="requirements.synced.azure_devops",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AzureDevOpsAdapterError as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.azure_devops",
            event_type="requirements.synced.azure_devops",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Azure DevOps sync apply failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.sync.azure_devops",
            event_type="requirements.synced.azure_devops",
            billing_key="requirements.refine",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise HTTPException(status_code=500, detail="Azure DevOps sync failed") from exc
