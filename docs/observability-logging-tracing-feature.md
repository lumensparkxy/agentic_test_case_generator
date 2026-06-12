# Feature: Production Observability, Logging, and Distributed Tracing

Date: 2026-05-10
Status: In Progress
Owner: TBD
Area: Backend, Frontend, Platform, AI Workflow Operations

## Implementation Progress

- Phase 1 implemented: structured JSON backend logging, request completion/failure logs, request-context binding, and automatic frontend `X-Request-ID` injection through the shared API helper.
- Phase 2 core workflow propagation implemented: `request_id`, `workflow_run_id`, `actor_user_id`, and `operation` now flow into requirement/test-case ADK workflow logging for direct uploads, refinements, JIRA imports, and Azure DevOps imports.
- Phase 3 implemented: Prometheus-compatible `/metrics` endpoint now exposes HTTP request counts/durations, workflow run counts/durations, agent fallback counts, audit write failure counts, and JIRA/Azure DevOps integration request counts/durations. Exposure is controlled by `METRICS_ENABLED`; optional `METRICS_ACCESS_TOKEN` requires a bearer token, and Cloud Run deployments default metrics off unless explicitly token-protected.
- Phase 4 implemented: optional OpenTelemetry FastAPI tracing can be enabled with `OTEL_ENABLED=true`; incoming W3C `traceparent` IDs are surfaced as `trace_id` in request logs, `X-Trace-ID` response headers, and audit payloads.
- Phase 5 implemented: audit writes now use bounded retry settings and record exhausted failures into a sanitized local dead-letter buffer with retry/dead-letter metrics.
- Not yet implemented: broader explicit instrumentation for non-agent admin/auth/reporting flows and a durable external dead-letter queue for compliance deployments.

## Summary

Introduce a production-grade observability layer for the Agentic Test Case Generator so every user-facing workflow can be correlated across frontend requests, FastAPI endpoints, agent orchestration, external integrations, persistence, billing, and audit records.

The current system has a strong audit foundation with `request_id`, `workflow_run_id`, usage events, and artifact version links. This feature upgrades that foundation into a consistent logging and tracing mechanism with structured request logs, automatic request correlation, agent workflow trace context, operational metrics, and optional distributed tracing via OpenTelemetry.

## Problem Statement

Today, the application can record core workflow audit events, but operational debugging still requires manual stitching across:

- Frontend API calls
- FastAPI request handling
- ADK/Gemini agent workflow logs
- Firestore audit writes
- Billing ledger/consumption records
- JIRA/Azure DevOps integration calls
- Export and automation workflows

Key gaps:

1. Logging is not centrally configured as structured JSON.
2. Not every request receives a frontend-generated `X-Request-ID`.
3. Backend request logs do not consistently include method, path, status, duration, actor, and request ID.
4. Agent workflow logs include session/user context but not consistently `request_id` or `workflow_run_id`.
5. There is no distributed tracing framework such as OpenTelemetry.
6. Metrics expose workflow latency, failure rate, fallback usage, audit write failures, and JIRA/Azure DevOps integration request outcomes/durations.
7. Audit persistence is best-effort; failed audit writes are only warned about, with no retry/dead-letter path.

## Goals

- Provide consistent request correlation across frontend, backend, agents, audit events, billing, and integrations.
- Emit structured JSON logs suitable for Cloud Logging, Datadog, Grafana Loki, ELK, or similar platforms.
- Add request/response logging middleware with safe metadata only.
- Propagate trace context into agent workflow logs and durable audit records.
- Add optional OpenTelemetry tracing for FastAPI and outbound service calls.
- Add operational metrics for latency, volume, errors, fallback behavior, and workflow outcomes.
- Preserve user privacy by avoiding raw document/test content in logs.
- Keep audit logging durable and non-blocking while surfacing audit-write failures clearly.

## Non-Goals

- Do not log raw uploaded documents, generated full test cases, secrets, API tokens, or Firebase/JWT credentials.
- Do not replace the existing `workflow_runs`, `usage_events`, or versioning records.
- Do not require a specific vendor such as Datadog, New Relic, or Sentry for the first implementation.
- Do not block user workflows if optional telemetry exporters are unavailable.

## Current State

Already implemented:

- `backend/app/main.py` adds/returns `X-Request-ID`.
- `backend/app/services/audit_service.py` records `workflow_runs` and `usage_events`.
- `backend/app/services/versioning_service.py` links artifacts to `request_id`, `workflow_run_id`, and `source_event_id`.
- Core workflow routers record workflow starts/completions/failures.
- Agent code emits workflow progress logs for sessions, events, reviews, stalls, and fallbacks.
- Tests exist for request ID middleware, audit service behavior, workflow audit hooks, and config log filtering.

Current limitations:

- Some routes, especially auth/reporting/admin reads, have thinner explicit instrumentation
  than the core generation and integration workflows.
- Compliance deployments still need a durable external dead-letter queue instead of
  only the sanitized local dead-letter buffer.
- Production metrics scraping remains deployment-specific; Cloud Run disables
  `/metrics` by default unless it is intentionally token-protected or placed behind
  an approved private monitoring path.

## Proposed Solution

### 1. Centralized structured logging

Add a backend logging module, for example `backend/app/observability/logging.py`, that configures JSON logs with consistent fields:

- `timestamp`
- `level`
- `logger`
- `message`
- `request_id`
- `trace_id`
- `span_id`
- `workflow_run_id`
- `actor_user_id`
- `operation`
- `path`
- `method`
- `status_code`
- `duration_ms`
- `environment`
- `service_name`
- `service_version`

Configuration should support environment variables:

- `LOG_LEVEL`, default `INFO`
- `LOG_FORMAT`, default `json`, optional `text` for local development
- `SERVICE_NAME`, default `agentic-test-case-generator-api`
- `SERVICE_VERSION`, default from package/build metadata or `dev`
- `ENVIRONMENT`, default `local`

### 2. Request logging middleware

Enhance request middleware so each request logs one structured completion event.

Required behavior:

- Preserve incoming `X-Request-ID` or generate one.
- Add `X-Request-ID` to every response.
- Capture start time and duration.
- Log method, route/path, status code, duration, actor if available, and request ID.
- Log unhandled exceptions with `logging.exception` while preserving request context.
- Never log request/response bodies by default.

Suggested event name:

- `http.request.completed`
- `http.request.failed`

### 3. Frontend request correlation

Update `frontend/src/App.jsx` shared `apiRequest` helper so every API request automatically includes `X-Request-ID` unless one is explicitly provided.

Required behavior:

- Generate via existing `createRequestId()` helper.
- Preserve caller-provided IDs for long-running workflows.
- Read response `X-Request-ID` and include it in user-facing error details where useful.
- Avoid duplicating ad hoc request ID logic throughout the app.

### 4. Workflow and agent context propagation

Pass `request_id` and `workflow_run_id` into agent orchestration functions and workflow log helpers.

Target areas:

- `backend/app/adk_client.py`
- `backend/app/agents/requirements_agent.py`
- `backend/app/agents/test_case_agent.py`
- `backend/app/agents/analysis_agent.py`
- `backend/app/agents/automation_agent.py`
- workflow routers under `backend/app/routers/`

Agent workflow logs should include:

- `request_id`
- `workflow_run_id`
- `agent_session_id`
- `actor_user_id`
- `operation`
- `iteration`
- `score`
- `approved`
- `fallback_used`
- `failure_reason`

### 5. Distributed tracing with OpenTelemetry

Add optional OpenTelemetry support.

Recommended instrumentation:

- FastAPI/Starlette request tracing
- HTTP client tracing for outbound JIRA/Azure DevOps/artifact fetch calls
- Firestore operation spans where practical
- ADK/Gemini workflow spans around long-running agent stages

Environment variables:

- `OTEL_ENABLED`, default `false`
- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_SERVICE_NAME`
- `OTEL_RESOURCE_ATTRIBUTES`

Trace propagation:

- Accept and forward W3C `traceparent` where possible.
- Include `trace_id` in structured logs and audit metadata.
- Include `trace_id` in `workflow_runs` and `usage_events` when available.

### 6. Metrics

Expose operational metrics through a `/metrics` endpoint or compatible exporter.
The endpoint must be scoped intentionally: local development can leave
`METRICS_ENABLED=true`, while production should either disable it, protect it
with `METRICS_ACCESS_TOKEN`, or place it behind an approved private monitoring
path.

Recommended metrics:

- `http_requests_total`
- `http_request_duration_seconds`
- `workflow_runs_total{operation,status}`
- `workflow_duration_seconds{operation}`
- `agent_iterations_total{workflow,agent}`
- `agent_fallbacks_total{workflow,reason}`
- `audit_write_failures_total{collection,operation}`
- `integration_requests_total{provider,operation,status}`
- `integration_request_duration_seconds{provider,operation,status}`
- `billing_consumption_total{billing_key,status}`

### 7. Audit reliability improvements

Keep audit writes non-blocking, but make failures observable.

Options:

- Count audit write failures in metrics.
- Log audit failures with structured fields.
- Add retry with bounded backoff for transient Firestore failures.
- Add a future dead-letter queue for compliance-critical deployments.

## User Stories

### Operations engineer

As an operations engineer, I want to search by `request_id` and see all logs, audit records, workflow runs, billing entries, and agent events for a user workflow so I can debug incidents quickly.

### Product/admin user

As an admin user, I want usage reports and audit trails to be linked to concrete requests and workflow runs so I can explain generated artifacts and billing consumption.

### Developer

As a developer, I want structured logs and traces around agent loops, fallback behavior, and external integrations so I can diagnose failures without reproducing them locally.

### Security/compliance reviewer

As a compliance reviewer, I want audit records to preserve metadata and traceability without logging sensitive content so the system is explainable while respecting privacy.

## Acceptance Criteria

### Request correlation

- Every backend response includes `X-Request-ID`.
- Every frontend API call made through `apiRequest` sends an `X-Request-ID`.
- Caller-provided request IDs are preserved.
- Request ID middleware tests cover generated and incoming IDs.

### Structured logging

- Backend logs are emitted as JSON when `LOG_FORMAT=json`.
- Logs include `request_id` for request-scoped logs.
- One completion log is emitted for every HTTP request.
- Unhandled exceptions include request context and stack traces.
- Logs do not include raw document contents, tokens, passwords, or secrets.

### Workflow correlation

- `workflow_runs` include `request_id` and, when tracing is enabled, `trace_id`.
- `usage_events` include `request_id`, `workflow_run_id`, and, when tracing is enabled, `trace_id`.
- Agent workflow logs include `request_id` and `workflow_run_id`.
- Generated requirement and test-case version records retain `source_event_id`, `request_id`, and `workflow_run_id`.

### Tracing

- When `OTEL_ENABLED=false`, the app runs with no tracing exporter dependency at runtime.
- When `OTEL_ENABLED=true`, FastAPI requests create spans.
- Outbound integration calls are traceable where supported.
- Trace IDs are included in structured logs.

### Metrics

- A metrics endpoint/exporter reports HTTP request count and duration.
- Workflow success/failure counts are emitted by operation.
- Agent fallback count is emitted by workflow/reason.
- Audit write failure count is emitted by collection/operation.

### Tests

- Existing audit and request middleware tests continue to pass.
- New tests cover structured request logging fields.
- New tests cover frontend automatic request ID injection.
- New tests cover agent log context propagation.
- New tests cover disabled/enabled OpenTelemetry configuration behavior.

## Implementation Plan

### Phase 1 — Consistent request correlation and structured logs

Files likely touched:

- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/observability/logging.py` new
- `backend/tests/test_request_middleware.py`
- `backend/tests/test_observability_logging.py` new
- `frontend/src/App.jsx`
- `frontend/src/services/apiClient.js`
- frontend unit/e2e tests if available

Tasks:

1. Add logging configuration module.
2. Add request context support using `contextvars`.
3. Enhance middleware to emit structured completion/failure logs.
4. Ensure frontend `apiRequest` injects `X-Request-ID` by default.
5. Add tests for generated/preserved request IDs and structured fields.

### Phase 2 — Workflow and agent context propagation

Files likely touched:

- `backend/app/routers/requirements.py`
- `backend/app/routers/testcases.py`
- `backend/app/routers/export.py`
- `backend/app/routers/automation.py`
- `backend/app/routers/integrations_jira.py`
- `backend/app/routers/integrations_azure_devops.py`
- `backend/app/adk_client.py`
- `backend/app/agents/*.py`
- `backend/tests/test_main_audit_logging.py`
- agent-specific tests

Tasks:

1. Pass `request_id` and `workflow_run_id` to workflow/agent functions.
2. Include these IDs in workflow log helpers.
3. Add missing instrumentation for auth/admin/reporting routes where appropriate.
4. Extend tests to assert workflow context propagation.

### Phase 3 — Metrics

Files likely touched:

- `backend/app/observability/metrics.py` new
- `backend/app/main.py`
- `backend/app/services/audit_service.py`
- workflow routers and agent fallback paths
- `backend/requirements.txt`

Tasks:

1. Add Prometheus-compatible metrics support.
2. Expose `/metrics`, gated by `METRICS_ENABLED` and optional bearer-token
   protection through `METRICS_ACCESS_TOKEN`.
3. Count HTTP requests/durations.
4. Count workflow outcomes and fallback paths.
5. Count audit write failures.

### Phase 4 — Optional OpenTelemetry tracing

Files likely touched:

- `backend/app/observability/tracing.py` new
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/requirements.txt`
- integration services/adapters

Tasks:

1. Add OpenTelemetry dependencies.
2. Initialize tracing only when `OTEL_ENABLED=true`.
3. Instrument FastAPI and supported HTTP clients.
4. Add custom spans around long-running ADK/agent stages.
5. Include `trace_id` in logs and audit metadata.

### Phase 5 — Audit reliability hardening

Files likely touched:

- `backend/app/services/audit_service.py`
- `backend/app/services/versioning_service.py`
- `backend/app/observability/metrics.py`
- tests for transient failure handling

Tasks:

1. Add structured audit failure logs.
2. Add bounded retry for transient audit persistence failures.
3. Emit metrics for skipped/failed audit writes.
4. Define future dead-letter queue approach for compliance deployments.

## Privacy and Security Requirements

- Never log access tokens, refresh tokens, API keys, Authorization headers, cookies, or Firebase credentials.
- Never log raw uploaded file text by default.
- Never log complete generated test-case bodies by default.
- Log counts, IDs, status, timing, and metadata only.
- Redact sensitive query parameters if request URLs are logged.
- Ensure CORS and auth behavior are unchanged.

## Suggested Event Names

HTTP events:

- `http.request.started`
- `http.request.completed`
- `http.request.failed`

Workflow events:

- `workflow.started`
- `workflow.completed`
- `workflow.failed`
- `workflow.billing_recorded`

Agent events:

- `agent.session_started`
- `agent.event_received`
- `agent.review_iteration`
- `agent.review_stalled`
- `agent.fallback_used`
- `agent.best_artifact_retained`
- `agent.completed`
- `agent.failed`

Audit events:

- `audit.workflow_run.started`
- `audit.workflow_run.completed`
- `audit.usage_event.recorded`
- `audit.write_failed`

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Sensitive data accidentally logged | Use allowlisted log fields only; add tests for redaction. |
| Logs become too noisy | Use event names, log levels, and sampling for high-volume debug events. |
| Tracing exporter outage affects app | Keep telemetry optional and non-blocking. |
| Audit retry delays request latency | Use small bounded retries or async/deferred retries in later phase. |
| Context propagation adds wide code changes | Phase implementation and preserve existing function defaults. |

## Rollout Plan

1. Enable structured text logs locally for developer validation.
2. Enable JSON logs in staging.
3. Verify request IDs across frontend/backend/audit records.
4. Add metrics in staging and validate dashboards.
5. Enable OpenTelemetry in staging with sampling.
6. Roll out to production behind environment flags.
7. Review logs for privacy before increasing telemetry volume.

## Definition of Done

- A developer can take a failing `request_id` and find the request log, workflow run, usage event, billing record, and agent workflow logs.
- Every core workflow has correlated request, workflow, actor, and operation metadata.
- Logs are structured and safe for centralized aggregation.
- Metrics show request volume, workflow success/failure rates, latency, fallback counts, audit write failures, and JIRA/Azure DevOps integration request outcomes and durations.
- Tracing can be enabled without code changes using environment variables.
- Existing backend tests pass, and new observability tests cover the implemented behavior.
