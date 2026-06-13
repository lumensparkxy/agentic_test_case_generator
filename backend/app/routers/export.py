import io
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from ..agents.export_agent import export_to_csv, export_to_excel, export_to_jira, export_to_json
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, ExportTestCasesInput, JiraExportInput, JiraExportResponse
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.workflow_project_service import append_stage_snapshot, get_project, project_error_to_http

router = APIRouter()

REPORT_EVIDENCE_STAGES = (
    "requirements",
    "context",
    "use_cases",
    "impact_analysis",
    "test_cases",
    "execution",
)


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


def _export_audit_metadata(payload: ExportTestCasesInput) -> dict[str, Any]:
    review = payload.review or None
    override_reason = (payload.draft_override_reason or "").strip()
    metadata: dict[str, Any] = {
        "test_case_count": len(payload.test_cases),
        "approved": bool(payload.approved),
        "draft_override_requested": bool(payload.draft_override_requested),
    }
    if review:
        metadata.update(
            {
                "review_approved": bool(review.approved),
                "review_score": review.score,
                "review_threshold": review.threshold,
            }
        )
    if override_reason:
        metadata["draft_override_reason"] = override_reason[:500]
    return metadata


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_payload(item) for key, item in value.items()}
    return value


def _project_report_evidence(payload: ExportTestCasesInput, *, actor: AuthUser) -> dict[str, Any]:
    if not payload.project_id:
        return {
            "source_snapshot_ids": {},
            "execution_run_ids": [],
            "evidence_refs": [],
            "primary_source_snapshot_id": None,
        }
    project = get_project(payload.project_id, actor=actor)
    source_snapshot_ids = {stage: project.current_snapshots[stage].snapshot_id for stage in REPORT_EVIDENCE_STAGES if stage in project.current_snapshots}
    execution_run_ids = [run.run_id for run in project.execution_runs if run.run_id]
    evidence_refs = [
        {
            "role": "evidence",
            "stage": stage,
            "snapshot_id": snapshot_id,
            "metadata": {"source": "project_snapshot"},
        }
        for stage, snapshot_id in source_snapshot_ids.items()
    ]
    evidence_refs.extend(
        {
            "role": "evidence",
            "stage": "execution",
            "snapshot_id": run.snapshot_id,
            "item_ids": [run.run_id],
            "metadata": {
                "source": "execution_run",
                "target_environment": run.target_environment,
                "status": run.status,
            },
        }
        for run in project.execution_runs
        if run.run_id
    )
    primary_source_snapshot_id = source_snapshot_ids.get("execution") or source_snapshot_ids.get("test_cases")
    return {
        "source_snapshot_ids": source_snapshot_ids,
        "execution_run_ids": execution_run_ids,
        "evidence_refs": evidence_refs,
        "primary_source_snapshot_id": primary_source_snapshot_id,
    }


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
        evidence = _project_report_evidence(payload, actor=current_user)
        append_stage_snapshot(
            project_id=payload.project_id,
            stage="reports",
            payload={
                "source": "export",
                "format": export_format,
                "test_case_count": len(payload.test_cases),
                "approved": payload.approved,
                "review": _model_payload(payload.review),
                "draft_override_requested": payload.draft_override_requested,
                "draft_override_reason": payload.draft_override_reason,
                "result_metadata": result_metadata,
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
            approved=payload.approved,
            source_snapshot_id=evidence["primary_source_snapshot_id"],
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
    export_metadata = _export_audit_metadata(payload)
    workflow_run_id = start_workflow_run(
        operation="export.csv",
        actor=current_user,
        request_id=request_id,
        metadata=export_metadata,
    )
    try:
        csv_content = export_to_csv(payload.test_cases)
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
    export_metadata = _export_audit_metadata(payload)
    workflow_run_id = start_workflow_run(
        operation="export.excel",
        actor=current_user,
        request_id=request_id,
        metadata=export_metadata,
    )
    try:
        excel_bytes = export_to_excel(payload.test_cases)
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


@router.post("/export/json")
async def export_json_endpoint(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to JSON format."""
    request_id = _get_request_id(request)
    export_metadata = _export_audit_metadata(payload)
    workflow_run_id = start_workflow_run(
        operation="export.json",
        actor=current_user,
        request_id=request_id,
        metadata=export_metadata,
    )
    try:
        json_content = export_to_json(payload.test_cases)
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
