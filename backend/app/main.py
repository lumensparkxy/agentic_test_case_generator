from io import BytesIO
from typing import List, Optional
import json
import logging
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Depends
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
from .services.context_grounding import build_grounded_context
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
    _current_user: AuthUser = Depends(get_current_user),
    files: Optional[List[UploadFile]] = File(None),
    file: UploadFile = File(None),
    feedback: Optional[str] = Form(None),
    existing_requirements: Optional[str] = Form(None),
    workflow_settings: Optional[str] = Form(None),
) -> RequirementsWorkflowResponse:
    _settings = get_settings()
    parsed_workflow_settings = _parse_workflow_settings_form(workflow_settings)
    
    # If feedback is provided, refine existing requirements
    if feedback and existing_requirements:
        try:
            existing_reqs = json.loads(existing_requirements)
            workflow = await run_in_threadpool(refine_requirements, existing_reqs, feedback, parsed_workflow_settings)
            return RequirementsWorkflowResponse(
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
        except Exception as exc:
            logging.exception("Requirement refinement failed")
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
        workflow = await run_in_threadpool(extract_requirements, raw_text, len(source_names), parsed_workflow_settings)
        source_name = source_names[0] if len(source_names) == 1 else f"{len(source_names)} documents"
        return RequirementsWorkflowResponse(
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
    except HTTPException:
        raise
    except Exception as exc:
        logging.exception("Requirement parsing failed")
        raise HTTPException(status_code=500, detail="Requirement parsing failed") from exc


@app.post("/requirements/enrich", response_model=EnrichResponse)
async def enrich_requirements(
    payload: EnrichInput,
    _current_user: AuthUser = Depends(get_current_user),
) -> EnrichResponse:
    grounded_context = payload.grounded_context or _build_grounded_context_from_enrich_input(payload)
    return EnrichResponse(
        requirements=payload.requirements,
        app_link=payload.app_link,
        prototype_link=payload.prototype_link,
        diagram_links=payload.diagram_links,
        image_links=payload.image_links,
        notes=payload.notes,
        grounded_context=grounded_context,
    )


@app.post("/testcases/generate", response_model=GenerateTestCasesResponse)
async def generate_test_cases_endpoint(
    payload: GenerateTestCasesInput,
    _current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    result = await run_in_threadpool(generate_test_cases, payload)
    return GenerateTestCasesResponse(**result)


@app.post("/testcases/refine", response_model=GenerateTestCasesResponse)
async def refine_test_cases_endpoint(
    payload: RefineTestCasesInput,
    _current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    result = await run_in_threadpool(refine_test_cases, payload)
    return GenerateTestCasesResponse(**result)


@app.post("/export/jira", response_model=JiraExportResponse)
async def export_jira(
    payload: JiraExportInput,
    _current_user: AuthUser = Depends(get_current_user),
) -> JiraExportResponse:
    return await run_in_threadpool(export_to_jira, payload)


@app.post("/export/csv")
async def export_csv(
    payload: GenerateTestCasesResponse,
    _current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to CSV format."""
    csv_content = export_to_csv(payload.test_cases)
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=test_cases.csv"}
    )


@app.post("/export/excel")
async def export_excel_endpoint(
    payload: GenerateTestCasesResponse,
    _current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to Excel format."""
    excel_bytes = export_to_excel(payload.test_cases)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"}
    )


@app.post("/export/json")
async def export_json_endpoint(
    payload: GenerateTestCasesResponse,
    _current_user: AuthUser = Depends(get_current_user),
):
    """Export test cases to JSON format."""
    json_content = export_to_json(payload.test_cases)
    return StreamingResponse(
        io.StringIO(json_content),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=test_cases.json"}
    )


@app.post("/automation/playwright", response_model=AutomationResponse)
async def automation_playwright(
    payload: AutomationInput,
    _current_user: AuthUser = Depends(get_current_user),
) -> AutomationResponse:
    return await run_in_threadpool(generate_playwright_pom, payload)
