# Agentic Test Case Generator

Web-based, human-in-the-loop workflow for parsing requirements (Word/Markdown/Excel), enriching context, generating test cases in a template format, exporting to JIRA (stub), and generating Playwright (Python) Page Object Model stubs.

## Features
- Google Login (Google Identity Services)
- Upload requirements (.md, .docx, .xlsx)
- Parse and extract requirement items
- Add context links (app, prototype, diagrams, images)
- Generate test cases from a user template
- Export to JIRA (stub)
- Generate Playwright (Python) POM stubs (stub)
- No document retention (in-memory processing only)

## Setup

### 1) Configure environment
Copy .env.example to .env and set values:
- GEMINI_API_KEY (required)
- MODEL_NAME (default: gemini-2.5-flash)
- GOOGLE_CLIENT_ID (required for login)
- JWT_SECRET_KEY (required for backend-issued access tokens)
- JWT_ALGORITHM (default: HS256)
- JWT_EXPIRATION_MINUTES (default: 60)
- VITE_GOOGLE_CLIENT_ID (required for frontend sign-in button)

Note: ADK expects GOOGLE_API_KEY. If only GEMINI_API_KEY is set, the backend normalizes it to GOOGLE_API_KEY at runtime.

For Google login, set `GOOGLE_CLIENT_ID` and `VITE_GOOGLE_CLIENT_ID` to the same OAuth web client ID.

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
- Google sign-in fails with audience/issuer errors: verify `GOOGLE_CLIENT_ID` and `VITE_GOOGLE_CLIENT_ID` exactly match the same web OAuth client ID.
- Login button missing: verify `VITE_GOOGLE_CLIENT_ID` is present in `.env` and restart frontend dev server.
- Requests return 401 after login: token may be expired or invalid; sign out/in again and confirm backend `JWT_SECRET_KEY` is set.

## Notes
- JIRA export is a stub; add credentials and mapping in backend/app/adapters/jira.py
- Automation generation is a stub; implement selectors and actions in backend/app/agents/automation_agent.py
- Uploaded documents are processed in-memory and not stored
- Upload size is capped at 16 MB per file
