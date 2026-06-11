from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from ..auth.authorization import require_org_admin
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, UsageReportResponse
from ..services.reporting_service import build_usage_report

router = APIRouter()


def _validate_usage_report_range(start_at: Optional[datetime], end_at: Optional[datetime]) -> None:
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="start_at must be earlier than or equal to end_at")


@router.get("/reports/usage/me", response_model=UsageReportResponse)
async def usage_report_me(
    current_user: AuthUser = Depends(get_current_user),
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> UsageReportResponse:
    _validate_usage_report_range(start_at, end_at)

    return await run_in_threadpool(
        build_usage_report,
        start_at=start_at,
        end_at=end_at,
        current_user=current_user,
        scope="self",
    )


@router.get("/reports/usage/org", response_model=UsageReportResponse)
async def usage_report_org(
    current_user: AuthUser = Depends(require_org_admin),
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> UsageReportResponse:
    _validate_usage_report_range(start_at, end_at)

    return await run_in_threadpool(
        build_usage_report,
        start_at=start_at,
        end_at=end_at,
        current_user=current_user,
        scope="organization",
    )


@router.get("/reports/usage", response_model=UsageReportResponse, deprecated=True)
async def usage_report(
    current_user: AuthUser = Depends(get_current_user),
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> UsageReportResponse:
    return await usage_report_me(current_user=current_user, start_at=start_at, end_at=end_at)
