# External Integrations

This reference lists external systems, local runtimes, and persistence paths
used by the tracked source.

## 1) Integration Inventory

| System | Type | Purpose | Auth model | Criticality | Evidence |
|--------|------|---------|------------|-------------|----------|
| Gemini API through Google ADK / GenAI SDK | External AI API | Requirement extraction, requirement analysis, test-case generation, review/refinement, automation POM generation | `GEMINI_API_KEY` normalized to `GOOGLE_API_KEY` | High | `backend/app/config.py`, `backend/app/adk_client.py`, `backend/app/agents/test_case_agent.py`, `backend/app/agents/automation_agent.py` |
| Firebase Authentication | External identity provider | Frontend sign-in and backend Firebase ID token verification | Firebase ID token bearer token | High | `frontend/src/firebase.js`, `backend/app/auth/firebase_auth.py`, `backend/app/services/firebase_admin.py` |
| Backend-issued JWT | Local/test auth compatibility path | Legacy/local API token support and E2E helper token minting; accepted only behind `AUTH_TOKEN_MODE=firebase-or-backend-jwt` | `JWT_SECRET_KEY`, `JWT_ALGORITHM` | Medium | `backend/app/auth/jwt_auth.py`, `scripts/e2e_playwright_workflow.py`, `docs/production-auth-policy-decision.md` |
| Google Identity credential verification | Local/test compatibility identity path | `/auth/google/login` exchanges Google credential for backend JWT only in compatibility mode | Google ID token verified against allowed audiences | Medium | `backend/app/auth/google_auth.py`, `backend/app/routers/auth.py`, `docs/production-auth-policy-decision.md` |
| Firestore | External data store | Audit events, workflow runs, optional audit dead-letter summaries, version records, connection records, billing repository, reporting data | Firebase Admin SDK credentials | High | `backend/app/services/firebase_admin.py`, `backend/app/services/firestore_repository.py`, `backend/app/services/audit_repository.py`, `backend/app/services/versioning_service.py`, `backend/app/services/billing_repository.py`, `backend/app/services/usage_event_repository.py` |
| JIRA Cloud | External API | Store user JIRA connection, import requirements, sync managed requirement blocks, export tests placeholder | User email plus API token, token encrypted before storage | High | `backend/app/adapters/jira.py`, `backend/app/services/jira_connection_service.py`, `backend/app/services/jira_requirements_service.py`, `backend/app/services/jira_sync_service.py`, `backend/app/routers/integrations_jira.py` |
| Azure DevOps Services | External API | Store user Azure DevOps connection, import work items, sync managed requirement blocks | Personal Access Token, encrypted before storage | High | `backend/app/adapters/azure_devops.py`, `backend/app/services/azure_devops_connection_service.py`, `backend/app/services/azure_devops_requirements_service.py`, `backend/app/services/azure_devops_sync_service.py`, `backend/app/routers/integrations_azure_devops.py` |
| Remote artifact URLs | External HTTP(S) resources | Ground app/prototype/diagram/image links into UI/API/workflow context | Public unauthenticated HTTP(S), textual content only, no embedded credentials | Medium | `backend/app/services/artifact_fetcher.py`, `backend/app/services/context_grounding.py`, `backend/app/routers/requirements.py`, `docs/artifact-fetching-threat-model.md` |
| Playwright Test execution runtime | Local subprocess/runtime | Convert executable candidates into generated specs, run selected cases, collect reports | Local process, environment config | High for automation feature | `backend/app/services/execution_service.py`, `backend/plain_english_test_framework/local_runner.py`, `backend/execution_runtime/playwright.config.ts` |
| Cloud Run / Artifact Registry / Secret Manager | Deployment platform | Deploy backend and frontend containers to managed infrastructure | `gcloud` credentials and Secret Manager | Medium | `scripts/deploy_cloud_run.sh`, `backend/Dockerfile`, `frontend/Dockerfile` |
| Prometheus-compatible metrics | Observability endpoint | Expose request, workflow, fallback, audit failure, audit dead-letter sink, and integration request counters/durations when enabled | `METRICS_ENABLED`, optional bearer `METRICS_ACCESS_TOKEN`, deployment perimeter | Medium | `backend/app/main.py`, `backend/app/observability/metrics.py`, `backend/app/observability/integrations.py`, `scripts/deploy_cloud_run.sh` |
| OpenTelemetry | Optional tracing | FastAPI tracing and trace ID propagation | `OTEL_*` environment variables | Medium | `backend/app/observability/tracing.py`, `backend/requirements.txt` |

## 2) Data Stores

| Store | Role | Access layer | Key risk | Evidence |
|-------|------|--------------|----------|----------|
| Firestore | Current transitional store for workflow/audit/version/reporting/billing/integration metadata | `backend/app/services/firestore_repository.py` plus domain repositories/services | Runtime behavior depends on Firebase credentials; several services degrade or warn when Firestore is unavailable | `backend/app/services/*.py`, `backend/tests/test_persistence_boundaries.py`, `backend/tests/test_reporting_service.py`, `docs/persistence-target-decision.md` |
| PostgreSQL | Accepted target for compliance-grade audit, billing ledger, reporting, and versioned artifacts | Future adapters behind the repository boundaries introduced for #49 | Not implemented yet; requires schema, migrations, idempotency, transaction tests, and migration planning | `docs/persistence-target-decision.md`, `docs/firebase-auth-audit-architecture.md` |
| In-memory process state | Fallback/dead-letter and local billing repository behavior in selected paths | Service module globals and test fakes | Local audit dead-letter summaries are not durable unless `AUDIT_DEAD_LETTER_BACKEND=firestore` is enabled | `backend/app/services/audit_service.py`, `backend/app/services/billing_repository.py` |
| Browser localStorage | Frontend access token and user session persistence | `frontend/src/App.jsx`, `frontend/src/constants/workflow.js` | XSS would expose token; current MVP stores token client-side | `frontend/src/App.jsx`, `README.md` |
| Local filesystem | Execution artifacts, exported E2E outputs, generated client outputs | `backend/app/services/execution_service.py`, scripts under `scripts/` | Local artifacts can grow and leak data if ignored boundaries are bypassed | `.gitignore`, `scripts/e2e_playwright_workflow.py`, `scripts/cleanup_generated_artifacts.py`, `docs/artifact-retention-policy.md`, `docs/client-submission-workflow.md` |

## 3) Secrets and Credentials Handling

Credential sources:

- `.env` and environment variables for local development.
- `.env.example` as the documented template.
- Secret Manager through `scripts/deploy_cloud_run.sh` for Cloud Run deployment.
- Firebase Admin credentials through `FIREBASE_SERVICE_ACCOUNT_JSON` or
  `GOOGLE_APPLICATION_CREDENTIALS`.
- User JIRA API tokens and Azure DevOps PATs are submitted through protected
  endpoints and encrypted before Firestore storage with non-secret key metadata.

Hardcoding checks:

- `scripts/azure_devops_smoke.py` includes a default organization URL for a
  smoke helper, but no PAT is hardcoded.
- `.env.example` contains placeholders, not real secrets.
- Generated outputs and local `.env` files are ignored by `.gitignore`.

Rotation/lifecycle notes:

- `docs/credential-rotation-runbook.md` documents per-user JIRA API token and
  Azure DevOps PAT rotation, `JIRA_CONNECTION_SECRET_KEY`,
  `AZURE_DEVOPS_CONNECTION_SECRET_KEY`, `JWT_SECRET_KEY`, `GEMINI_API_KEY`,
  Firebase Admin credential, metrics token, and Cloud Run Secret Manager
  rotation steps.
- JIRA/Azure DevOps connection services encrypt new writes with a primary
  connection secret, can read records encrypted with configured previous keys
  during a rotation window, and expose
  `scripts/reencrypt_integration_credentials.py` to re-encrypt records with the
  primary key before previous keys are removed.
- `scripts/deploy_cloud_run.sh` creates new Secret Manager versions for managed
  secrets and now includes optional dedicated JIRA/Azure DevOps connection
  encryption-key secrets plus previous-key rotation secrets when those values
  are set.
- `docs/production-auth-policy-decision.md` defines Firebase ID tokens as the
  production protected-endpoint token type and backend JWTs as local/test
  compatibility tokens.

## 4) Reliability and Failure Behavior

- Agent workflows include parser diagnostics, retryable parser failure checks,
  deterministic fallback output, and public workflow diagnostics.
- Artifact fetching blocks non-HTTP(S), embedded credentials, loopback, local,
  private, reserved, multicast, unresolved, and DNS-to-private hosts before
  fetch.
- Artifact fetching has request timeout, byte limit, redirect limit,
  unsupported-content rejection, and partial failure behavior. Authenticated or
  internal artifact fetching is out of scope without a separate allow-list or
  proxy design.
- Audit writes use bounded retry settings and sanitized local dead-letter
  summaries. Compliance deployments can set
  `AUDIT_DEAD_LETTER_BACKEND=firestore` to also write the same sanitized
  summary shape to the `AUDIT_DEAD_LETTER_COLLECTION` Firestore collection.
- JIRA and Azure DevOps connection services encrypt tokens and surface status
  summaries with token hints and non-secret encryption-key metadata.
- Execution preview classifies candidates as executable, manual, unsupported,
  or invalid before any subprocess run.
- Execution run limits cases per request by `EXECUTION_MAX_CASES_PER_REQUEST`.
- Billing shadow mode defaults to true and can record usage without hard
  blocking.

## 5) Observability for Integrations

- Request middleware logs completed and failed HTTP requests with request ID,
  trace ID, method, path, status, and duration.
- Agent workflow logs carry request/workflow/user/operation context.
- JIRA and Azure DevOps adapters record provider request success/failure counts
  and durations with low-cardinality `provider`, `operation`, and `status`
  labels, and emit safe structured logs without provider URLs, issue keys,
  work-item IDs, user emails, raw query text, or secrets.
- `/metrics` exposes Prometheus-compatible counters and durations through
  `backend/app/observability/metrics.py` when `METRICS_ENABLED=true`. Cloud Run
  deployments default it off and require `METRICS_ACCESS_TOKEN` if enabled.
- Optional OpenTelemetry tracing is configured through
  `backend/app/observability/tracing.py`.

Missing visibility gaps:

- [TODO] Broader explicit instrumentation for non-agent admin/auth/reporting
  flows is still listed as not fully implemented in the observability feature
  document.

## 6) Evidence

- `backend/app/config.py`
- `backend/app/adk_client.py`
- `backend/app/agents/test_case_agent.py`
- `backend/app/auth/firebase_auth.py`
- `backend/app/auth/jwt_auth.py`
- `backend/app/auth/google_auth.py`
- `backend/app/services/firebase_admin.py`
- `backend/app/services/firestore_repository.py`
- `backend/app/services/audit_repository.py`
- `backend/app/services/usage_event_repository.py`
- `backend/app/services/artifact_fetcher.py`
- `backend/app/services/context_grounding.py`
- `backend/app/services/credential_crypto.py`
- `docs/artifact-fetching-threat-model.md`
- `backend/app/services/audit_service.py`
- `backend/app/services/billing_repository.py`
- `backend/app/observability/integrations.py`
- `backend/app/observability/metrics.py`
- `backend/app/adapters/jira.py`
- `backend/app/adapters/azure_devops.py`
- `backend/execution_runtime/playwright.config.ts`
- `scripts/deploy_cloud_run.sh`
- `scripts/reencrypt_integration_credentials.py`
- `docs/credential-rotation-runbook.md`
- `docs/observability-logging-tracing-feature.md`
