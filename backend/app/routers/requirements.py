from ..services.knowledge_feedback import finish_generation
from ..services.guidance_dependency import generation_guidance
from contextlib import nullcontext
from io import BytesIO
from typing import Any, List, Optional
import hashlib
import json
import logging
from uuid import uuid4

from docx import Document
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from ..agents.adk_runtime import bounded_model_requests
from ..agents.requirements_agent import extract_requirements, refine_requirements
from ..auth.jwt_auth import get_current_user
from ..config import get_settings
from ..models import AuthUser, EnrichInput, EnrichResponse, RequirementsWorkflowResponse, WorkflowSettings
from ..observability.metrics import record_workflow_completed
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.billing_service import enforce_billing_access, record_billing_consumption
from ..services.context_grounding import build_grounded_context
from ..services.firestore_repository import get_required_firestore_client
from ..services.versioning_service import persist_requirement_versions
from ..services.workflow_project_service import (
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectPermissionError,
    append_stage_snapshot,
    find_idempotent_stage_snapshot,
    get_project_summary,
    project_error_to_http,
)
from ..utils.excel_parser import parse_excel_to_text

from ..contracts.requirement_imports import RequirementImportPreview
from ..services.requirement_import_service import require_review_client
from ..services.requirement_import_publication import ExtractionPublicationUnconfirmed, ProjectRequirementExtraction
from ..services.requirement_reconciliation import digest
from ..services.requirement_source_evidence import bind_document_evidence

router = APIRouter()

MAX_UPLOAD_SIZE_BYTES = 16 * 1024 * 1024
CONTEXT_OPERATION = "requirements.enrich"
CONTEXT_EVENT = "requirements.enriched"


class _ContextPublicationUnconfirmed(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "context_publication_unconfirmed", "message": "Context analysis could not be confirmed. Retry this request."})


async def _read_upload(upload: UploadFile) -> bytes:
    content = await upload.read(MAX_UPLOAD_SIZE_BYTES + 1)
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(413, f"File too large. Max supported size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.")
    return content


async def _upload_fingerprints(uploads):
    fingerprints = []
    for upload in uploads:
        try:
            content = await _read_upload(upload)
            fingerprints.append([upload.filename or "uploaded", upload.content_type, hashlib.sha256(content).hexdigest()])
        finally:
            await upload.seek(0)
    return fingerprints


def _build_grounded_context_from_enrich_input(payload: EnrichInput):
    return build_grounded_context(payload)


def _parse_docx_to_structured_text(content: bytes) -> str:
    doc = Document(BytesIO(content))
    lines: List[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = str(getattr(paragraph.style, "name", "") or "")
        if style_name.lower().startswith("heading"):
            level_digits = "".join(char for char in style_name if char.isdigit())
            level = max(1, min(int(level_digits or "1"), 6))
            lines.append(f"{'#' * level} {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _apply_file_source_metadata(requirements: List[Any], source_name: str, source_names: List[str]) -> List[Any]:
    source_label = source_name or (source_names[0] if source_names else "uploaded file")
    enriched = []
    for requirement in requirements:
        source_hierarchy = list(getattr(requirement, "source_hierarchy", []) or [])
        if not source_hierarchy:
            source_hierarchy = [source_label]
        enriched.append(
            requirement.model_copy(
                update={
                    "source_system": getattr(requirement, "source_system", None) or "file",
                    "source_path": getattr(requirement, "source_path", None) or source_label,
                    "source_hierarchy": source_hierarchy,
                }
            )
        )
    return enriched


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


def _parse_project_revision_form(base_project_revision: Optional[str]) -> Optional[int]:
    if base_project_revision is None or str(base_project_revision).strip() == "":
        return None
    try:
        return int(base_project_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="base_project_revision must be an integer") from exc


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


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_payload(item) for key, item in value.items()}
    return value


def _requirements_project_payload(response: RequirementsWorkflowResponse) -> dict[str, Any]:
    raw_text = response.raw_text or ""
    return {
        "source_name": response.source_name,
        "source_names": list(response.source_names or []),
        "raw_text_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else None,
        "requirements": _model_payload(response.requirements),
        "approved": response.approved,
        "review": _model_payload(response.review),
        "iteration_history": _model_payload(response.iteration_history),
        "coverage_metrics": _model_payload(response.coverage_metrics),
        "workflow_settings": _model_payload(response.workflow_settings),
        "workflow_diagnostics": _model_payload(response.workflow_diagnostics),
    }


def _context_project_payload(response: EnrichResponse) -> dict[str, Any]:
    return {
        "requirements": _model_payload(response.requirements),
        "app_link": str(response.app_link) if response.app_link else None,
        "prototype_link": str(response.prototype_link) if response.prototype_link else None,
        "diagram_links": [str(item) for item in response.diagram_links or []],
        "image_links": [str(item) for item in response.image_links or []],
        "notes": response.notes,
        "execution_prerequisites": _model_payload(response.execution_prerequisites),
        "grounded_context": _model_payload(response.grounded_context),
    }


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


@router.post(
    "/requirements/parse",
    response_model=RequirementsWorkflowResponse | RequirementImportPreview,
    dependencies=[Depends(generation_guidance("requirements", bounded_extraction=True))],
)
async def parse_requirements(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
    feedback: Optional[str] = Form(None),
    existing_requirements: Optional[str] = Form(None),
    workflow_settings: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    base_project_revision: Optional[str] = Form(None),
    import_mode: Optional[str] = Form(None),
) -> RequirementsWorkflowResponse | RequirementImportPreview:
    parsed_workflow_settings = _parse_workflow_settings_form(workflow_settings)
    parsed_project_revision = _parse_project_revision_form(base_project_revision)
    await run_in_threadpool(require_review_client, project_id, parsed_project_revision, import_mode, current_user)
    request_id = _get_request_id(request)
    is_refinement_request = bool(feedback and existing_requirements)
    operation = "requirements.refine" if is_refinement_request else "requirements.parse"
    event_type = "requirements.refined" if is_refinement_request else "requirements.parsed"
    uploads: List[UploadFile] = list(files or [])
    if file is not None:
        uploads.append(file)
    if project_id and not uploads and not is_refinement_request:
        raise HTTPException(400, "No file provided")
    if project_id:
        source = [feedback, existing_requirements] if is_refinement_request else await _upload_fingerprints(uploads)
        fingerprint = digest(
            [
                operation,
                parsed_project_revision,
                source,
                parsed_workflow_settings.model_dump(mode="json") if parsed_workflow_settings else None,
                request.headers.get("X-Knowledge-Bypass", "").lower() == "true",
            ]
        )
        extraction = ProjectRequirementExtraction(project_id, parsed_project_revision, current_user, request_id, operation, fingerprint)
        saved = await run_in_threadpool(extraction.find_preview)
        if saved is not None:
            return saved

    billing_context = await run_in_threadpool(
        enforce_billing_access,
        current_user=current_user,
        billing_key=operation,
    )

    admission = await run_in_threadpool(extraction.admit) if project_id else None
    if admission is not None and admission.result_import_id:
        return await run_in_threadpool(extraction.find_preview, required=True)

    async def stop_admission():
        if admission is not None:
            try:
                await run_in_threadpool(admission.stop)
            except Exception:
                logging.exception("Could not reconcile requirement extraction admission")

    async def bounded_call(function, *args, **kwargs):
        with bounded_model_requests(admission.reserve) if admission is not None else nullcontext():
            return await run_in_threadpool(function, *args, **kwargs)

    try:
        workflow_run_id = start_workflow_run(
            operation=operation,
            actor=current_user,
            request_id=request_id,
            metadata={
                "has_feedback": is_refinement_request,
                "workflow_settings": parsed_workflow_settings.model_dump() if parsed_workflow_settings else {},
            },
        )
    except Exception:
        await stop_admission()
        raise

    published = False

    async def publish(response, quantity, result_metadata, billing_metadata):
        nonlocal published
        await bounded_call(
            extraction.publish,
            response=response,
            event_type=event_type,
            billing_key=operation,
            run_id=workflow_run_id,
            billing_context=billing_context,
            quantity=quantity,
            result_metadata=result_metadata,
            billing_metadata=billing_metadata,
            admission=admission,
        )
        published = True
        return await run_in_threadpool(extraction.find_preview, required=True)

    async def fail(error_message):
        if not published:
            await stop_admission()
            await run_in_threadpool(
                _log_failure,
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation=operation,
                event_type=event_type,
                billing_key=operation,
                error_message=error_message,
                metadata={"has_feedback": is_refinement_request},
            )

    # If feedback is provided, refine existing requirements.
    if is_refinement_request:
        try:
            existing_reqs = json.loads(existing_requirements)
            workflow = await bounded_call(
                refine_requirements,
                existing_reqs,
                feedback,
                parsed_workflow_settings,
                actor_user_id=current_user.sub,
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                operation="requirements.refine",
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
            result_metadata = {
                "requirements_modified_count": modified_count,
                "requirements_total": len(response.requirements),
                "approved": response.approved,
            }
            billing_metadata = {"approved": response.approved}
            if project_id:
                return await publish(response, max(1, modified_count), result_metadata, billing_metadata)
            event_id = _log_success(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="requirements.refine",
                event_type="requirements.refined",
                billing_key="requirements.refine",
                quantity=max(1, modified_count),
                unit="requirement",
                result_metadata=result_metadata,
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
                metadata=billing_metadata,
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
            return finish_generation(response, current_user, project_id, "requirements", feedback, request_id)
        except ExtractionPublicationUnconfirmed:
            await stop_admission()
            raise
        except HTTPException:
            if project_id:
                await fail("http_exception")
            raise
        except Exception as exc:
            logging.exception("Requirement refinement failed")
            await fail(str(exc))
            raise HTTPException(status_code=500, detail="Refinement failed") from exc

    # Otherwise, parse the uploaded file
    if not uploads:
        raise HTTPException(400, "No file provided")

    try:
        raw_sections: List[str] = []
        source_names: List[str] = []
        documents: List[tuple[str, str]] = []

        for upload in uploads:
            filename = upload.filename or "uploaded"
            content = await _read_upload(upload)

            if filename.endswith(".md") or upload.content_type == "text/markdown":
                parsed_text = content.decode("utf-8", errors="ignore")
            elif filename.endswith(".docx"):
                parsed_text = _parse_docx_to_structured_text(content)
            elif filename.endswith(".xlsx"):
                parsed_text = parse_excel_to_text(content)
            else:
                raise HTTPException(status_code=400, detail="Unsupported file type. Supported: .md, .docx, .xlsx")

            source_names.append(filename)
            documents.append((filename, parsed_text))
            raw_sections.append(f"--- SOURCE: {filename} ---\n{parsed_text}")

        raw_text = "\n\n".join(raw_sections)
        workflow = await bounded_call(
            extract_requirements,
            raw_text,
            len(source_names),
            parsed_workflow_settings,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="requirements.parse",
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
        response.requirements = _apply_file_source_metadata(response.requirements, source_name, source_names)
        evidence = await run_in_threadpool(bind_document_evidence, response.requirements, documents)
        response.requirements = evidence.requirements
        response.source_evidence_findings = evidence.findings
        if evidence.findings:
            response.approved = False
            response.review = response.review.model_copy(
                update={
                    "approved": False,
                    "summary": "Review the unresolved inclusive range evidence.",
                    "blocking_issues": [*response.review.blocking_issues, "Inclusive range evidence is incomplete."],
                }
            )
        result_metadata = {
            "requirements_generated_count": len(response.requirements),
            "document_count": len(source_names),
            "source_names": source_names,
            "approved": response.approved,
        }
        billing_metadata = {"approved": response.approved, "document_count": len(source_names)}
        if project_id:
            return await publish(response, len(response.requirements), result_metadata, billing_metadata)
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="requirements.parse",
            event_type="requirements.parsed",
            billing_key="requirements.parse",
            quantity=len(response.requirements),
            unit="requirement",
            result_metadata=result_metadata,
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
            metadata=billing_metadata,
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
        return finish_generation(response, current_user, project_id, "requirements", feedback, request_id)
    except ExtractionPublicationUnconfirmed:
        await stop_admission()
        raise
    except HTTPException:
        await fail("http_exception")
        raise
    except Exception as exc:
        logging.exception("Requirement parsing failed")
        await fail(str(exc))
        raise HTTPException(status_code=500, detail="Requirement parsing failed") from exc


def _find_context_receipt(payload, actor, key, fingerprint, *, require_current=True):
    try:
        receipt = find_idempotent_stage_snapshot(payload.project_id, "context", CONTEXT_OPERATION, key, actor=actor, require_current=require_current)
    except (ProjectConflictError, ProjectNotFoundError, ProjectPermissionError) as exc:
        raise project_error_to_http(exc) from exc
    except Exception as exc:
        raise _ContextPublicationUnconfirmed() from exc
    if receipt and receipt.metadata.get("analysis_request_fingerprint") != fingerprint:
        raise HTTPException(409, "This request already saved Context for different inputs.")
    return receipt


def _context_response(payload, grounded_context):
    return EnrichResponse(**payload.model_dump(exclude={"grounded_context"}), grounded_context=grounded_context)


def _publish_context(response, actor, request_id, run_id, base_revision, key, fingerprint, quantity, metadata):
    try:
        client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
        writes = []
        event_id = record_usage_event(
            event_type=CONTEXT_EVENT,
            billing_key=CONTEXT_OPERATION,
            quantity=quantity,
            unit="artifact_source",
            actor=actor,
            request_id=request_id,
            workflow_run_id=run_id,
            status="completed",
            metadata={"operation": CONTEXT_OPERATION, **metadata},
            pending_writes=writes,
            firestore_client=client,
        )
        complete_workflow_run(run_id, status="completed", metadata=metadata, pending_writes=writes, firestore_client=client)
        return append_stage_snapshot(
            project_id=response.project_id,
            stage="context",
            payload=_context_project_payload(response),
            operation=CONTEXT_OPERATION,
            actor=actor,
            request_id=request_id,
            workflow_run_id=run_id,
            source_event_id=event_id,
            approved=True,
            title="Context analyzed",
            metadata={**metadata, "analysis_request_fingerprint": fingerprint},
            base_project_revision=base_revision,
            idempotency_key=key,
            pending_writes=writes,
        )
    except Exception as exc:
        # Reconcile a possible commit before checking whether its result is still current.
        receipt = _find_context_receipt(response, actor, key, fingerprint, require_current=False)
        if receipt:
            return receipt
        if isinstance(exc, HTTPException):
            raise
        raise project_error_to_http(exc) from exc


@router.post("/requirements/enrich", response_model=EnrichResponse)
def enrich_requirements(
    request: Request,
    payload: EnrichInput,
    current_user: AuthUser = Depends(get_current_user),
) -> EnrichResponse:
    request_id = _get_request_id(request)
    base_revision = payload.base_project_revision
    if payload.project_id:
        key = digest(["context", current_user.sub, request_id])
        fingerprint = digest(payload.model_dump(mode="json"))
        receipt = _find_context_receipt(payload, current_user, key, fingerprint)
        if receipt:
            return _context_response(payload, receipt.payload["grounded_context"])
        try:
            project = get_project_summary(payload.project_id, actor=current_user)
        except Exception as exc:
            raise project_error_to_http(exc) from exc
        if project.status != "active" or (base_revision is not None and base_revision != project.current_revision):
            receipt = _find_context_receipt(payload, current_user, key, fingerprint)
            if receipt:
                return _context_response(payload, receipt.payload["grounded_context"])
            conflict = ProjectConflictError(project.current_revision)
            raise project_error_to_http(conflict)
        base_revision = project.current_revision

    workflow_run_id = start_workflow_run(
        operation=CONTEXT_OPERATION,
        actor=current_user,
        request_id=request_id,
        metadata={"requirement_count": len(payload.requirements)},
    )
    try:
        grounded_context = payload.grounded_context
        if grounded_context is None:
            grounded_context = _build_grounded_context_from_enrich_input(payload)
        response = _context_response(payload, grounded_context)
        metadata = {
            "requirement_count": len(response.requirements),
            "artifact_source_count": len(response.grounded_context.artifact_sources),
            "ui_element_count": len(response.grounded_context.ui_elements),
        }
        quantity = max(1, metadata["artifact_source_count"])
        if payload.project_id:
            snapshot = _publish_context(response, current_user, request_id, workflow_run_id, base_revision, key, fingerprint, quantity, metadata)
            if snapshot.metadata.get("analysis_request_fingerprint") != fingerprint:
                raise HTTPException(409, "This request already saved Context for different inputs.")
        else:
            _log_success(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation=CONTEXT_OPERATION,
                event_type=CONTEXT_EVENT,
                billing_key=CONTEXT_OPERATION,
                quantity=quantity,
                unit="artifact_source",
                result_metadata=metadata,
            )
            return response
    except _ContextPublicationUnconfirmed:
        raise
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation=CONTEXT_OPERATION,
            event_type=CONTEXT_EVENT,
            billing_key=CONTEXT_OPERATION,
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise

    if snapshot.workflow_run_id == workflow_run_id:
        record_workflow_completed(workflow_run_id, "completed")
    else:
        complete_workflow_run(workflow_run_id, status="completed", metadata={**metadata, "reused_snapshot_id": snapshot.snapshot_id})
    receipt = _find_context_receipt(payload, current_user, key, fingerprint)
    if receipt is None:
        raise _ContextPublicationUnconfirmed()
    return _context_response(payload, receipt.payload["grounded_context"])
