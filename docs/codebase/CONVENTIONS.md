# Coding Conventions

This reference describes conventions visible in the tracked source. It does not
invent rules that are not enforced by configuration.

## 1) Naming Rules

| Item | Rule | Example | Evidence |
|------|------|---------|----------|
| Backend Python files | snake_case, usually by feature or service role | `test_case_agent.py`, `artifact_fetcher.py`, `azure_devops_sync_service.py` | `backend/app/agents/`, `backend/app/services/` |
| Backend test files | `test_*.py` in `backend/tests/` | `test_execution_service.py` | `backend/tests/` |
| Python functions | snake_case; internal helpers commonly start with `_` | `_normalize_base_url`, `preview_execution` | `backend/app/services/execution_service.py` |
| Python classes/models | PascalCase | `GenerateTestCasesResponse`, `BillingAccount` | `backend/app/models.py` |
| Constants/env vars | uppercase snake_case | `DEFAULT_MODEL_NAME`, `EXECUTION_MAX_CASES_PER_REQUEST` | `backend/app/config.py`, `.env.example` |
| Frontend components | PascalCase file and function names | `AutomationPanel.jsx`, `GeneratedTestCasesView.jsx` | `frontend/src/components/` |
| Frontend hooks | `use*` camelCase | `useBillingStatus.js`, `useEscapeToClose.js` | `frontend/src/hooks/` |
| Frontend helpers | camelCase exports in domain helper files | `createRequestId`, `downloadResponseBlob` | `frontend/src/services/apiClient.js` |
| Generated frontend API contracts | committed generated files under `frontend/src/api/generated/` | `api-contracts.d.ts`, `api-contracts.js` | `scripts/generate_frontend_api_types.py` |
| Frontend style modules | lower-case domain names imported through `styles/index.css` | `requirements-source.css`, `generation-results.css` | `frontend/src/styles/` |

## 2) Formatting and Linting

- Backend linting: Ruff is configured in `pyproject.toml` and installed from
  `backend/requirements-dev.txt`.
- Backend formatting: Ruff format is the tracked backend formatter.
- Frontend linting: ESLint flat config is tracked in `frontend/eslint.config.js`
  and runs through `npm run lint`.
- Frontend formatting: Prettier config is tracked in `frontend/.prettierrc.json`.
- Type checking: [TODO] no tracked Python type checker or TypeScript config was
  found for application code.
- Enforced lint commands: CI runs `python -m ruff check backend scripts` and
  `npm run lint`.
- Enforced format checks: CI runs `python -m ruff format --check backend scripts`
  and `npm run format:check`.
- Practical rule: match the surrounding file style, run focused tests, and run
  `git diff --check` before committing if possible.
- Source-focused codebase scans use `python scripts/scan_codebase.py`, which
  reads `git ls-files` and explicitly excludes ignored generated output
  directories plus local agent skill mirrors before writing
  `docs/codebase/.codebase-scan.txt`.

Current validation commands:

```bash
python -m pip install -r backend/requirements-dev.txt
python -m ruff check backend scripts
python -m ruff format --check backend scripts
python -m unittest discover -s backend/tests -p 'test_*.py'
python scripts/evaluate_requirements.py --offline --strict
python scripts/evaluate_generation.py --offline --strict
python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json --indent 0
python scripts/generate_frontend_api_types.py --check
python scripts/scan_codebase.py

cd frontend
npm run lint
npm run format:check
npm run build
npm run test:e2e -- e2e/export-approval-gate.spec.js

cd backend/execution_runtime
npm run test:playwright -- --list
```

## 3) Import and Module Conventions

- Backend application imports use relative imports inside `backend/app`, for
  example `from ..models import AuthUser` in routers.
- Backend tests import application modules through `app.*` when run with
  `backend` on the Python import path.
- Frontend modules use relative imports. No path alias is configured.
- Generated API contract files live in `frontend/src/api/generated/`. Do not
  edit them by hand; update them with `python scripts/generate_frontend_api_types.py`
  after backend API contract changes and verify with
  `python scripts/generate_frontend_api_types.py --check`.
- Frontend CSS enters the app through `frontend/src/styles/index.css`.
  Shared foundations live in `tokens.css` and `base.css`; feature-specific
  selectors stay in the nearest owned style module.
- Router modules expose a module-level `router = APIRouter()`.
- Service modules mostly expose functions rather than classes; provider-specific
  remote behavior lives in adapter modules.
- Orchestrator-delegated agent work should go through the specialist task
  contract registry in `backend/app/agents/specialist_registry.py`; add new
  task payload/result models in `backend/app/agents/specialist_contracts.py`
  before wiring new local or ADK-backed implementations.
- Orchestrated action progress should use
  `backend/app/services/orchestrator_run_service.py` for run records, event
  records, and checkpoints. Pass stable request IDs or idempotency keys when a
  client or worker may retry an action.
- The frontend project workspace may persist the selected project ID in local
  storage for resume-on-reload, but project state, recommended orchestrator
  actions, blockers, and timeline entries must be reloaded from backend project,
  status, and run endpoints.
- The generated frontend API contract module exports type declarations and
  high-traffic endpoint constants, not a full generated API client.

## 4) Error and Logging Conventions

Backend error strategy:

- Auth helpers raise `HTTPException` for missing, expired, revoked, or invalid
  bearer tokens.
- Routers raise `HTTPException` for endpoint-level validation and provider
  workflow failures.
- Service functions return typed response models or raise domain/runtime errors
  that routers translate to HTTP responses.
- Agent workflows prefer diagnostics, warnings, parser failure metadata, and
  deterministic fallback output when model output is malformed or incomplete.
- Specialist task dispatch returns structured diagnostics for input validation,
  output validation, timeout, and execution failures instead of partial untyped
  payloads.

Logging strategy:

- `backend/app/main.py` configures structured request logging through
  `backend/app/observability/logging.py`.
- Request middleware binds `request_id`, optional `trace_id`, method, and path
  into log context.
- Workflow agents add `request_id`, `workflow_run_id`, `actor_user_id`, and
  operation context to workflow logs.
- Audit writes are retried and dead-letter summaries are sanitized before
  storage in memory or the optional Firestore durable dead-letter sink.

Sensitive-data rules visible in code:

- Connection secrets are excluded from Pydantic reprs with `Field(repr=False)`.
- JIRA tokens and Azure DevOps PATs are encrypted before Firestore storage with
  non-secret key metadata and planned previous-key rotation support.
- Token hints are displayed instead of full credentials.
- The plain-English framework scans generated specs for secret-like values.
- README and `.env.example` warn not to commit PATs, API keys, credentials, or
  generated client data.

## 5) Testing Conventions

- Backend tests live in `backend/tests/` and are discovered with
  `python -m unittest discover -s backend/tests -p 'test_*.py'`.
- Tests use `unittest`, `fastapi.testclient.TestClient`, `unittest.mock.patch`,
  and local fake clients instead of real remote services.
- Integration tests for JIRA/Azure DevOps patch service and adapter boundaries.
- Firestore-dependent tests patch
  `app.services.firestore_repository.get_firestore_client`, swap repository
  test hooks, or use fake clients to isolate behavior.
- Frontend E2E specs live in `frontend/e2e/`.
- Execution runtime smoke checks use `npm run test:playwright -- --list` under
  `backend/execution_runtime`.

## 6) Branching, Issue, and Change Scope

The repository-level `AGENTS.md` makes GitHub issues or issue-ready proposals
the unit of work. Use `codex/issue-<number>-<short-slug>` when working from a
GitHub issue. If no issue exists, create one or add an issue-ready proposal
before changing code or docs.

The GitHub `main` branch is protected. Changes must merge through a pull
request linked to the issue. The solo-maintainer setup requires 0 approving
reviews, but still requires the branch to be up to date, all conversations to be
resolved, and these checks to pass:

- `Backend tests and offline benchmarks`
- `Frontend build and focused E2E`

Direct pushes, force pushes, and deletion of `main` are blocked. Admin
enforcement is enabled.

Keep implementations scoped to acceptance criteria, update traceability docs
when validation evidence changes, and avoid unrelated cleanup in issue-scoped
work.

## 7) Evidence

- `AGENTS.md`
- `backend/app/main.py`
- `backend/app/auth/jwt_auth.py`
- `backend/app/auth/firebase_auth.py`
- `backend/app/services/jira_connection_service.py`
- `backend/app/services/azure_devops_connection_service.py`
- `backend/app/services/credential_crypto.py`
- `backend/app/observability/logging.py`
- `backend/plain_english_test_framework/validation.py`
- `backend/tests/`
- `frontend/src/services/apiClient.js`
- `frontend/src/components/`
- `.github/workflows/ci.yml`
- `pyproject.toml`
- `backend/requirements-dev.txt`
- `frontend/eslint.config.js`
- `frontend/.prettierrc.json`
- `frontend/package.json`
