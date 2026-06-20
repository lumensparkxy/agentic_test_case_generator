# Technology Stack

This reference describes the current tracked source stack. It excludes ignored
execution artifacts, local virtual environments, Node modules, screenshots, and
generated client submission outputs.

## 1) Runtime Summary

| Area | Value | Evidence |
|------|-------|----------|
| Backend language | Python | `backend/app/main.py`, `backend/requirements.txt` |
| Backend runtime | Python 3.12 in CI and backend Docker image; README recommends local Python 3.10+ | `.github/workflows/ci.yml`, `backend/Dockerfile`, `README.md` |
| Backend package manager | `pip` with `backend/requirements.txt` | `backend/requirements.txt`, `.github/workflows/ci.yml` |
| Backend web framework | FastAPI served by Uvicorn | `backend/app/main.py`, `backend/requirements.txt`, `backend/Dockerfile` |
| Frontend language | JavaScript and JSX | `frontend/src/App.jsx`, `frontend/package.json` |
| Frontend runtime | Node.js for build/dev tooling; browser runtime for React app | `frontend/package.json`, `.github/workflows/ci.yml`, `frontend/Dockerfile` |
| Frontend package manager | `npm` with checked-in lockfile | `frontend/package.json`, `frontend/package-lock.json` |
| Frontend build system | Vite | `frontend/package.json`, `frontend/vite.config.js` |
| Execution runtime | Node.js Playwright Test runtime isolated under `backend/execution_runtime/` | `backend/execution_runtime/package.json`, `backend/execution_runtime/playwright.config.ts` |
| Container runtime | Backend Python image and frontend Nginx image, composed locally by Docker Compose | `backend/Dockerfile`, `frontend/Dockerfile`, `compose.yaml` |

## 2) Production Frameworks and Dependencies

| Dependency | Version | Role in system | Evidence |
|------------|---------|----------------|----------|
| `fastapi` | `>=0.136.3` | Backend API routing, dependency injection, OpenAPI contract | `backend/requirements.txt`, `backend/app/main.py` |
| `uvicorn` | `>=0.49.0` | ASGI server for local and container backend runs | `backend/requirements.txt`, `backend/Dockerfile` |
| `pydantic` | `>=2.13.4` | Request, response, workflow, billing, integration, and execution models | `backend/requirements.txt`, `backend/app/models.py` |
| `google-adk` | `>=2.2.0,<3.0` | Multi-agent requirement/test-case workflows | `backend/requirements.txt`, `backend/app/adk_client.py`, `backend/app/agents/test_case_agent.py` |
| `google-genai` | `>=2.8.0,<3.0` | Gemini API calls for agent and automation generation | `backend/requirements.txt`, `backend/app/agents/automation_agent.py` |
| `firebase-admin` | `>=7.4.0` | Firebase ID token verification and Firestore client access | `backend/requirements.txt`, `backend/app/auth/firebase_auth.py`, `backend/app/services/firebase_admin.py` |
| `PyJWT` | `>=2.13.0` | Backend-issued legacy/local JWT support and E2E helper token minting | `backend/requirements.txt`, `backend/app/auth/jwt_auth.py`, `scripts/e2e_playwright_workflow.py` |
| `google-auth` | `>=2.53.0` | Google credential verification for `/auth/google/login` | `backend/requirements.txt`, `backend/app/auth/google_auth.py` |
| `cryptography` | `>=48.0.1` | Fernet encryption and key-rotation support for stored JIRA tokens and Azure DevOps PATs | `backend/requirements.txt`, `backend/app/services/credential_crypto.py`, `backend/app/services/jira_connection_service.py`, `backend/app/services/azure_devops_connection_service.py` |
| `python-docx` | `>=1.2.0` | Word requirement parsing and client brief generation | `backend/requirements.txt`, `backend/app/routers/requirements.py`, `scripts/build_client_solution_brief.py` |
| `openpyxl` | `>=3.1.5` | Excel requirement parsing and XLSX export | `backend/requirements.txt`, `backend/app/utils/excel_parser.py`, `backend/app/agents/export_agent.py` |
| `PyYAML` | `>=6.0.3` | Plain-English spec, IR, environment, and data fixture handling | `backend/requirements.txt`, `backend/plain_english_test_framework/compiler.py` |
| `jsonschema` | `>=4.26.0` | Schema validation for plain-English spec and IR contracts | `backend/requirements.txt`, `schemas/spec.schema.json`, `schemas/ir.schema.json` |
| OpenTelemetry packages | pinned around `1.41.1` / `0.62b1` | Optional tracing support | `backend/requirements.txt`, `backend/app/observability/tracing.py` |
| `react` / `react-dom` | `^19.2.7` | Frontend UI runtime | `frontend/package.json`, `frontend/src/main.jsx` |
| `firebase` | `^12.14.0` | Frontend Firebase Authentication provider setup | `frontend/package.json`, `frontend/src/firebase.js` |
| `@react-oauth/google` | `^0.13.5` | Google OAuth dependency still present in frontend package manifest | `frontend/package.json` |
| `lucide-react` | `^1.21.0` | Frontend workflow-shell line icons and directional collapse controls | `frontend/package.json`, `frontend/src/components/layout/WorkflowNavigationDrawer.jsx`, `frontend/src/components/projects/ProjectInformationRail.jsx` |

## 3) Development Toolchain

| Tool | Purpose | Evidence |
|------|---------|----------|
| `python -m unittest` | Backend regression suite | `backend/tests/`, `.github/workflows/ci.yml` |
| Ruff | Backend linting and target formatter | `pyproject.toml`, `backend/requirements-dev.txt`, `.github/workflows/ci.yml` |
| Offline evaluation scripts | Deterministic requirement and generation quality gates | `scripts/evaluate_requirements.py`, `scripts/evaluate_generation.py`, `.github/workflows/ci.yml` |
| `scripts/export_openapi.py` | FastAPI contract export | `scripts/export_openapi.py`, `.github/workflows/ci.yml` |
| Vite | Frontend dev server and production build | `frontend/package.json` |
| ESLint | Frontend JavaScript/JSX linting | `frontend/eslint.config.js`, `frontend/package.json`, `.github/workflows/ci.yml` |
| Prettier | Frontend target formatter | `frontend/.prettierrc.json`, `frontend/package.json` |
| Playwright Test | Frontend E2E checks and backend execution runtime | `frontend/package.json`, `frontend/playwright.config.js`, `backend/execution_runtime/package.json` |
| Docker Compose | Local two-container runtime | `compose.yaml` |
| GitHub Actions | CI for backend/frontend lint and format checks, backend tests, offline benchmarks, frontend build, and focused mocked E2E | `.github/workflows/ci.yml` |

## 4) Key Commands

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/requirements-dev.txt

uvicorn app.main:app --reload --app-dir backend --reload-dir backend

cd frontend
npm ci
npm run lint
npm run format:check
npm run dev
npm run build
npm run test:e2e -- e2e/export-approval-gate.spec.js

cd backend/execution_runtime
npm ci
npm run test:playwright -- --list

python -m ruff check backend scripts
python -m ruff format --check backend scripts
python -m unittest discover -s backend/tests -p 'test_*.py'
python scripts/evaluate_requirements.py --offline --strict
python scripts/evaluate_generation.py --offline --strict
python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json --indent 0
python scripts/generate_frontend_api_types.py --check
```

## 5) Environment and Config

- Main local template: `.env.example`.
- Backend config loader: `backend/app/config.py`.
- Frontend build-time config: `frontend/src/firebase.js`, `frontend/src/services/apiClient.js`, `frontend/Dockerfile`.
- Local container config: `compose.yaml`.
- Cloud Run deployment helper: `scripts/deploy_cloud_run.sh`.
- Credential rotation runbook: `docs/credential-rotation-runbook.md`.

Required or important backend variables:

- `GEMINI_API_KEY`
- `MODEL_NAME`
- `JWT_SECRET_KEY`
- `JWT_ALGORITHM`
- `JWT_EXPIRATION_MINUTES`
- `AUTH_TOKEN_MODE`: `firebase-only` for production,
  `firebase-or-backend-jwt` for local/E2E compatibility.
- `METRICS_ENABLED`
- `METRICS_ACCESS_TOKEN`
- `AUDIT_DEAD_LETTER_BACKEND`
- `AUDIT_DEAD_LETTER_COLLECTION`
- `CORS_ALLOW_ORIGINS`
- `FIREBASE_PROJECT_ID`
- `FIREBASE_SERVICE_ACCOUNT_JSON`
- `JIRA_CONNECTION_SECRET_KEY`
- `JIRA_CONNECTION_PREVIOUS_SECRET_KEYS`
- `AZURE_DEVOPS_CONNECTION_SECRET_KEY`
- `AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS`
- `EXECUTION_ENABLED`
- `EXECUTION_ARTIFACT_ROOT`
- `EXECUTION_DEFAULT_BASE_URL`
- `EXECUTION_PLAYWRIGHT_CONFIG`
- `EXECUTION_RUNTIME_CWD`
- `EXECUTION_MAX_CASES_PER_REQUEST`
- `EXECUTION_BROWSER_CHANNEL`
- `BILLING_*`
- `OTEL_*`
- `LOG_*`

Required or important frontend variables:

- `VITE_API_BASE`
- `VITE_FIREBASE_API_KEY`
- `VITE_FIREBASE_AUTH_DOMAIN`
- `VITE_FIREBASE_PROJECT_ID`
- `VITE_FIREBASE_APP_ID`
- `VITE_FIREBASE_*` optional metadata and provider toggles.
- `VITE_GOOGLE_CLIENT_ID` only for compatibility-mode Google OAuth hints.

Deployment/runtime constraints:

- Backend Docker installs Node.js, npm, the execution runtime dependencies, and
  Microsoft Edge Playwright support so backend execution endpoints can generate
  and run Playwright specs.
- Frontend Docker builds static assets with Vite and serves them through Nginx.
- Local generated execution outputs live under `.execution_artifacts/` and are
  ignored by git.
- Generated execution/client-submission outputs should be reviewed with
  `python scripts/cleanup_generated_artifacts.py` before deletion. The cleanup
  command is dry-run by default and targets `.execution_artifacts/`,
  `client_submission/`, and `/tmp/pw_workflow_out`.
- `scripts/deploy_cloud_run.sh` stores required Gemini/JWT secrets, optional
  Firebase Admin and metrics secrets, optional dedicated JIRA/Azure DevOps
  connection encryption keys, and optional previous-key rotation lists in
  Secret Manager when those values are set locally. See
  `docs/credential-rotation-runbook.md` before rotating production secrets.

## 6) Evidence

- `backend/requirements.txt`
- `backend/requirements-dev.txt`
- `frontend/package.json`
- `frontend/eslint.config.js`
- `frontend/.prettierrc.json`
- `backend/execution_runtime/package.json`
- `backend/app/config.py`
- `backend/app/main.py`
- `backend/app/services/credential_crypto.py`
- `.env.example`
- `.github/workflows/ci.yml`
- `backend/Dockerfile`
- `frontend/Dockerfile`
- `compose.yaml`
- `scripts/deploy_cloud_run.sh`
- `scripts/reencrypt_integration_credentials.py`
- `docs/credential-rotation-runbook.md`
