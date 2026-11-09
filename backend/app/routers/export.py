from typing import Any, Callable, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ..services.test_case_quality import placeholder_reason
from ..services.export_evidence import build_export_evidence
from ..contracts.exports import ExportTestCasesDocument
from ..agents.export_agent import export_to_csv, export_to_excel, export_to_jira, export_to_json
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, ExportTestCasesInput, JiraExportInput, JiraExportResponse
from ..observability.metrics import record_workflow_completed
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.firestore_repository import get_required_firestore_client
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


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


class _ExportPublicationUnconfirmed(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "export_publication_unconfirmed", "message": "The export could not be confirmed. Retry this request."})


def _load_export_project(project_id, actor):
    try:
        project = get_project(project_id, actor=actor)
    except Exception as exc:
        raise project_error_to_http(exc) from exc
    if project.status != "active":
        raise HTTPException(409, "Restore the archived project before exporting.")
    return project


def _find_export_receipt(project_id, actor, operation, key, *, require_current=True):
    try:
        return find_idempotent_stage_snapshot(project_id, "reports", operation, key, actor=actor, require_current=require_current)
    except (ProjectConflictError, ProjectNotFoundError, ProjectPermissionError) as exc:
        raise project_error_to_http(exc) from exc
    except Exception as exc:
        raise _ExportPublicationUnconfirmed() from exc


def _check_export_fingerprint(receipt, fingerprint):
    if receipt.metadata.get("export_request_fingerprint") != fingerprint:
        raise HTTPException(409, "This request already published a report for different inputs.")


def _replay_export_metadata(receipt, payload, actor, fingerprint):
    _check_export_fingerprint(receipt, fingerprint)
    project = _load_export_project(payload.project_id, actor)
    state = project.stage_state.get("reports")
    if not state or state.stale or state.current_snapshot_id != receipt.snapshot_id:
        raise HTTPException(409, "The saved export is no longer current. Reload before exporting.")
    current = payload.model_copy(update={"base_project_revision": project.current_revision})
    audit = build_export_evidence(current, project).model_dump(mode="json")
    audit["project_revision"] = payload.base_project_revision
    audit["exported_at"] = receipt.payload["audit_metadata"]["exported_at"]
    if digest(audit) != receipt.metadata.get("export_audit_fingerprint"):
        raise HTTPException(409, "The saved export evidence has changed. Reload before exporting.")
    return audit


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


def _publish_project_export(
    *,
    payload: ExportTestCasesInput,
    export_format: str,
    current_user: AuthUser,
    request_id: str,
    workflow_run_id: str,
    result_metadata: dict[str, Any],
    key: str,
    fingerprint: str,
):
    operation = f"export.{export_format}"
    try:
        client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
        writes = []
        event_id = record_usage_event(
            event_type=operation,
            billing_key=operation,
            quantity=len(payload.test_cases),
            unit="test_case",
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            status="completed",
            metadata={"operation": operation, **result_metadata},
            pending_writes=writes,
            firestore_client=client,
        )
        complete_workflow_run(workflow_run_id, status="completed", metadata=result_metadata, pending_writes=writes, firestore_client=client)
        evidence = _project_report_evidence(result_metadata)
        audit_metadata = {key: value for key, value in result_metadata.items() if key not in {"content_length", "byte_count"}}
        return append_stage_snapshot(
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
                "evidence": evidence,
            },
            operation=operation,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            approved=not result_metadata["is_draft"],
            source_snapshot_id=result_metadata["snapshot_id"],
            title=f"{export_format.upper()} export",
            metadata={
                "format": export_format,
                "test_case_count": len(payload.test_cases),
                "source_snapshot_ids": evidence["source_snapshot_ids"],
                "execution_run_ids": evidence["execution_run_ids"],
                "evidence_count": len(evidence["evidence_refs"]),
                "export_request_fingerprint": fingerprint,
                "export_audit_fingerprint": digest(audit_metadata),
            },
            base_project_revision=payload.base_project_revision,
            idempotency_key=key,
            pending_writes=writes,
        )
    except Exception as project_exc:
        # A reply may be lost after Firestore commits, even if the report later becomes stale.
        receipt = _find_export_receipt(payload.project_id, current_user, operation, key, require_current=False)
        if receipt:
            return receipt
        if isinstance(project_exc, HTTPException):
            raise
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
        metadata={**failure_metadata, "error_message": error_message},
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


def _file_export(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser,
    *,
    export_format: str,
    render: Callable[..., str | bytes],
    media_type: str,
    extension: str,
) -> Response:
    operation = f"export.{export_format}"
    request_id = _get_request_id(request)

    def response(content):
        return Response(content, media_type=media_type, headers={"Content-Disposition": f"attachment; filename=test_cases.{extension}"})

    project = None
    if payload.project_id:
        key = digest(["file-export", current_user.sub, request_id])
        fingerprint = digest(payload.model_dump(mode="json"))
        receipt = _find_export_receipt(payload.project_id, current_user, operation, key)
        if receipt:
            audit = _replay_export_metadata(receipt, payload, current_user, fingerprint)
            return response(render(payload.test_cases, audit_metadata=audit))
        project = _load_export_project(payload.project_id, current_user)
        if payload.base_project_revision != project.current_revision:
            # A duplicate may have committed between the receipt read and the project read.
            receipt = _find_export_receipt(payload.project_id, current_user, operation, key)
            if receipt:
                audit = _replay_export_metadata(receipt, payload, current_user, fingerprint)
                return response(render(payload.test_cases, audit_metadata=audit))

    audit = build_export_evidence(payload, project).model_dump(mode="json")
    workflow_run_id = start_workflow_run(operation=operation, actor=current_user, request_id=request_id, metadata=audit)
    try:
        content = render(payload.test_cases, audit_metadata=audit)
        result_metadata = {**audit, ("byte_count" if isinstance(content, bytes) else "content_length"): len(content)}
        if payload.project_id:
            snapshot = _publish_project_export(
                payload=payload,
                export_format=export_format,
                current_user=current_user,
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                result_metadata=result_metadata,
                key=key,
                fingerprint=fingerprint,
            )
            _check_export_fingerprint(snapshot, fingerprint)
        else:
            _log_success(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation=operation,
                event_type=operation,
                billing_key=operation,
                quantity=len(payload.test_cases),
                unit="test_case",
                result_metadata=result_metadata,
            )
            return response(content)
    except _ExportPublicationUnconfirmed:
        raise
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation=operation,
            event_type=operation,
            billing_key=operation,
            error_message=str(exc),
            metadata={"test_case_count": len(payload.test_cases)},
        )
        raise

    if snapshot.workflow_run_id == workflow_run_id:
        record_workflow_completed(workflow_run_id, "completed")
    else:
        complete_workflow_run(workflow_run_id, status="completed", metadata={"reused_snapshot_id": snapshot.snapshot_id})
        audit = _replay_export_metadata(snapshot, payload, current_user, fingerprint)
        content = render(payload.test_cases, audit_metadata=audit)
    return response(content)


@router.post("/export/csv")
def export_csv(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to CSV format."""
    return _file_export(request, payload, current_user, export_format="csv", render=export_to_csv, media_type="text/csv", extension="csv")


@router.post("/export/excel")
def export_excel_endpoint(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to Excel format."""
    return _file_export(
        request,
        payload,
        current_user,
        export_format="excel",
        render=export_to_excel,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension="xlsx",
    )


@router.post("/export/json", response_model=ExportTestCasesDocument)
def export_json_endpoint(
    request: Request,
    payload: ExportTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to JSON format."""
    return _file_export(request, payload, current_user, export_format="json", render=export_to_json, media_type="application/json", extension="json")
