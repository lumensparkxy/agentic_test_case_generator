from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, QaProjectDetail
from ..contracts.project_sync import AcceptProjectFinding, ResumeProjectSync
from ..services import project_sync
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
        return {"changes": sorted(rows, key=lambda item: item["created_at"], reverse=True)}

    return await perform(read_history)


@router.post("/projects/{project_id}/changes/accept", response_model=QaProjectDetail)
async def accept(project_id: str, payload: AcceptProjectFinding, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await perform(project_sync.accept_finding, project_id, actor, payload, feedback_request_id(request))


@router.post("/projects/{project_id}/changes/resume", response_model=QaProjectDetail)
async def resume(project_id: str, payload: ResumeProjectSync, actor: AuthUser = Depends(get_current_user)):
    return await perform(project_sync.resume_sync, project_id, actor, payload.change_id)
