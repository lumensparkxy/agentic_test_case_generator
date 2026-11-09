from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, QaProjectDetail
from ..contracts.project_sync import (
    AcceptProjectFinding,
    ProjectSyncAdmissionReleaseInput,
    ProjectSyncAdmissionReleaseResult,
    ProjectSyncAdmissionStatus,
    ProjectSyncRebindInput,
    ProjectSyncRebindReceipt,
    ResumeProjectSync,
)
from ..services import project_draft_admission as admission
from ..services import project_sync, project_sync_rebind
from ..services.workflow_project_service import project_error_to_http
from .knowledge import feedback_request_id

router = APIRouter()


async def perform(function, *args):
    try:
        return await run_in_threadpool(function, *args)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


@router.get("/projects/{project_id}/changes")
async def history(project_id: str, actor: AuthUser = Depends(get_current_user)):
    def read_history():
        from ..services import workflow_project_service as projects

        project = projects.get_project(project_id, actor=actor)
        rows = projects._stream_collection(projects._get_project_doc(project_id).collection("project_changes"))
        for row in rows:
            if not project.project_sync or row["id"] != project.project_sync["id"]:
                row["status"] = "superseded"
        return {"changes": [project_sync_rebind.public_sync_record(row) for row in sorted(rows, key=lambda item: item["created_at"], reverse=True)]}

    return await perform(read_history)


@router.post("/projects/{project_id}/changes/accept", response_model=QaProjectDetail)
async def accept(project_id: str, payload: AcceptProjectFinding, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await perform(project_sync.accept_finding, project_id, actor, payload, feedback_request_id(request))


@router.post("/projects/{project_id}/changes/resume", response_model=QaProjectDetail)
async def resume(project_id: str, payload: ResumeProjectSync, request: Request, actor: AuthUser = Depends(get_current_user)):
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    return await perform(project_sync.resume_sync, project_id, actor, payload.change_id, request_id)


@router.post("/projects/{project_id}/changes/rebind-drafts", response_model=ProjectSyncRebindReceipt)
async def rebind_drafts(project_id: str, payload: ProjectSyncRebindInput, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await perform(project_sync_rebind.rebind_sync_drafts, project_id, actor, payload, feedback_request_id(request))


@router.get("/projects/{project_id}/changes/admission", response_model=ProjectSyncAdmissionStatus, response_model_exclude_unset=True)
async def read_sync_admission(
    project_id: str, request_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None, actor: AuthUser = Depends(get_current_user)
):
    def status():
        receipt = project_sync_rebind.read_sync_rebind_receipt(project_id, actor, request_id) if request_id else None
        extras = {"rebind_receipt": receipt} if request_id else {}
        client = admission.draft_admission_client(read_only=True)
        if client is None:
            return ProjectSyncAdmissionStatus(enabled=False, blocked=False, attempt=None, **extras)
        current = admission.read_project_sync_admission(client, project_id, actor.sub)
        rebind = project_sync_rebind.sync_rebind_status(client, project_id, actor)
        if rebind is not None:
            extras["rebind"] = rebind
        return ProjectSyncAdmissionStatus(enabled=True, **current, **extras)

    return await perform(status)


@router.post("/projects/{project_id}/changes/admission/{operation_id}/release", response_model=ProjectSyncAdmissionReleaseResult)
async def release_sync_admission(
    project_id: str,
    operation_id: Annotated[str, Path(pattern=r"^draft_[0-9a-f]{64}$")],
    payload: ProjectSyncAdmissionReleaseInput,
    actor: AuthUser = Depends(get_current_user),
):
    def release():
        client = admission.draft_admission_client()
        if client is None:
            raise HTTPException(409, "Project Sync recovery is disabled in this environment.")
        admission.release_draft_attempt(client, project_id, actor.sub, operation_id, payload.fence, project_sync=True)
        return ProjectSyncAdmissionReleaseResult()

    return await perform(release)
