import logging
import time
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_cors_allow_origins
from .auth.jwt_auth import get_current_user
from .observability.logging import bind_log_context, configure_logging, reset_log_context

from .routers.auth import router as auth_router
from .routers.automation import router as automation_router
from .routers.billing import router as billing_router
from .routers.export import router as export_router
from .routers.integrations_azure_devops import router as azure_devops_router
from .routers.integrations_jira import router as jira_router
from .routers.requirements import _build_grounded_context_from_enrich_input, router as requirements_router
from .routers.reports import router as reports_router
from .routers.testcases import router as testcases_router

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Agentic Test Case Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(reports_router)
app.include_router(requirements_router)
app.include_router(jira_router)
app.include_router(azure_devops_router)
app.include_router(export_router)
app.include_router(testcases_router)
app.include_router(automation_router)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    start_time = time.perf_counter()
    request.state.request_id = request_id
    context_token = bind_log_context(request_id=request_id, method=request.method, path=request.url.path)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http.request.completed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    except Exception:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.exception(
            "HTTP request failed",
            extra={
                "event": "http.request.failed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
                "duration_ms": duration_ms,
            },
        )
        raise
    finally:
        reset_log_context(context_token)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


