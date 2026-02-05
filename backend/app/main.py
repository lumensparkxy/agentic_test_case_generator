from io import BytesIO
from typing import List, Optional
import json
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from docx import Document

from .config import get_settings
from .models import (
    ParseResponse,
    EnrichInput,
    GenerateTestCasesInput,
    GenerateTestCasesResponse,
    JiraExportInput,
    JiraExportResponse,
    AutomationInput,
    AutomationResponse,
    Requirement,
)
from fastapi.responses import StreamingResponse
import csv
import io

from .agents.requirements_agent import extract_requirements, refine_requirements
from .agents.test_case_agent import generate_test_cases
from .agents.export_agent import export_to_jira, export_to_csv, export_to_excel, export_to_json
from .agents.automation_agent import generate_playwright_pom
from .utils.excel_parser import parse_excel_to_text

app = FastAPI(title="Agentic Test Case Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/requirements/parse", response_model=ParseResponse)
async def parse_requirements(
    file: UploadFile = File(None),
    feedback: Optional[str] = Form(None),
    existing_requirements: Optional[str] = Form(None),
) -> ParseResponse:
    _settings = get_settings()
    
    # If feedback is provided, refine existing requirements
    if feedback and existing_requirements:
        try:
            existing_reqs = json.loads(existing_requirements)
            requirements = refine_requirements(existing_reqs, feedback)
            return ParseResponse(
                source_name="refined",
                raw_text="",  # Keep empty since we're refining
                requirements=requirements
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Refinement failed: {str(exc)}") from exc
    
    # Otherwise, parse the uploaded file
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")
    
    filename = file.filename or "uploaded"
    content = await file.read()
    try:
        if filename.endswith(".md") or file.content_type == "text/markdown":
            raw_text = content.decode("utf-8", errors="ignore")
        elif filename.endswith(".docx"):
            doc = Document(BytesIO(content))
            raw_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        elif filename.endswith(".xlsx"):
            raw_text = parse_excel_to_text(content)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Supported: .md, .docx, .xlsx")

        requirements = extract_requirements(raw_text)
        return ParseResponse(source_name=filename, raw_text=raw_text, requirements=requirements)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/requirements/enrich", response_model=EnrichInput)
async def enrich_requirements(payload: EnrichInput) -> EnrichInput:
    return payload


@app.post("/testcases/generate", response_model=GenerateTestCasesResponse)
async def generate_test_cases_endpoint(
    payload: GenerateTestCasesInput,
) -> GenerateTestCasesResponse:
    test_cases = generate_test_cases(payload)
    return GenerateTestCasesResponse(test_cases=test_cases)


@app.post("/export/jira", response_model=JiraExportResponse)
async def export_jira(payload: JiraExportInput) -> JiraExportResponse:
    return export_to_jira(payload)


@app.post("/export/csv")
async def export_csv(payload: GenerateTestCasesResponse):
    """Export test cases to CSV format."""
    csv_content = export_to_csv(payload.test_cases)
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=test_cases.csv"}
    )


@app.post("/export/excel")
async def export_excel_endpoint(payload: GenerateTestCasesResponse):
    """Export test cases to Excel format."""
    excel_bytes = export_to_excel(payload.test_cases)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"}
    )


@app.post("/export/json")
async def export_json_endpoint(payload: GenerateTestCasesResponse):
    """Export test cases to JSON format."""
    json_content = export_to_json(payload.test_cases)
    return StreamingResponse(
        io.StringIO(json_content),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=test_cases.json"}
    )


@app.post("/automation/playwright", response_model=AutomationResponse)
async def automation_playwright(payload: AutomationInput) -> AutomationResponse:
    return generate_playwright_pom(payload)
