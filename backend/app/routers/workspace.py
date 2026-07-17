import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, WorkspaceSummaryResponse
from ..services.workspace_summary_service import get_workspace_summary


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/workspace/summary", response_model=WorkspaceSummaryResponse)
async def workspace_summary(
    include_archived: bool = False,
    projects_limit: int = Query(default=20, ge=1, le=50),
    work_items_limit: int = Query(default=50, ge=1, le=50),
    runs_limit: int = Query(default=20, ge=1, le=50),
    reports_limit: int = Query(default=20, ge=1, le=50),
    current_user: AuthUser = Depends(get_current_user),
) -> WorkspaceSummaryResponse:
    try:
        return await run_in_threadpool(
            get_workspace_summary,
            actor=current_user,
            include_archived=include_archived,
            projects_limit=projects_limit,
            work_items_limit=work_items_limit,
            runs_limit=runs_limit,
            reports_limit=reports_limit,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Workspace summary load failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace summary is unavailable",
        ) from exc


__all__ = ["router"]
