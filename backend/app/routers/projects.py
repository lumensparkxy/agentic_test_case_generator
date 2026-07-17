from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

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
from ..services.orchestrator_run_service import list_orchestrator_runs
from ..services.orchestrator_service import get_project_orchestrator_status
from ..services.use_case_review_service import review_use_case_snapshot, use_case_review_error_to_http
from ..services.workflow_project_service import (
    append_stage_snapshot,
    create_project,
    get_project,
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
            base_project_revision=payload.base_project_revision,
        )
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
            accepted_recommendation_ids=payload.accepted_recommendation_ids,
            base_project_revision=payload.base_project_revision,
        )
    except Exception as exc:
        raise impact_error_to_http(exc) from exc


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
            actor=current_user,
            request_id=_get_request_id(request),
        )
    except Exception as exc:
        raise use_case_review_error_to_http(exc) from exc
