import logging
import time
from uuid import uuid4
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import get_cors_allow_origins
from .auth.jwt_auth import get_current_user
from .observability.logging import bind_log_context, configure_logging, reset_log_context
from .observability.metrics import record_http_request, render_prometheus_metrics
from .observability.tracing import configure_tracing, resolve_trace_id

from .routers.auth import router as auth_router
from .routers.automation import router as automation_router
from .routers.billing import router as billing_router
from .routers.export import router as export_router
from .routers.integrations_azure_devops import router as azure_devops_router
from .routers.integrations_jira import router as jira_router
from .routers.requirements import _build_grounded_context_from_enrich_input, router as requirements_router
from .routers.reports import router as reports_router
from .routers.testcases import router as testcases_router
from .agents.automation_agent import generate_playwright_pom
from .services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from .services.execution_service import preview_execution, run_execution

configure_logging()
logger = logging.getLogger(__name__)
METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

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

configure_tracing(app)


def _request_metric_path(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", None) or request.url.path)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    trace_id = resolve_trace_id(request.headers.get("traceparent"))
    start_time = time.perf_counter()
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    context_token = bind_log_context(request_id=request_id, trace_id=trace_id, method=request.method, path=request.url.path)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        metric_path = _request_metric_path(request)
        record_http_request(
            method=request.method,
            path=metric_path,
            status_code=response.status_code,
            duration_seconds=duration_ms / 1000,
        )
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http.request.completed",
                "request_id": request_id,
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    except Exception:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        metric_path = _request_metric_path(request)
        record_http_request(
            method=request.method,
            path=metric_path,
            status_code=500,
            duration_seconds=duration_ms / 1000,
        )
        logger.exception(
            "HTTP request failed",
            extra={
                "event": "http.request.failed",
                "request_id": request_id,
                "trace_id": trace_id,
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


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=render_prometheus_metrics(), media_type=METRICS_CONTENT_TYPE)
