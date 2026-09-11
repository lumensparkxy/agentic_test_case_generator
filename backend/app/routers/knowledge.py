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
