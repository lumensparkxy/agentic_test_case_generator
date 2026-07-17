# Codebase Structure

This reference maps the tracked source tree and the boundaries maintainers
should preserve when changing the application.

## 1) Top-Level Map

| Path | Purpose | Evidence |
|------|---------|----------|
| `backend/` | FastAPI API, agents, services, auth, tests, execution framework, backend Dockerfile | `backend/app/main.py`, `backend/tests/`, `backend/Dockerfile` |
| `backend/app/` | Runtime application package | `backend/app/main.py` |
| `backend/app/routers/` | HTTP endpoint groups registered by `main.py` | `backend/app/main.py`, `backend/app/routers/*.py` |
| `backend/app/contracts/` | Domain-owned Pydantic request, response, and data contracts re-exported through the legacy `models.py` facade | `backend/app/contracts/*.py`, `backend/app/models.py` |
| `backend/app/agents/` | ADK/Gemini-facing workflow agents, specialist task contracts/registry, prompt helpers, bounded use-case planning, impact recommendation logic, test-case coverage/review/fallback helpers, and response hydration helpers | `backend/app/adk_client.py`, `backend/app/agents/specialist_contracts.py`, `backend/app/agents/specialist_registry.py`, `backend/app/agents/use_case_agent.py`, `backend/app/agents/test_case_agent.py`, `backend/app/agents/impact_update_agent.py` |
| `backend/app/services/` | Business services, persistence repository boundaries/adapters, orchestrator decisions and run persistence, impact update application, execution services, billing, reporting, grounding | `backend/app/services/*.py` |
| `backend/app/adapters/` | External API clients for JIRA and Azure DevOps | `backend/app/adapters/jira.py`, `backend/app/adapters/azure_devops.py` |
| `backend/app/auth/` | Firebase, Google credential, backend JWT, role, and authorization helpers | `backend/app/auth/*.py` |
| `backend/app/observability/` | Structured logging, Prometheus-style metrics, optional tracing | `backend/app/observability/*.py` |
| `backend/app/utils/` | Parsing, JSON recovery, requirement normalization, workflow diagnostics | `backend/app/utils/*.py` |
| `backend/plain_english_test_framework/` | Structured-English spec parser, compiler, IR validator, Playwright generator, and local runner | `backend/plain_english_test_framework/*.py` |
| `backend/execution_runtime/` | Node-based Playwright Test runtime used by backend execution endpoints | `backend/execution_runtime/package.json`, `backend/execution_runtime/playwright.config.ts` |
| `backend/tests/` | Backend unit and integration-style tests using `unittest` and local fakes/patches | `backend/tests/test_*.py` |
| `frontend/` | React/Vite web application, E2E specs, frontend Dockerfile, Nginx config | `frontend/src/main.jsx`, `frontend/package.json`, `frontend/Dockerfile` |
| `frontend/src/app/` | Pure route contracts plus global/project shell, navigation, loading, placeholder, and recovery components | `frontend/src/app/workflowRoutes.js`, `frontend/src/app/GlobalAppShell.jsx`, `frontend/src/app/RoutePages.jsx` |
| `frontend/src/pages/` | Route-level authenticated workspace experiences composed from feature components | `frontend/src/pages/HomePage.jsx`, `frontend/src/pages/ProjectsPage.jsx`, `frontend/src/pages/ReviewsPage.jsx`, `frontend/src/pages/UseCaseReviewPage.jsx` |
| `frontend/src/components/` | Focused UI components grouped by feature or layout | `frontend/src/components/**` |
| `frontend/src/api/` | Generated frontend API contract declarations and endpoint constants | `frontend/src/api/generated/api-contracts.d.ts`, `frontend/src/api/generated/api-contracts.js` |
| `frontend/src/services/` | Frontend API helper functions, bounded read-model clients, and review-decision transports | `frontend/src/services/apiClient.js`, `frontend/src/services/workspaceSummaryClient.js`, `frontend/src/services/useCaseReviewClient.js` |
| `frontend/src/hooks/` | Reusable React hooks, including domain workflow state hooks for projects, requirements, context, generation, integrations, execution, export, and app session state | `frontend/src/hooks/*.js` |
| `frontend/src/styles/` | Shared CSS entry point, design tokens, base rules, layout styles, and feature-owned selector modules | `frontend/src/styles/index.css`, `frontend/src/styles/*.css` |
| `frontend/src/utils/` | Pure frontend helpers | `frontend/src/utils/*.js` |
| `frontend/e2e/` | Playwright browser workflow tests and screenshot capture script | `frontend/e2e/*.js`, `frontend/e2e/*.mjs` |
| `schemas/` | JSON schemas for the plain-English spec and IR contracts | `schemas/spec.schema.json`, `schemas/ir.schema.json` |
| `scripts/` | Evaluation scripts, smoke/E2E scripts, deployment helper, integration credential re-encryption helper, payload fixtures, benchmark fixtures, and orchestrator lifecycle benchmark fixtures | `scripts/*.py`, `scripts/*.sh`, `scripts/api_payloads/`, `scripts/benchmark_orchestrator_*` |
| `docs/` | Planning, architecture, traceability, observability, client workflow, and codebase docs | `docs/*.md` |
| `.github/workflows/` | CI pipeline definitions | `.github/workflows/ci.yml` |
| `compose.yaml` | Local two-container app orchestration | `compose.yaml` |
| `.env.example` | Local environment template | `.env.example` |

## 2) Entry Points

- Main backend runtime entry: `backend/app/main.py`.
- Frontend runtime entry: `frontend/src/main.jsx`.
- Frontend orchestration component: `frontend/src/App.jsx`.
- Authenticated workspace pages: `frontend/src/pages/HomePage.jsx`,
  `frontend/src/pages/ProjectsPage.jsx`,
  `frontend/src/pages/ReviewsPage.jsx`, and
  `frontend/src/pages/UseCaseReviewPage.jsx`.
- Global Review Inbox projection:
  `frontend/src/components/reviews/ReviewInbox.jsx` and
  `frontend/src/styles/review-inbox.css`.
- Use Cases review state and decision transport:
  `frontend/src/hooks/useUseCaseReview.js` and
  `frontend/src/services/useCaseReviewClient.js`.
- Frontend workflow navigation shell:
  `frontend/src/components/layout/WorkflowNavigationDrawer.jsx`.
- Shared workflow/project status presentation:
  `frontend/src/components/workflow/StatusBadge.jsx`.
- Frontend route-scoped contextual task surface:
  `frontend/src/components/projects/OrchestratorCockpitPanel.jsx`,
  `frontend/src/components/projects/ContextualTaskCard.jsx`, and
  `frontend/src/components/projects/contextualTask.js`.
- Frontend project information rail:
  `frontend/src/components/projects/ProjectInformationRail.jsx`.
- Frontend style entry point: `frontend/src/styles/index.css`.
- Backend execution runtime config: `backend/execution_runtime/playwright.config.ts`.
- Backend use-case planning coordinator:
  `backend/app/agents/use_case_agent.py`.
- Frontend E2E config: `frontend/playwright.config.js`.
- Shared responsive E2E geometry and overflow assertions:
  `frontend/e2e/support/layout.js`.
- Backend container entry: `backend/Dockerfile`.
- Frontend container entry: `frontend/Dockerfile`.
- Local container entry: `compose.yaml`.
- Reproducible next-version E2E workflow: `scripts/e2e_playwright_workflow.py`.
- API smoke workflow: `scripts/run_api_smoke.sh` and `scripts/e2e_api_verify.py`.
- Offline orchestrator lifecycle evaluation:
  `scripts/evaluate_orchestrator.py`.
- OpenAPI export: `scripts/export_openapi.py`.
- Integration credential re-encryption: `scripts/reencrypt_integration_credentials.py`.

`backend/app/main.py` creates the FastAPI app, installs CORS and request
middleware, registers routers, configures tracing, exposes `/health`, and
exposes `/metrics`.

## 3) Module Boundaries

| Boundary | What belongs here | What must not be here |
|----------|-------------------|------------------------|
| `backend/app/routers/` | HTTP request parsing, FastAPI dependencies, workflow audit start/complete calls, response assembly | Low-level external API request code, raw SDK setup, large agent prompts |
| `backend/app/contracts/` | Domain-owned Pydantic models for requirements, grounding, auth, test cases, execution, impact updates, projects, orchestration, integrations, export, automation, reporting, and billing | Runtime business behavior or route handlers |
| `backend/app/agents/` | ADK/Gemini workflow orchestration, specialist task contracts/registry, prompt construction, bounded use-case shard coordination, deterministic fallbacks, impact recommendation logic, test-design review logic, coverage metrics, and agent response hydration | HTTP endpoint definitions, Firestore collection setup, frontend-specific shaping |
| `backend/app/services/` | Domain services for billing, audit, versioning, QA project lifecycle, orchestrator decisions, orchestrator run persistence, impact update application, execution, grounding, reporting, integration connection storage, and storage adapter boundaries | FastAPI route decorators, JSX/UI behavior |
| `backend/app/adapters/` | Provider-specific JIRA and Azure DevOps HTTP semantics | App-wide workflow decisions or billing policy |
| `backend/app/auth/` | Bearer token resolution, Firebase verification, Google credential verification, role normalization | Business workflow behavior |
| `backend/app/observability/` | Logging context, metric counters, optional OpenTelemetry wiring | Endpoint business logic |
| `backend/plain_english_test_framework/` | Structured-English spec to schema-valid IR to Playwright generation | API billing, user auth, remote artifact fetching |
| `frontend/src/components/` | Presentational and workflow UI components receiving props, including the project workspace, information rail, and route-scoped contextual task | Backend API transport details beyond passed callbacks/data |
| `frontend/src/pages/` | Route-level composition for Home, Projects, and project workbenches | Backend persistence policy or duplicate workflow routing contracts |
| `frontend/src/app/` | Canonical global/project route parsing and path building, route links, and route-level shell/page composition | Backend authorization policy, project persistence, or workflow mutation behavior |
| `frontend/src/api/` | Generated API contract declarations and high-traffic endpoint constants derived from FastAPI OpenAPI | Hand-written API behavior or generated files edited manually |
| `frontend/src/styles/` | Design tokens, base styles, layout styles, and selectors grouped by feature ownership | React state, API calls, or unrelated visual redesigns |
| `frontend/src/services/` | API base URL, request ID, API error parsing, download helpers | Large workflow state or JSX markup |
| `frontend/src/utils/` | Pure formatting, requirement, usage, and workflow helpers | React state or network side effects |
| `scripts/` | Local, CI, deployment, evaluation, and reproducibility utilities | Runtime API code imported by production paths unless intentionally shared |
| `docs/` | Human-readable planning, reference, explanation, and how-to material | Generated screenshots, exported client data, secrets |

## 4) Naming and Organization Rules

- Python files use snake_case, for example `use_case_agent.py`,
  `test_case_agent.py`, `test_case_coverage.py`, `artifact_fetcher.py`, and
  `azure_devops_sync_service.py`.
- Python classes and Pydantic models use PascalCase, for example
  `RequirementAnalysis`, `ExecutionPreviewResponse`, and `BillingAccount`.
- Backend Pydantic contract modules live under `backend/app/contracts/` by
  domain; keep `backend/app/models.py` as a compatibility facade for existing
  imports.
- Python functions and helpers use snake_case. Private/internal helpers are
  commonly prefixed with `_`, for example `_get_request_id()` and
  `_normalize_base_url()`.
- JSX component files use PascalCase, for example `AutomationPanel.jsx` and
  `RequirementReviewWorkbench.jsx`.
- Frontend helper files use camelCase or lower-case domain names, for example
  `apiClient.js`, `useBillingStatus.js`, and `workflow.js`.
- Backend route modules are grouped by product area, for example
  `projects.py`, `requirements.py`, `testcases.py`, `integrations_jira.py`, and
  `integrations_azure_devops.py`.
- Frontend component directories are grouped by feature or layout, for example
  `automation/`, `generation/`, `integrations/`, `projects/`,
  `requirements/`, `workflow/`, and `layout/`. Shared status semantics belong
  in `workflow/StatusBadge.jsx` instead of feature-specific pill variants.
- Frontend generated API contracts live under `frontend/src/api/generated/` and
  are refreshed with `python scripts/generate_frontend_api_types.py`.
- Frontend CSS is imported through `frontend/src/styles/index.css`; shared
  foundations live in `tokens.css` and `base.css`, while feature selectors live
  in the closest named style module.
- There are no tracked TypeScript path aliases. Backend imports use relative
  package imports inside `backend/app/`; frontend imports use relative paths.

## 5) Generated and Ignored Boundaries

Do not document generated files as source conventions and do not commit them.

- `.execution_artifacts/` is ignored and contains generated execution specs,
  IR, Playwright results, reports, traces, videos, and environment files.
- `client_submission/` is ignored and contains generated client screenshots,
  downloaded exports, and generated briefs.
- `/tmp/pw_workflow_out` is used by the full local Playwright documentation
  workflow for JSON snapshots and exported files.
- `frontend/dist/`, `frontend/test-results/`, `frontend/playwright-report/`,
  Node modules, Python caches, and `.venv/` are ignored.
- Backend execution runtime artifacts under `backend/execution_runtime/artifacts/`
  are ignored.
- Use `python scripts/cleanup_generated_artifacts.py` for a dry-run cleanup
  plan before deleting ignored generated artifacts.

## 6) Evidence

- `git ls-files`
- `backend/app/main.py`
- `backend/app/routers/`
- `backend/app/contracts/`
- `backend/app/services/`
- `backend/app/agents/`
- `backend/app/agents/impact_update_agent.py`
- `backend/app/agents/specialist_contracts.py`
- `backend/app/agents/specialist_registry.py`
- `backend/app/agents/test_case_coverage.py`
- `backend/app/agents/test_case_review.py`
- `backend/app/agents/test_case_fallback.py`
- `backend/app/agents/test_case_hydration.py`
- `backend/app/services/impact_update_service.py`
- `backend/app/services/orchestrator_service.py`
- `backend/plain_english_test_framework/`
- `backend/execution_runtime/package.json`
- `frontend/src/App.jsx`
- `frontend/src/app/`
- `frontend/src/api/generated/api-contracts.d.ts`
- `frontend/src/api/generated/api-contracts.js`
- `frontend/src/components/`
- `frontend/src/components/workflow/StatusBadge.jsx`
- `frontend/src/styles/`
- `frontend/src/services/apiClient.js`
- `frontend/e2e/`
- `frontend/e2e/support/layout.js`
- `.gitignore`
- `.dockerignore`
