from io import BytesIO
from typing import Any, List, Optional
import json
import logging
from uuid import uuid4
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from docx import Document
from pydantic import ValidationError

from .config import get_auth_settings, get_cors_allow_origins, get_settings
from .models import (
    AuthTokenResponse,
    AuthUser,
    EnrichInput,
    EnrichResponse,
    GenerateTestCasesInput,
    GenerateTestCasesResponse,
    GoogleLoginRequest,
    JiraExportInput,
    JiraExportResponse,
    LogoutResponse,
    AutomationInput,
    AutomationResponse,
    Requirement,
    RefineTestCasesInput,
    RequirementsWorkflowResponse,
    WorkflowSettings,
)
from fastapi.responses import StreamingResponse
import csv
import io

from .agents.requirements_agent import extract_requirements, refine_requirements
from .agents.test_case_agent import generate_test_cases, refine_test_cases
from .agents.export_agent import export_to_jira, export_to_csv, export_to_excel, export_to_json
from .agents.automation_agent import generate_playwright_pom
from .auth.google_auth import verify_google_credential
from .auth.jwt_auth import create_access_token, get_current_user
from .services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from .services.context_grounding import build_grounded_context
from .services.versioning_service import persist_requirement_versions, persist_test_case_versions
from .utils.excel_parser import parse_excel_to_text

app = FastAPI(title="Agentic Test Case Generator")

MAX_UPLOAD_SIZE_BYTES = 16 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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


def _test_case_snapshot(test_cases: List[Any]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for index, item in enumerate(test_cases or []):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        case_id = str((item or {}).get("id") or f"TC-{index + 1:03d}")
        snapshot[case_id] = json.dumps(item or {}, sort_keys=True, default=str)
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


@app.post("/auth/google/login", response_model=AuthTokenResponse)
async def auth_google_login(payload: GoogleLoginRequest) -> AuthTokenResponse:
    settings = get_auth_settings()
    user_claims = verify_google_credential(payload.credential, settings.google_client_ids, payload.client_id)
    user = AuthUser(**user_claims)
    access_token, expires_in = create_access_token(user)
    return AuthTokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=user,
    )


@app.get("/auth/me", response_model=AuthUser)
async def auth_me(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    return current_user


@app.post("/auth/logout", response_model=LogoutResponse)
async def auth_logout() -> LogoutResponse:
    return LogoutResponse(status="ok")


@app.post("/requirements/parse", response_model=RequirementsWorkflowResponse)
async def parse_requirements(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    files: Optional[List[UploadFile]] = File(None),
    file: UploadFile = File(None),
    feedback: Optional[str] = Form(None),
    existing_requirements: Optional[str] = Form(None),
    workflow_settings: Optional[str] = Form(None),
) -> RequirementsWorkflowResponse:
    _settings = get_settings()
    parsed_workflow_settings = _parse_workflow_settings_form(workflow_settings)
    request_id = _get_request_id(request)

    workflow_run_id = start_workflow_run(
        operation="requirements.parse" if not (feedback and existing_requirements) else "requirements.refine",
        actor=current_user,
        request_id=request_id,
        metadata={
            "has_feedback": bool(feedback and existing_requirements),
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


@app.post("/requirements/enrich", response_model=EnrichResponse)
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


@app.post("/testcases/generate", response_model=GenerateTestCasesResponse)
async def generate_test_cases_endpoint(
    request: Request,
    payload: GenerateTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="testcases.generate",
        actor=current_user,
        request_id=request_id,
        metadata={"requirement_count": len(payload.requirements)},
    )
    try:
        result = await run_in_threadpool(generate_test_cases, payload, current_user.sub)
        response = GenerateTestCasesResponse(**result)
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.generate",
            event_type="testcases.generated",
            billing_key="testcases.generate",
            quantity=len(response.test_cases),
            unit="test_case",
            result_metadata={
                "test_cases_generated_count": len(response.test_cases),
                "requirement_count": len(payload.requirements),
                "approved": response.approved,
            },
        )
        response.test_cases = persist_test_case_versions(
            current_test_cases=response.test_cases,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="testcases.generate",
            approved=response.approved,
        )
        return response
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.generate",
            event_type="testcases.generated",
            billing_key="testcases.generate",
            error_message=str(exc),
            metadata={"requirement_count": len(payload.requirements)},
        )
        raise


@app.post("/testcases/refine", response_model=GenerateTestCasesResponse)
async def refine_test_cases_endpoint(
    request: Request,
    payload: RefineTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="testcases.refine",
        actor=current_user,
        request_id=request_id,
        metadata={"existing_test_case_count": len(payload.test_cases)},
    )
    try:
        result = await run_in_threadpool(refine_test_cases, payload, current_user.sub)
        response = GenerateTestCasesResponse(**result)
        modified_count = _count_snapshot_changes(
            _test_case_snapshot(payload.test_cases),
            _test_case_snapshot(response.test_cases),
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.refine",
            event_type="testcases.refined",
            billing_key="testcases.refine",
            quantity=max(1, modified_count),
            unit="test_case",
            result_metadata={
                "test_cases_modified_count": modified_count,
                "test_cases_total": len(response.test_cases),
                "approved": response.approved,
            },
        )
        response.test_cases = persist_test_case_versions(
            current_test_cases=response.test_cases,
            previous_test_cases=payload.test_cases,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="testcases.refine",
            approved=response.approved,
        )
        return response
    except Exception as exc:
        _log_failure(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.refine",
            event_type="testcases.refined",
            billing_key="testcases.refine",
            error_message=str(exc),
            metadata={"existing_test_case_count": len(payload.test_cases)},
        )
        raise


@app.post("/export/jira", response_model=JiraExportResponse)
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


@app.post("/export/csv")
async def export_csv(
    request: Request,
    payload: GenerateTestCasesResponse,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to CSV format."""
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="export.csv",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        csv_content = export_to_csv(payload.test_cases)
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.csv",
            event_type="export.csv",
            billing_key="export.csv",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata={"test_case_count": len(payload.test_cases), "content_length": len(csv_content)},
        )
        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=test_cases.csv"}
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


@app.post("/export/excel")
async def export_excel_endpoint(
    request: Request,
    payload: GenerateTestCasesResponse,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to Excel format."""
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="export.excel",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        excel_bytes = export_to_excel(payload.test_cases)
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.excel",
            event_type="export.excel",
            billing_key="export.excel",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata={"test_case_count": len(payload.test_cases), "byte_count": len(excel_bytes)},
        )
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"}
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


@app.post("/export/json")
async def export_json_endpoint(
    request: Request,
    payload: GenerateTestCasesResponse,
    current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to JSON format."""
    request_id = _get_request_id(request)
    workflow_run_id = start_workflow_run(
        operation="export.json",
        actor=current_user,
        request_id=request_id,
        metadata={"test_case_count": len(payload.test_cases)},
    )
    try:
        json_content = export_to_json(payload.test_cases)
        _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="export.json",
            event_type="export.json",
            billing_key="export.json",
            quantity=len(payload.test_cases),
            unit="test_case",
            result_metadata={"test_case_count": len(payload.test_cases), "content_length": len(json_content)},
        )
        return StreamingResponse(
            io.StringIO(json_content),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=test_cases.json"}
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


@app.post("/automation/playwright", response_model=AutomationResponse)
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
