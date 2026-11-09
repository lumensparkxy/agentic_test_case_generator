from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool

from ..contracts.execution import ExecutionRequestReceipt
from ..contracts.exports import SavedFileReport
from ..contracts.projects import QaProjectExecutionRunDetail, QaProjectTestCaseEvidence, QaProjectTestCaseReviewInput, QaProjectUseCaseGenerationInput
from ..contracts.scenario_refresh import ScenarioRefreshRelease, ScenarioRefreshReleased, ScenarioRefreshStatus
from ..contracts.scoped_impact import ScopedImpactRelease, ScopedImpactReleased, ScopedImpactStatus
from ..contracts.selected_case_repair import (
    SelectedCaseRepairInput,
    SelectedCaseRepairRelease,
    SelectedCaseRepairReleased,
    SelectedCaseRepairResult,
    SelectedCaseRepairStatus,
)
from ..services.use_case_generation_service import generate_project_use_cases, release_scenario_refresh, scenario_refresh_status
from ..auth.jwt_auth import get_current_user
from ..models import (
    AuthUser,
    ImpactAnalysisInput,
    ImpactUpdateApplyInput,
    OrchestratorRunListResponse,
    OrchestratorStatusResponse,
    QaProjectCreateInput,
    QaProjectDetail,
    QaProjectListResponse,
    QaProjectTimelineEvent,
    QaProjectUpdateInput,
    QaProjectUseCaseSnapshotInput,
    UseCaseReviewRequest,
    UseCaseReviewResponse,
)
from ..services.impact_update_service import analyze_project_impact, apply_project_impact_update, impact_error_to_http
from ..services.execution_request_service import public_execution_request, read_execution_request
from ..services.orchestrator_run_service import list_orchestrator_runs
from ..services.orchestrator_service import get_project_orchestrator_status
from ..services.saved_file_report import REPORT_ID_PATTERN, read_saved_file_report
from ..services.scoped_impact import release_scoped_impact, scoped_impact_status
from ..services.selected_case_repair import release_selected_case_repair, repair_selected_case, selected_case_repair_status
from ..services.test_case_suite_review import review_saved_test_cases
from ..services.use_case_review_service import review_use_case_snapshot, use_case_review_error_to_http
from ..services.workflow_project_service import (
    append_stage_snapshot,
    create_project,
    get_project,
    get_project_execution_run,
    get_project_test_case_evidence,
    list_projects,
    project_error_to_http,
    update_project,
)

router = APIRouter()


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


@router.post("/projects", response_model=QaProjectDetail)
async def create_qa_project(
    request: Request,
    payload: QaProjectCreateInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(
            create_project,
            name=payload.name,
            description=payload.description,
            actor=current_user,
            request_id=_get_request_id(request),
        )
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects", response_model=QaProjectListResponse)
async def list_qa_projects(
    include_archived: bool = False,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectListResponse:
    try:
        projects = await run_in_threadpool(list_projects, actor=current_user, include_archived=include_archived)
        return QaProjectListResponse(projects=projects)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}", response_model=QaProjectDetail)
async def get_qa_project(
    project_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(get_project, project_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.patch("/projects/{project_id}", response_model=QaProjectDetail)
async def update_qa_project(
    project_id: str,
    request: Request,
    payload: QaProjectUpdateInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(
            update_project,
            project_id=project_id,
            actor=current_user,
            request_id=_get_request_id(request),
            name=payload.name,
            description=payload.description,
            status_value=payload.status,
            testing_intent=payload.testing_intent,
            base_project_revision=payload.base_project_revision,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/timeline", response_model=list[QaProjectTimelineEvent])
async def get_qa_project_timeline(
    project_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> list[QaProjectTimelineEvent]:
    try:
        project = await run_in_threadpool(get_project, project_id, actor=current_user)
        return project.timeline
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/execution-runs/{run_record_id}", response_model=QaProjectExecutionRunDetail)
async def get_qa_project_execution_run(
    project_id: str,
    run_record_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectExecutionRunDetail:
    try:
        return await run_in_threadpool(get_project_execution_run, project_id, run_record_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/test-case-evidence", response_model=QaProjectTestCaseEvidence)
async def get_qa_project_test_case_evidence(
    project_id: str,
    snapshot_id: str = Query(min_length=1, max_length=160, pattern=r"^[^/]+$"),
    case_id: str = Query(min_length=1),
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectTestCaseEvidence:
    try:
        return await run_in_threadpool(get_project_test_case_evidence, project_id, snapshot_id, case_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/reports/{report_id}", response_model=SavedFileReport)
async def get_qa_project_saved_file_report(
    project_id: str,
    report_id: str = Path(pattern=REPORT_ID_PATTERN),
    current_user: AuthUser = Depends(get_current_user),
) -> SavedFileReport:
    try:
        return await run_in_threadpool(read_saved_file_report, project_id, report_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/execution-requests/{request_id}", response_model=ExecutionRequestReceipt)
async def get_qa_project_execution_request(
    project_id: str,
    request_id: str = Path(max_length=160),
    current_user: AuthUser = Depends(get_current_user),
) -> ExecutionRequestReceipt:
    try:
        record = await run_in_threadpool(read_execution_request, project_id, request_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc
    if not record:
        raise HTTPException(404, "Execution request has no saved receipt.")
    return public_execution_request(record)


@router.get("/projects/{project_id}/orchestrator/status", response_model=OrchestratorStatusResponse)
async def get_qa_project_orchestrator_status(
    project_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> OrchestratorStatusResponse:
    try:
        return await run_in_threadpool(get_project_orchestrator_status, project_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/orchestrator/runs", response_model=OrchestratorRunListResponse)
async def list_qa_project_orchestrator_runs(
    project_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> OrchestratorRunListResponse:
    try:
        return await run_in_threadpool(list_orchestrator_runs, project_id=project_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/impact-analysis", response_model=QaProjectDetail)
async def analyze_qa_project_impact(
    project_id: str,
    request: Request,
    payload: ImpactAnalysisInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(
            analyze_project_impact,
            project_id=project_id,
            actor=current_user,
            request_id=_get_request_id(request),
            base_project_revision=payload.base_project_revision,
        )
    except Exception as exc:
        raise impact_error_to_http(exc) from exc


@router.get("/projects/{project_id}/impact-update/status", response_model=ScopedImpactStatus)
def get_scoped_impact_status(
    project_id: str,
    request_id: str | None = Query(default=None, min_length=1, max_length=160),
    actor: AuthUser = Depends(get_current_user),
):
    try:
        return scoped_impact_status(project_id, actor, request_id=request_id)
    except Exception as exc:
        raise impact_error_to_http(exc) from exc


@router.post("/projects/{project_id}/impact-update/{operation_id}/release", response_model=ScopedImpactReleased)
def release_project_scoped_impact(
    project_id: str,
    operation_id: str,
    payload: ScopedImpactRelease,
    actor: AuthUser = Depends(get_current_user),
):
    try:
        return ScopedImpactReleased(released=release_scoped_impact(project_id, actor, operation_id, payload.fence))
    except Exception as exc:
        raise impact_error_to_http(exc) from exc


@router.post("/projects/{project_id}/impact-update/apply", response_model=QaProjectDetail)
async def apply_qa_project_impact_update(
    project_id: str,
    request: Request,
    payload: ImpactUpdateApplyInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(
            apply_project_impact_update,
            project_id=project_id,
            actor=current_user,
            request_id=_get_request_id(request),
            analysis_snapshot_id=payload.analysis_snapshot_id,
            accepted_recommendation_ids=payload.accepted_recommendation_ids,
            base_project_revision=payload.base_project_revision,
            request_id_explicit=bool(request.headers.get("X-Request-ID")),
        )
    except Exception as exc:
        raise impact_error_to_http(exc) from exc


@router.get("/projects/{project_id}/test-cases/repair", response_model=SelectedCaseRepairStatus)
def get_selected_case_repair(
    project_id: str,
    snapshot_id: str | None = Query(default=None, min_length=1, max_length=160),
    case_id: str | None = Query(default=None, min_length=1, max_length=160),
    request_id: str | None = Query(default=None, min_length=1, max_length=160),
    actor: AuthUser = Depends(get_current_user),
):
    try:
        return selected_case_repair_status(project_id, actor, snapshot_id=snapshot_id, case_id=case_id, request_id=request_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/test-cases/repair", response_model=SelectedCaseRepairResult)
def repair_project_test_case(
    project_id: str,
    payload: SelectedCaseRepairInput,
    request: Request,
    actor: AuthUser = Depends(get_current_user),
):
    request_id = request.headers.get("X-Request-ID")
    if not request_id or not request_id.strip() or len(request_id) > 160:
        raise HTTPException(422, "A bounded X-Request-ID is required for selected-case repair.")
    try:
        return repair_selected_case(project_id, payload, actor, request_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/test-cases/repair/{operation_id}/release", response_model=SelectedCaseRepairReleased)
def release_project_test_case_repair(
    project_id: str,
    operation_id: str,
    payload: SelectedCaseRepairRelease,
    actor: AuthUser = Depends(get_current_user),
):
    try:
        return SelectedCaseRepairReleased(released=release_selected_case_repair(project_id, actor, operation_id, payload.fence))
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/test-cases/review", response_model=QaProjectDetail)
async def review_qa_project_test_cases(
    project_id: str,
    request: Request,
    payload: QaProjectTestCaseReviewInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(
            review_saved_test_cases,
            project_id=project_id,
            snapshot_id=payload.snapshot_id,
            base_project_revision=payload.base_project_revision,
            actor=current_user,
            request_id=_get_request_id(request),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/use-cases", response_model=QaProjectDetail)
async def save_qa_project_use_cases(
    project_id: str,
    request: Request,
    payload: QaProjectUseCaseSnapshotInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    request_id = _get_request_id(request)
    snapshot_payload = {
        "requirement_analysis": payload.requirement_analysis,
        "coverage_plan": payload.coverage_plan,
        "review": payload.review,
        "coverage_metrics": payload.coverage_metrics,
        "workflow_settings": payload.workflow_settings,
        "workflow_diagnostics": payload.workflow_diagnostics,
    }
    try:
        await run_in_threadpool(
            append_stage_snapshot,
            project_id=project_id,
            stage="use_cases",
            payload=snapshot_payload,
            operation="use_cases.save",
            actor=current_user,
            request_id=request_id,
            approved=payload.approved,
            source_snapshot_id=payload.source_snapshot_id,
            title="Use cases saved",
            metadata={
                "requirement_analysis_count": len(payload.requirement_analysis),
                "coverage_plan_count": len(payload.coverage_plan),
            },
            base_project_revision=payload.base_project_revision,
        )
        return await run_in_threadpool(get_project, project_id, actor=current_user)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post(
    "/projects/{project_id}/use-cases/reviews",
    response_model=UseCaseReviewResponse,
)
async def review_qa_project_use_cases(
    project_id: str,
    request: Request,
    payload: UseCaseReviewRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> UseCaseReviewResponse:
    try:
        return await run_in_threadpool(
            review_use_case_snapshot,
            project_id=project_id,
            snapshot_id=payload.snapshot_id,
            base_project_revision=payload.base_project_revision,
            decision=payload.decision,
            comment=payload.comment,
            scenario_reviews=[item.model_dump() for item in payload.scenario_reviews],
            actor=current_user,
            request_id=_get_request_id(request),
        )
    except Exception as exc:
        raise use_case_review_error_to_http(exc) from exc


@router.get("/projects/{project_id}/use-cases/refresh", response_model=ScenarioRefreshStatus)
def get_scenario_refresh(
    project_id: str,
    request_id: str | None = Query(default=None, min_length=1, max_length=160),
    actor: AuthUser = Depends(get_current_user),
):
    try:
        return scenario_refresh_status(project_id, actor, request_id=request_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/use-cases/refresh/{operation_id}/release", response_model=ScenarioRefreshReleased)
def release_project_scenario_refresh(
    project_id: str,
    operation_id: str,
    payload: ScenarioRefreshRelease,
    actor: AuthUser = Depends(get_current_user),
):
    try:
        return ScenarioRefreshReleased(released=release_scenario_refresh(project_id, actor, operation_id, payload.fence))
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.post("/projects/{project_id}/use-cases/generate", response_model=QaProjectDetail)
async def generate_qa_project_use_cases(
    project_id: str,
    request: Request,
    payload: QaProjectUseCaseGenerationInput,
    current_user: AuthUser = Depends(get_current_user),
) -> QaProjectDetail:
    try:
        return await run_in_threadpool(
            generate_project_use_cases,
            project_id=project_id,
            actor=current_user,
            request_id=_get_request_id(request),
            base_project_revision=payload.base_project_revision,
            refresh_requirement_ids=payload.refresh_requirement_ids,
            request_id_explicit=bool(request.headers.get("X-Request-ID")),
            memory_bypass=request.headers.get("X-Knowledge-Bypass", "").lower() == "true",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc
