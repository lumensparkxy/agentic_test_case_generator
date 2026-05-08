from io import BytesIO
from typing import Any, List, Optional
import json
import logging
from uuid import uuid4

from docx import Document
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from ..agents.requirements_agent import extract_requirements, refine_requirements
from ..auth.jwt_auth import get_current_user
from ..config import get_settings
from ..models import AuthUser, EnrichInput, EnrichResponse, RequirementsWorkflowResponse, WorkflowSettings
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.billing_service import enforce_billing_access, record_billing_consumption
from ..services.context_grounding import build_grounded_context
from ..services.versioning_service import persist_requirement_versions
from ..utils.excel_parser import parse_excel_to_text

router = APIRouter()

MAX_UPLOAD_SIZE_BYTES = 16 * 1024 * 1024


def _build_grounded_context_from_enrich_input(payload: EnrichInput):
    return build_grounded_context(payload)


def _parse_workflow_settings_form(workflow_settings: Optional[str]) -> Optional[WorkflowSettings]:
    if not workflow_settings:
        return None

    try:
        payload = json.loads(workflow_settings)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="workflow_settings must be valid JSON") from exc

    try:
        return WorkflowSettings(**payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="workflow_settings payload is invalid") from exc


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


def _requirement_snapshot(requirements: List[Any]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for index, item in enumerate(requirements or []):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        req_id = str((item or {}).get("id") or f"REQ-{index + 1:03d}")
        text = str((item or {}).get("text") or "").strip()
        snapshot[req_id] = text
    return snapshot


def _count_snapshot_changes(previous: dict[str, str], current: dict[str, str]) -> int:
    changed = sum(1 for key, value in current.items() if previous.get(key) != value)
    removed = sum(1 for key in previous.keys() if key not in current)
    return changed + removed


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
        logging.exception("Billing consumption recording failed after workflow success")


@router.post("/requirements/parse", response_model=RequirementsWorkflowResponse)
async def parse_requirements(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
    feedback: Optional[str] = Form(None),
    existing_requirements: Optional[str] = Form(None),
    workflow_settings: Optional[str] = Form(None),
) -> RequirementsWorkflowResponse:
    _settings = get_settings()
    parsed_workflow_settings = _parse_workflow_settings_form(workflow_settings)
    request_id = _get_request_id(request)
    is_refinement_request = bool(feedback and existing_requirements)
    billing_context = await run_in_threadpool(
        enforce_billing_access,
        current_user=current_user,
        billing_key="requirements.refine" if is_refinement_request else "requirements.parse",
    )

    workflow_run_id = start_workflow_run(
        operation="requirements.parse" if not is_refinement_request else "requirements.refine",
        actor=current_user,
        request_id=request_id,
        metadata={
            "has_feedback": is_refinement_request,
            "workflow_settings": parsed_workflow_settings.model_dump() if parsed_workflow_settings else {},
        },
    )

    # If feedback is provided, refine existing requirements
    if feedback and existing_requirements:
        try:
            existing_reqs = json.loads(existing_requirements)
            workflow = await run_in_threadpool(
                refine_requirements,
                existing_reqs,
                feedback,
                parsed_workflow_settings,
                current_user.sub,
            )
            response = RequirementsWorkflowResponse(
                source_name="refined",
                source_names=["refined"],
                raw_text="",  # Keep empty since we're refining
                requirements=workflow["requirements"],
                approved=workflow["approved"],
                review=workflow["review"],
                iteration_history=workflow["iteration_history"],
                coverage_metrics=workflow["coverage_metrics"],
                workflow_settings=workflow.get("workflow_settings", {}),
                workflow_diagnostics=workflow.get("workflow_diagnostics", {}),
            )
            modified_count = _count_snapshot_changes(
                _requirement_snapshot(existing_reqs),
                _requirement_snapshot(response.requirements),
            )
            event_id = _log_success(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="requirements.refine",
                event_type="requirements.refined",
                billing_key="requirements.refine",
                quantity=max(1, modified_count),
                unit="requirement",
                result_metadata={
                    "requirements_modified_count": modified_count,
                    "requirements_total": len(response.requirements),
                    "approved": response.approved,
                },
            )
            _record_billing_consumption_safe(
                current_user=current_user,
                billing_context=billing_context,
                source_event_id=event_id,
                request=request,
                workflow_run_id=workflow_run_id,
                billing_key="requirements.refine",
                quantity=max(1, modified_count),
                unit="requirement",
                metadata={"approved": response.approved},
            )
            response.requirements = persist_requirement_versions(
                current_requirements=response.requirements,
                previous_requirements=existing_reqs,
                actor=current_user,
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                source_event_id=event_id,
                operation="requirements.refine",
                approved=response.approved,
            )
            return response
        except Exception as exc:
            logging.exception("Requirement refinement failed")
            _log_failure(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="requirements.refine",
                event_type="requirements.refined",
                billing_key="requirements.refine",
                error_message=str(exc),
                metadata={"has_feedback": True},
            )
            raise HTTPException(status_code=500, detail="Refinement failed") from exc

    # Otherwise, parse the uploaded file
    uploads: List[UploadFile] = list(files or [])
    if file is not None:
        uploads.append(file)

    if not uploads:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        raw_sections: List[str] = []
        source_names: List[str] = []

        for upload in uploads:
            filename = upload.filename or "uploaded"
            content = await upload.read()
            if len(content) > MAX_UPLOAD_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max supported size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.",
                )

            if filename.endswith(".md") or upload.content_type == "text/markdown":
                parsed_text = content.decode("utf-8", errors="ignore")
            elif filename.endswith(".docx"):
                doc = Document(BytesIO(content))
                parsed_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            elif filename.endswith(".xlsx"):
                parsed_text = parse_excel_to_text(content)
            else:
                raise HTTPException(status_code=400, detail="Unsupported file type. Supported: .md, .docx, .xlsx")

            source_names.append(filename)
            raw_sections.append(f"--- SOURCE: {filename} ---\n{parsed_text}")

        raw_text = "\n\n".join(raw_sections)
        workflow = await run_in_threadpool(
            extract_requirements,
            raw_text,
            len(source_names),
            parsed_workflow_settings,
            current_user.sub,
        )
        source_name = source_names[0] if len(source_names) == 1 else f"{len(source_names)} documents"
        response = RequirementsWorkflowResponse(
            source_name=source_name,
            source_names=source_names,
            raw_text=raw_text,
            requirements=workflow["requirements"],
            approved=workflow["approved"],
            review=workflow["review"],
            iteration_history=workflow["iteration_history"],
            coverage_metrics=workflow["coverage_metrics"],
            workflow_settings=workflow.get("workflow_settings", {}),
            workflow_diagnostics=workflow.get("workflow_diagnostics", {}),
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.parse",
            event_type="requirements.parsed",
            billing_key="requirements.parse",
            quantity=len(response.requirements),
            unit="requirement",
            result_metadata={
                "requirements_generated_count": len(response.requirements),
                "document_count": len(source_names),
                "source_names": source_names,
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
            metadata={"approved": response.approved, "document_count": len(source_names)},
        )
        response.requirements = persist_requirement_versions(
            current_requirements=response.requirements,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="requirements.parse",
            source_name=source_name,
            source_names=source_names,
            raw_text=raw_text,
            approved=response.approved,
        )
        return response
    except HTTPException:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.parse",
            event_type="requirements.parsed",
            billing_key="requirements.parse",
            error_message="http_exception",
            metadata={"has_feedback": False},
        )
        raise
    except Exception as exc:
        logging.exception("Requirement parsing failed")
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.parse",
            event_type="requirements.parsed",
            billing_key="requirements.parse",
            error_message=str(exc),
            metadata={"has_feedback": False},
        )
        raise HTTPException(status_code=500, detail="Requirement parsing failed") from exc


@router.post("/requirements/enrich", response_model=EnrichResponse)
async def enrich_requirements(
    request: Request,
    payload: EnrichInput,
    current_user: AuthUser = Depends(get_current_user),
) -> EnrichResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="requirements.enrich",
        actor=current_user,
        request_id=request_id,
        metadata={"requirement_count": len(payload.requirements)},
    )
    try:
        grounded_context = payload.grounded_context or _build_grounded_context_from_enrich_input(payload)
        response = EnrichResponse(
            requirements=payload.requirements,
            app_link=payload.app_link,
            prototype_link=payload.prototype_link,
            diagram_links=payload.diagram_links,
            image_links=payload.image_links,
            notes=payload.notes,
            grounded_context=grounded_context,
        )
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.enrich",
            event_type="requirements.enriched",
            billing_key="requirements.enrich",
            quantity=max(1, len(response.grounded_context.artifact_sources)),
            unit="artifact_source",
            result_metadata={
                "requirement_count": len(response.requirements),
                "artifact_source_count": len(response.grounded_context.artifact_sources),
                "ui_element_count": len(response.grounded_context.ui_elements),
            },
        )
        return response
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.enrich",
            event_type="requirements.enriched",
            billing_key="requirements.enrich",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise
