import io
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from ..services.test_case_quality import placeholder_reason
from ..services.export_evidence import build_export_evidence
from ..contracts.exports import ExportTestCasesDocument
from ..agents.export_agent import export_to_csv, export_to_excel, export_to_jira, export_to_json
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, ExportTestCasesInput, JiraExportInput, JiraExportResponse
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.workflow_project_service import append_stage_snapshot, get_project, project_error_to_http

router = APIRouter()


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


def _export_audit_metadata(payload: ExportTestCasesInput, *, actor: AuthUser) -> dict[str, Any]:
    project = None
    if payload.project_id:
        try:
            project = get_project(payload.project_id, actor=actor)
        except Exception as exc:
            raise project_error_to_http(exc) from exc
    return build_export_evidence(payload, project).model_dump(mode="json")


def _project_report_evidence(metadata: dict[str, Any]) -> dict[str, Any]:
    source_ids = metadata["source_snapshot_ids"]
    refs = [
        {"role": "evidence", "stage": stage, "snapshot_id": snapshot_id, "metadata": {"source": "project_snapshot"}}
        for stage, snapshot_id in source_ids.items()
    ]
    refs.extend(
        {"role": "evidence", "stage": "execution", "item_ids": [run["run_id"]], "metadata": {"source": "execution_run", "status": run["status"]}}
        for run in metadata["execution_runs"]
    )
    return {"source_snapshot_ids": source_ids, "execution_run_ids": metadata["execution_run_ids"], "evidence_refs": refs}


def _record_project_export_snapshot(
    *,
    payload: ExportTestCasesInput,
    export_format: str,
    current_user: AuthUser,
    request_id: str,
    workflow_run_id: str,
    source_event_id: str,
    result_metadata: dict[str, Any],
) -> None:
    if not payload.project_id:
        return
    try:
        evidence = _project_report_evidence(result_metadata)
        audit_metadata = {key: value for key, value in result_metadata.items() if key not in {"content_length", "byte_count"}}
        append_stage_snapshot(
            project_id=payload.project_id,
            stage="reports",
            payload={
                "source": "export",
                "format": export_format,
                "test_case_count": len(payload.test_cases),
                "approved": not result_metadata["is_draft"],
                "review": result_metadata["machine_review"],
                "audit_metadata": audit_metadata,
                "draft_override_requested": result_metadata["draft_override_used"],
                "draft_override_reason": result_metadata["draft_override_reason"],
                "result_metadata": {key: result_metadata[key] for key in ("content_length", "byte_count") if key in result_metadata},
                "evidence": {
                    "source_snapshot_ids": evidence["source_snapshot_ids"],
                    "execution_run_ids": evidence["execution_run_ids"],
                    "evidence_refs": evidence["evidence_refs"],
                },
            },
            operation=f"export.{export_format}",
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=source_event_id,
            approved=not result_metadata["is_draft"],
            source_snapshot_id=result_metadata["snapshot_id"],
            title=f"{export_format.upper()} export",
            metadata={
                "format": export_format,
                "test_case_count": len(payload.test_cases),
                "source_snapshot_ids": evidence["source_snapshot_ids"],
                "execution_run_ids": evidence["execution_run_ids"],
                "evidence_count": len(evidence["evidence_refs"]),
            },
            base_project_revision=payload.base_project_revision,
        )
    except Exception as project_exc:
        raise project_error_to_http(project_exc) from project_exc


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
        metadata=failure_metadata,
        error_message=error_message,
    )


@router.post("/export/jira", response_model=JiraExportResponse)
async def export_jira(
    request: Request,
    payload: JiraExportInput,
    current_user: AuthUser = Depends(get_current_user),
) -> JiraExportResponse:
    if any(placeholder_reason(case) for case in payload.test_cases):
        raise HTTPException(422, "Generate concrete tests before exporting unfinished generation work.")
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="export.jira",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        response = await run_in_threadpool(export_to_jira, payload)
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.jira",
            event_type="export.jira",
            billing_key="export.jira",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata={"status": response.status, "test_case_count": len(payload.test_cases)},
        )
        return response
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.jira",
            event_type="export.jira",
            billing_key="export.jira",
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise


@router.post("/export/csv")
async def export_csv(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to CSV format."""
    request_id = _get_request_id(request)
    export_metadata = _export_audit_metadata(payload, actor=current_user)
    workflow_run_id = start_workflow_run(
        operation="export.csv",
        actor=current_user,
        request_id=request_id,
        metadata=export_metadata,
    )
    try:
        csv_content = export_to_csv(payload.test_cases, audit_metadata=export_metadata)
        result_metadata = {**export_metadata, "content_length": len(csv_content)}
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.csv",
            event_type="export.csv",
            billing_key="export.csv",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata=result_metadata,
        )
        _record_project_export_snapshot(
            payload=payload,
            export_format="csv",
            current_user=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            result_metadata=result_metadata,
        )
        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=test_cases.csv"},
        )
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.csv",
            event_type="export.csv",
            billing_key="export.csv",
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise


@router.post("/export/excel")
async def export_excel_endpoint(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to Excel format."""
    request_id = _get_request_id(request)
    export_metadata = _export_audit_metadata(payload, actor=current_user)
    workflow_run_id = start_workflow_run(
        operation="export.excel",
        actor=current_user,
        request_id=request_id,
        metadata=export_metadata,
    )
    try:
        excel_bytes = export_to_excel(payload.test_cases, audit_metadata=export_metadata)
        result_metadata = {**export_metadata, "byte_count": len(excel_bytes)}
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.excel",
            event_type="export.excel",
            billing_key="export.excel",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata=result_metadata,
        )
        _record_project_export_snapshot(
            payload=payload,
            export_format="excel",
            current_user=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            result_metadata=result_metadata,
        )
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"},
        )
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.excel",
            event_type="export.excel",
            billing_key="export.excel",
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise


@router.post("/export/json", response_model=ExportTestCasesDocument)
async def export_json_endpoint(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to JSON format."""
    request_id = _get_request_id(request)
    export_metadata = _export_audit_metadata(payload, actor=current_user)
    workflow_run_id = start_workflow_run(
        operation="export.json",
        actor=current_user,
        request_id=request_id,
        metadata=export_metadata,
    )
    try:
        json_content = export_to_json(payload.test_cases, audit_metadata=export_metadata)
        result_metadata = {**export_metadata, "content_length": len(json_content)}
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.json",
            event_type="export.json",
            billing_key="export.json",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata=result_metadata,
        )
        _record_project_export_snapshot(
            payload=payload,
            export_format="json",
            current_user=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            result_metadata=result_metadata,
        )
        return StreamingResponse(
            io.StringIO(json_content),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=test_cases.json"},
        )
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.json",
            event_type="export.json",
            billing_key="export.json",
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise
