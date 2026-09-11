from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from ..auth.jwt_auth import get_current_user
from ..contracts.knowledge import KnowledgeCollection, KnowledgeEntry, KnowledgeMutation
from ..models import AuthUser
from ..services.guidance_service import skill_catalog
from ..services.knowledge_service import list_knowledge, mutate_knowledge

router = APIRouter()


async def guarded(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Knowledge is unavailable. Retry; no changes were confirmed.") from exc


@router.get("/skills")
async def skills(actor: AuthUser = Depends(get_current_user)):
    return await guarded(skill_catalog)


@router.get("/projects/{project_id}/knowledge", response_model=KnowledgeCollection)
async def project_knowledge(project_id: str, actor: AuthUser = Depends(get_current_user)):
    return await guarded(list_knowledge, actor, project_id)


@router.get("/me/knowledge", response_model=KnowledgeCollection)
async def personal_knowledge(actor: AuthUser = Depends(get_current_user)):
    return await guarded(list_knowledge, actor)


async def mutate(request, actor, project_id, payload):
    request_id = request.headers.get("X-Request-ID")
    if not request_id or len(request_id) > 160:
        raise HTTPException(422, "A bounded X-Request-ID is required for knowledge mutations")
    return await guarded(mutate_knowledge, actor, project_id, payload, request_id)


@router.post("/projects/{project_id}/knowledge", response_model=KnowledgeEntry)
async def change_project_knowledge(project_id: str, payload: KnowledgeMutation, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await mutate(request, actor, project_id, payload)


@router.post("/me/knowledge", response_model=KnowledgeEntry)
async def change_personal_knowledge(payload: KnowledgeMutation, request: Request, actor: AuthUser = Depends(get_current_user)):
    return await mutate(request, actor, None, payload)


@router.get("/projects/{project_id}/knowledge/{entry_id}/versions/{revision}")
async def read_knowledge_version(project_id: str, entry_id: str, revision: int, actor: AuthUser = Depends(get_current_user)):
    from ..services.knowledge_service import knowledge_version

    return await guarded(knowledge_version, actor, project_id, entry_id, revision)


from pydantic import BaseModel, Field
from ..contracts.guidance import GuidanceStage


class FeedbackRetry(BaseModel):
    stage: GuidanceStage
    feedback: str = Field(min_length=1, max_length=1000)
    source_request_id: str = Field(min_length=1, max_length=160)


@router.post("/projects/{project_id}/knowledge/suggestions/retry")
async def retry_knowledge_feedback(project_id: str, payload: FeedbackRetry, actor: AuthUser = Depends(get_current_user)):
    from ..services.knowledge_feedback import retry_feedback

    return await guarded(retry_feedback, actor, project_id, payload.stage, payload.feedback, payload.source_request_id)
