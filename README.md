# Agentic Test Case Generator

Web-based, human-in-the-loop workflow for parsing requirements (Word/Markdown/Excel), enriching context, generating test cases in a template format, and exporting them as CSV, Excel, or JSON.

## Features
- Firebase Authentication sign-in with Google, Microsoft, and Apple providers when enabled
- Upload requirements (.md, .docx, .xlsx)
- Parse and extract requirement items
- Import and sync requirements from JIRA Cloud issues
- Import and sync requirements from Azure DevOps Services work items
- Analyze context links (app, prototype, diagrams, images) into grounded UI/API/workflow facts
- Generate test cases from a user template with requirement-analysis, scenario-plan, and coverage diagnostics
- Preview generated test cases for browser automation readiness and run approved executable candidates with Playwright
- No document retention (in-memory processing only)

## Documentation Map

- `docs/codebase/STACK.md` describes runtimes, dependencies, commands, and environment configuration.
- `docs/codebase/STRUCTURE.md` maps source directories, entry points, and module boundaries.
- `docs/codebase/ARCHITECTURE.md` explains the backend, frontend, agent, integration, and execution-runtime flow.
- `docs/codebase/CONVENTIONS.md` captures naming, routing, error handling, logging, and testing conventions.
- `docs/codebase/INTEGRATIONS.md` inventories Firebase, Gemini/ADK, Firestore, JIRA, Azure DevOps, Playwright, and observability integrations.
- `docs/codebase/TESTING.md` lists validation gates and folds in the reproducible Playwright documentation E2E workflow for the next version.
- `docs/codebase/CONCERNS.md` records high-churn areas, technical debt, security/scaling concerns, and open architecture questions.

## Setup

### 1) Configure environment
Copy .env.example to .env and set values:
- GEMINI_API_KEY (required)
- MODEL_NAME (default: gemini-3.5-flash)
- GOOGLE_CLIENT_ID (required for login)
- GOOGLE_CLIENT_IDS (optional comma-separated allow-list for multiple web client IDs)
- FIREBASE_PROJECT_ID (recommended for Firebase Admin initialization)
- FIREBASE_SERVICE_ACCOUNT_JSON (optional for local containers or non-GCP runtimes)
- GOOGLE_APPLICATION_CREDENTIALS (optional alternative for the deploy helper; use an absolute path to a local Firebase Admin SDK JSON file)
- JWT_SECRET_KEY (required for backend-issued access tokens)
- JWT_ALGORITHM (default: HS256)
- JWT_EXPIRATION_MINUTES (default: 60)
- VITE_GOOGLE_CLIENT_ID (required for frontend sign-in button)
- VITE_FIREBASE_API_KEY (required for frontend sign-in button)
- VITE_FIREBASE_AUTH_DOMAIN (required for frontend sign-in button)
- VITE_FIREBASE_PROJECT_ID (required for frontend sign-in button)
- VITE_FIREBASE_APP_ID (required for frontend sign-in button)
- VITE_FIREBASE_STORAGE_BUCKET, VITE_FIREBASE_MESSAGING_SENDER_ID, VITE_FIREBASE_MEASUREMENT_ID (recommended to mirror your Firebase web app config)
- VITE_FIREBASE_ENABLE_GOOGLE_AUTH, VITE_FIREBASE_ENABLE_MICROSOFT_AUTH, VITE_FIREBASE_ENABLE_APPLE_AUTH (optional provider toggles; default true)
- JIRA_CONNECTION_SECRET_KEY (optional dedicated encryption key for stored JIRA API tokens; falls back to JWT_SECRET_KEY)
- AZURE_DEVOPS_CONNECTION_SECRET_KEY (optional dedicated encryption key for stored Azure DevOps PATs; falls back to JWT_SECRET_KEY)
- AZURE_DEVOPS_API_VERSION (optional; defaults to 7.1)
- AZURE_DEVOPS_API_TIMEOUT_SECONDS, AZURE_DEVOPS_PROJECT_PAGE_SIZE, AZURE_DEVOPS_WORK_ITEM_PAGE_SIZE (optional Azure DevOps client tuning)
- EXECUTION_ENABLED (optional; defaults to true)
- EXECUTION_ARTIFACT_ROOT (optional; defaults to `.execution_artifacts`)
- EXECUTION_DEFAULT_BASE_URL (optional browser execution target)
- EXECUTION_PLAYWRIGHT_CONFIG, EXECUTION_RUNTIME_CWD, EXECUTION_MAX_CASES_PER_REQUEST, EXECUTION_BROWSER_CHANNEL (optional execution runtime tuning)
- BILLING_SHADOW_MODE (optional; defaults to true), BILLING_CONTACT_EMAIL, BILLING_ADMIN_EMAILS, BILLING_PILOT_REQUIREMENTS_LIMIT, BILLING_PILOT_TEST_CASE_LIMIT, and related billing tuning values

Note: ADK expects GOOGLE_API_KEY. Configure GEMINI_API_KEY in this project; the backend normalizes it to GOOGLE_API_KEY at runtime. If both are set, GEMINI_API_KEY is preferred so the project `.env` does not get shadowed by an older shell-level GOOGLE_API_KEY.

For Google login, set `GOOGLE_CLIENT_ID` and `VITE_GOOGLE_CLIENT_ID` to the same OAuth web client ID. If you intentionally use different client IDs across environments or builds, add all valid IDs to `GOOGLE_CLIENT_IDS`.

### 1.1) Google Cloud Console quick setup (for local dev)
1. Create/select a Google Cloud project.
2. Configure OAuth consent screen.
3. Create OAuth 2.0 Client ID of type **Web application**.
4. Add Authorized JavaScript origins:
	- `http://localhost:5173`
	- `http://127.0.0.1:5173`
5. Copy the generated client ID into `.env` as both:
	- `GOOGLE_CLIENT_ID`
	- `VITE_GOOGLE_CLIENT_ID`

If you also run a deployed frontend with a different Google OAuth web client, add both client IDs to `GOOGLE_CLIENT_IDS` as a comma-separated list.

### 1.2) Azure DevOps integration setup

Azure DevOps access is stored as a per-user integration connection, separate from the app login session. The first implementation uses Azure DevOps Personal Access Tokens so both work/school organization accounts and personal Microsoft accounts can connect.

For each user connection:

1. Sign in to the app.
2. Create an Azure DevOps PAT in the target organization/account with the smallest useful scopes:
	- Project and team: read
	- Work items: read/write
3. Connect using an Azure DevOps organization or project URL, for example:
	- `https://dev.azure.com/{organization}`
	- `https://dev.azure.com/{organization}/{project}`
4. If a project URL is supplied, the backend normalizes it to the organization URL and remembers the project as the default project for that connection.

The backend encrypts stored PATs using `AZURE_DEVOPS_CONNECTION_SECRET_KEY` when set, otherwise `JWT_SECRET_KEY`. Do not commit PATs to `.env.example`, tests, logs, or documentation.

### 2) Backend

Create a Python virtual environment (3.10+ recommended) and install deps:
- `python -m pip install -r backend/requirements.txt`
- `python -m pip install -r backend/requirements-dev.txt` for lint and format tooling

Run the API:
- `uvicorn app.main:app --reload --app-dir backend --reload-dir backend`

### 3) Frontend

Install deps:
- `npm ci` in frontend

Run UI:
- `npm run dev` in frontend

Open http://localhost:5173

### 3.0.1) Install the execution runtime

The Automation tab uses the backend execution runtime under `backend/execution_runtime`.

- `cd backend/execution_runtime`
- `npm ci`

The Playwright config uses your installed Microsoft Edge browser by default through `channel: "msedge"`. If Edge is not already installed on the machine, install Microsoft Edge or run:

- `npx playwright install msedge`

Generated execution files are written under `EXECUTION_ARTIFACT_ROOT` and ignored by git. The internal handoff is generated `TestCase` JSON from the webapp, not Excel.

### 3.1) Evaluate generation quality with benchmark fixtures

To measure the current test-case generator against the benchmark fixtures in `scripts/benchmark_inputs/`, run:

- `python scripts/evaluate_generation.py`

Useful options:

- `--offline` to force deterministic fallback mode even if model credentials are present
- `--strict` to fail the run when any expected benchmark trait is unmet
- `--output-json path/to/report.json` to save the benchmark report as JSON

If no `GOOGLE_API_KEY` or `GEMINI_API_KEY` is configured, the script automatically uses offline fallback mode and still reports structural baseline metrics.

### 3.1.1) Run backend regression tests

The current backend suite is written with `unittest` and does not require `pytest`:

- `python -m unittest discover -s backend/tests -p 'test_*.py'`

If you prefer pytest locally, install it separately; it is not required by the checked-in test suite today.

### 3.2) Evaluate requirement extraction quality with benchmark fixtures

To measure the requirements agent against the document-style benchmark fixtures in `scripts/benchmark_requirement_inputs/`, run:

- `python scripts/evaluate_requirements.py`

Useful options:

- `--offline` to force deterministic heuristic mode even if model credentials are present
- `--strict` to fail the run when any expected benchmark trait is unmet
- `--output-json path/to/report.json` to save the benchmark report as JSON

If no `GOOGLE_API_KEY` or `GEMINI_API_KEY` is configured, the script automatically uses offline fallback mode and still reports structural baseline metrics for extraction quality.

### 3.3) Export the API contract

To generate the current FastAPI OpenAPI schema for contract checks or frontend type generation, run:

- `python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json`

Use this as the source for generated TypeScript clients/types instead of hand-copying response shapes into the frontend.

### 3.4) Run lint and format checks

Backend linting uses Ruff:

- `python -m ruff check backend scripts`
- `python -m ruff format --check backend scripts`

Frontend linting and formatting use ESLint and Prettier:

- `cd frontend && npm run lint`
- `cd frontend && npm run format:check`

CI enforces the backend Ruff lint/format checks and frontend ESLint/Prettier checks.

### 4) Run with containers on fixed local ports

If you want the containerized app to use the same local URLs every time, run it with Docker Compose from the repo root:

- `docker compose up --build`

This publishes:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`

This is useful for Google OAuth because you only need to authorize the standard local dev origins:

- `http://localhost:5173`
- `http://127.0.0.1:5173`

To stop the containers:

- `docker compose down`

### 5) Deploy both containers to Google Cloud Run

This repo includes a helper script that deploys both services as containers to Cloud Run and keeps them on scale-to-zero managed infrastructure.

Prerequisites:

- `gcloud` CLI installed and authenticated
- Docker installed and running
- Billing enabled on your GCP project
- A `.env` file with at least these values set:
	- `GEMINI_API_KEY`
	- `GOOGLE_CLIENT_ID`
	- `VITE_GOOGLE_CLIENT_ID`
	- `VITE_FIREBASE_API_KEY`
	- `VITE_FIREBASE_AUTH_DOMAIN`
	- `VITE_FIREBASE_PROJECT_ID`
	- `VITE_FIREBASE_APP_ID`
	- `JWT_SECRET_KEY`

Set your target project and optionally the region/repository/service names:

- `export PROJECT_ID=your-gcp-project-id`
- `export REGION=us-central1`
- `export ARTIFACT_REPO=agentic-tcg`
- `export BACKEND_SERVICE=tcg-backend`
- `export FRONTEND_SERVICE=tcg-frontend`

Run the deploy script from the repo root:

- `./scripts/deploy_cloud_run.sh`

What the script does:

- enables Cloud Run, Artifact Registry, and Secret Manager APIs
- creates the Docker Artifact Registry repository if needed
- stores `GEMINI_API_KEY` and `JWT_SECRET_KEY` in Secret Manager
- optionally stores `FIREBASE_SERVICE_ACCOUNT_JSON` in Secret Manager when provided
- if `GOOGLE_APPLICATION_CREDENTIALS` points to a local Firebase Admin SDK JSON file, the deploy script reads that file and uploads it as the `FIREBASE_SERVICE_ACCOUNT_JSON` secret automatically
- builds and pushes the backend container
- deploys the backend to Cloud Run
- builds and pushes the frontend container with the deployed backend URL and Firebase web config baked in
- deploys the frontend to Cloud Run
- updates backend CORS to allow the deployed frontend URL
- runs a CORS preflight smoke check against the deployed backend

After deployment, add the frontend Cloud Run URL as an Authorized JavaScript origin in your Google OAuth web client.

If you use Firebase Authentication with popup or redirect flows, also add the deployed frontend hostname (without `https://`) to Firebase Console -> Authentication -> Settings -> Authorized domains. For Cloud Run, this is typically both the canonical `*.a.run.app` hostname and the region-scoped `*.run.app` hostname shown by the deploy script.

## API Authentication
- Public endpoints:
	- `GET /health`
	- `POST /auth/google/login`
	- `GET /auth/me`
	- `POST /auth/logout`
- Protected endpoints (Bearer token required):
	- `/requirements/*`
	- `/testcases/*`
	- `/integrations/jira/*`
	- `/integrations/azure-devops/*`
	- `/export/*`
	- `/automation/*`

Frontend stores the access token in `localStorage` for the current MVP.

## Troubleshooting
- Backend fails with auth errors: verify `GEMINI_API_KEY` is set in [.env.example](.env.example) (copied to `.env`). The backend maps it to `GOOGLE_API_KEY` at runtime for ADK.
- Frontend cannot reach API: set `VITE_API_BASE` in `.env` or use the default from [.env.example](.env.example).
- Import errors after install: re-run `python -m pip install -r backend/requirements.txt` inside your active virtual environment.
- Backend restarts or crashes unexpectedly in local dev: make sure Uvicorn reload is limited to the backend source tree (`--reload-dir backend`) so it does not watch `.venv` or other workspace folders.
- Google sign-in fails with audience/issuer errors: verify `GOOGLE_CLIENT_ID` and `VITE_GOOGLE_CLIENT_ID` exactly match the same web OAuth client ID, or list every valid web client ID in `GOOGLE_CLIENT_IDS`.
- Login button missing: verify `VITE_GOOGLE_CLIENT_ID` is present in `.env` and restart frontend dev server.
- Requests return 401 after login: token may be expired or invalid; sign out/in again and confirm backend `JWT_SECRET_KEY` is set.
- Azure DevOps connection fails with 401/403: verify the PAT is active, belongs to an account with access to the organization, and includes Project/team read plus Work Items read/write scopes.
- Azure DevOps project import requires a project: use a project URL during connection or select/provide a project before searching/importing work items.

## Notes
- Requirement import/sync is implemented for JIRA Cloud and Azure DevOps Services. JIRA test-case export remains a backend stub.
- Playwright POM generation remains backend-only/experimental and is hidden in the current UI until the automation workflow is productized.
- Uploaded documents are processed in-memory and not stored
- Upload size is capped at 16 MB per file
