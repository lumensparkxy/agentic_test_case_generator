# Agentic Test Case Generator

Web-based, human-in-the-loop workflow for parsing requirements (Word/Markdown), enriching context, generating test cases in a template format, exporting to JIRA (stub), and generating Playwright (Python) Page Object Model stubs.

## Features
- Upload requirements (.md, .docx)
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
- MODEL_NAME (default: gemini-3-flash-preview)

Note: ADK expects GOOGLE_API_KEY. The backend maps GEMINI_API_KEY to GOOGLE_API_KEY at runtime.

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

## Troubleshooting
- Backend fails with auth errors: verify `GEMINI_API_KEY` is set in [.env.example](.env.example) (copied to `.env`). The backend maps it to `GOOGLE_API_KEY` at runtime for ADK.
- Frontend cannot reach API: set `VITE_API_BASE` in `.env` or use the default from [.env.example](.env.example).
- Import errors after install: re-run `python -m pip install -r backend/requirements.txt` inside your active virtual environment.

## Notes
- JIRA export is a stub; add credentials and mapping in backend/app/adapters/jira.py
- Automation generation is a stub; implement selectors and actions in backend/app/agents/automation_agent.py
- Uploaded documents are processed in-memory and not stored
