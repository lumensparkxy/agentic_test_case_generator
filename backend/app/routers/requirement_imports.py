from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, QaProjectDetail
from ..contracts.requirement_imports import RequirementImportPreview, ImportApplyInput, ImportRecoveryInput, ImportHistoryEntry, RequirementReviewsInput
from ..services.requirement_import_service import repository, prepare_recovery, recompare_import

router = APIRouter()


@router.get("/projects/{project_id}/requirement-imports", response_model=list[RequirementImportPreview])
async def pending_imports(project_id: str, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(repository().list_pending, project_id, actor)


@router.post("/projects/{project_id}/requirement-imports/recovery", response_model=RequirementImportPreview)
async def recover_import(project_id: str, payload: ImportRecoveryInput, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(prepare_recovery, project_id, actor, payload)


@router.get("/projects/{project_id}/requirement-imports/{import_id}", response_model=RequirementImportPreview)
async def get_import(project_id: str, import_id: str, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(repository().read, project_id, import_id, actor)


@router.post("/projects/{project_id}/requirement-imports/{import_id}/apply", response_model=QaProjectDetail)
async def apply_import(project_id: str, import_id: str, payload: ImportApplyInput, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(repository().apply, project_id, import_id, actor, payload, request.state.request_id)


@router.post("/projects/{project_id}/requirement-imports/{import_id}/compare", response_model=RequirementImportPreview)
async def compare_import(project_id: str, import_id: str, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(recompare_import, project_id, import_id, actor)


@router.delete("/projects/{project_id}/requirement-imports/{import_id}", status_code=204)
async def cancel_import(project_id: str, import_id: str, actor: AuthUser = Depends(get_current_user)):
    await run_in_threadpool(repository().cancel, project_id, import_id, actor)
    return Response(status_code=204)


@router.get("/projects/{project_id}/requirements/{requirement_uid}/history", response_model=list[ImportHistoryEntry])
async def requirement_history(project_id: str, requirement_uid: str, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(repository().history, project_id, requirement_uid, actor)


@router.patch("/projects/{project_id}/requirements/reviews", response_model=QaProjectDetail)
async def review_requirements(project_id: str, payload: RequirementReviewsInput, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await run_in_threadpool(repository().review, project_id, actor, payload, request.state.request_id)
