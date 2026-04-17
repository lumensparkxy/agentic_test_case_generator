# Agentic Test Case Generator

Web-based, human-in-the-loop workflow for parsing requirements (Word/Markdown/Excel), enriching context, generating test cases in a template format, and exporting them as CSV, Excel, or JSON.

## Features
- Firebase Authentication with Google, Microsoft, and Apple sign-in
- Upload requirements (.md, .docx, .xlsx)
- Parse and extract requirement items
- Add context links (app, prototype, diagrams, images)
- Generate test cases from a user template
- No document retention (in-memory processing only)

## Setup

### 1) Configure environment
Copy .env.example to .env and set values:
- GEMINI_API_KEY (required)
- MODEL_NAME (default: gemini-2.5-flash)
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
- VITE_FIREBASE_ENABLE_GOOGLE_AUTH, VITE_FIREBASE_ENABLE_MICROSOFT_AUTH, VITE_FIREBASE_ENABLE_APPLE_AUTH (optional booleans to show/hide provider buttons while configuring providers)
- VITE_FIREBASE_PROJECT_ID (required for frontend sign-in button)
- VITE_FIREBASE_APP_ID (required for frontend sign-in button)
- VITE_FIREBASE_STORAGE_BUCKET, VITE_FIREBASE_MESSAGING_SENDER_ID, VITE_FIREBASE_MEASUREMENT_ID (recommended to mirror your Firebase web app config)

Note: ADK expects GOOGLE_API_KEY. If only GEMINI_API_KEY is set, the backend normalizes it to GOOGLE_API_KEY at runtime.

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

### 1.2) Microsoft and Apple quick setup (Firebase Auth)

For popup-based Firebase OAuth providers, the callback handler used by this project is:

- `https://<VITE_FIREBASE_AUTH_DOMAIN>/__/auth/handler`

For the current local `.env`, that resolves to:

- `https://testcase-generatorxy.firebaseapp.com/__/auth/handler`

Microsoft setup:

1. In Firebase Console -> Authentication -> Sign-in method, enable **Microsoft**.
2. Create or open the Microsoft Entra app registration that backs this Firebase provider.
3. Add this **Web redirect URI** in the Microsoft app registration:
	- `https://<VITE_FIREBASE_AUTH_DOMAIN>/__/auth/handler`
4. Copy the Microsoft **Client ID** and **Client Secret** back into the Firebase Microsoft provider configuration and save.

Apple setup:

1. Join the Apple Developer Program and create a **Service ID** for Sign in with Apple.
2. In the Apple developer configuration for Sign in with Apple on the web, add this **Return URL**:
	- `https://<VITE_FIREBASE_AUTH_DOMAIN>/__/auth/handler`
3. Create a Sign in with Apple private key.
4. In Firebase Console -> Authentication -> Sign-in method, enable **Apple** and provide the Service ID, Team ID, Key ID, and private key.

If you are still configuring a provider, you can temporarily hide its button in the chooser with one of these optional `.env` flags:

- `VITE_FIREBASE_ENABLE_MICROSOFT_AUTH=false`
- `VITE_FIREBASE_ENABLE_APPLE_AUTH=false`

### 2) Backend

Create a Python virtual environment (3.10+ recommended) and install deps:
- `python -m pip install -r backend/requirements.txt`

Run the API:
- `uvicorn app.main:app --reload --app-dir backend`

### 3) Frontend

Install deps:
- `npm install` in frontend

Run UI:
- `npm run dev` in frontend

Open http://localhost:5173

### 3.1) Evaluate generation quality with benchmark fixtures

To measure the current test-case generator against the benchmark fixtures in `scripts/benchmark_inputs/`, run:

- `python scripts/evaluate_generation.py`

Useful options:

- `--offline` to force deterministic fallback mode even if model credentials are present
- `--strict` to fail the run when any expected benchmark trait is unmet
- `--output-json path/to/report.json` to save the benchmark report as JSON

If no `GOOGLE_API_KEY` or `GEMINI_API_KEY` is configured, the script automatically uses offline fallback mode and still reports structural baseline metrics.

### 3.2) Evaluate requirement extraction quality with benchmark fixtures

To measure the requirements agent against the document-style benchmark fixtures in `scripts/benchmark_requirement_inputs/`, run:

- `python scripts/evaluate_requirements.py`

Useful options:

- `--offline` to force deterministic heuristic mode even if model credentials are present
- `--strict` to fail the run when any expected benchmark trait is unmet
- `--output-json path/to/report.json` to save the benchmark report as JSON

If no `GOOGLE_API_KEY` or `GEMINI_API_KEY` is configured, the script automatically uses offline fallback mode and still reports structural baseline metrics for extraction quality.

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
	- `/export/*`
	- `/automation/*`

Frontend stores the access token in `localStorage` for the current MVP.

## Troubleshooting
- Backend fails with auth errors: verify `GEMINI_API_KEY` is set in [.env.example](.env.example) (copied to `.env`). The backend maps it to `GOOGLE_API_KEY` at runtime for ADK.
- Frontend cannot reach API: set `VITE_API_BASE` in `.env` or use the default from [.env.example](.env.example).
- Import errors after install: re-run `python -m pip install -r backend/requirements.txt` inside your active virtual environment.
- Google sign-in fails with audience/issuer errors: verify `GOOGLE_CLIENT_ID` and `VITE_GOOGLE_CLIENT_ID` exactly match the same web OAuth client ID, or list every valid web client ID in `GOOGLE_CLIENT_IDS`.
- Apple sign-in fails with `auth/operation-not-allowed`: enable Apple in Firebase Console -> Authentication -> Sign-in method and complete the Service ID / Team ID / Key ID / private key setup for the same Firebase project referenced by `VITE_FIREBASE_PROJECT_ID`.
- Microsoft sign-in fails with `invalid_request` or `redirect_uri` errors: add `https://<VITE_FIREBASE_AUTH_DOMAIN>/__/auth/handler` as a Web redirect URI in the Microsoft Entra app registration used by Firebase, then re-save that provider's Client ID and Client Secret in Firebase Console.
- Microsoft or Apple buttons should be hidden until provider setup is ready: set `VITE_FIREBASE_ENABLE_MICROSOFT_AUTH=false` or `VITE_FIREBASE_ENABLE_APPLE_AUTH=false` in `.env`, then restart the frontend dev server.
- Login button missing: verify `VITE_GOOGLE_CLIENT_ID` is present in `.env` and restart frontend dev server.
- Requests return 401 after login: token may be expired or invalid; sign out/in again and confirm backend `JWT_SECRET_KEY` is set.

## Notes
- JIRA export and Playwright automation stubs remain in the backend but are hidden in the current UI until they are implemented.
- Uploaded documents are processed in-memory and not stored
- Upload size is capped at 16 MB per file
