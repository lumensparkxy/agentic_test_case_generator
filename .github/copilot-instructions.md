# Agentic Test Case Generator - Copilot Instructions

## Project Overview

This is a **full-stack web application** for AI-powered test case generation. It features a human-in-the-loop workflow for:
- Parsing requirements from Word/Markdown documents
- Generating comprehensive test cases using Google ADK multi-agent pipelines
- Exporting to CSV, Excel, JSON, and JIRA
- Generating Playwright (Python) Page Object Model stubs

## Tech Stack

### Frontend
- **React 18** with Vite bundler
- **Location**: `frontend/`
- **Entry**: `src/App.jsx` - Single-page tabbed workflow
- **Styling**: CSS variables with modern design system (`src/App.css`)
- **API Base**: Configured via `VITE_API_BASE` env var (default: `http://localhost:8000`)

### Backend
- **FastAPI** with Python 3.10+
- **Location**: `backend/`
- **Entry**: `app/main.py` - REST API endpoints
- **AI Engine**: Google ADK with Gemini models (multi-agent pipelines)
- **Key Dependencies**: `google-adk`, `google-genai`, `python-docx`, `openpyxl`, `pydantic`

## Architecture

### Multi-Agent Pipelines (Google ADK)

The backend uses sophisticated agent orchestration:

1. **Requirements Agent** (`app/agents/requirements_agent.py`)
   - `InitialExtractorAgent` → `RefinementLoop` (Reviewer + Refiner)
   - Extracts testable requirements from documents
   - Supports human feedback refinement

2. **Test Case Agent** (`app/agents/test_case_agent.py`)
   - `TestCaseGeneratorAgent` → `ValidationLoop` (Validator + Refiner)
   - Generates industry-standard test cases (JIRA/Xray/TestRail compatible)
   - Iterative validation until quality approved

3. **Export Agent** (`app/agents/export_agent.py`)
   - CSV, Excel (styled), JSON export
   - JIRA integration (stub - needs credentials)

4. **Automation Agent** (`app/agents/automation_agent.py`)
   - Playwright POM stub generation

### Data Models (`app/models.py`)

Key models follow industry standards:
- `Requirement`: id, text
- `TestCase`: id, title, description, priority, type, status, preconditions, steps, expected_result, test_data, estimated_time, automation_status, component, tags
- `TestStep`: step number, action, expected, test_data

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| POST | `/requirements/parse` | Parse document or refine with feedback |
| POST | `/requirements/enrich` | Add context to requirements |
| POST | `/testcases/generate` | Generate test cases from requirements |
| POST | `/export/csv` | Download as CSV |
| POST | `/export/excel` | Download as styled XLSX |
| POST | `/export/json` | Download as JSON |
| POST | `/export/jira` | Export to JIRA (stub) |
| POST | `/automation/playwright` | Generate POM stubs |

## Development Workflow

### Running the Application

**Backend** (Terminal 1):
```bash
source .venv/bin/activate
uvicorn app.main:app --reload --app-dir backend
```

**Frontend** (Terminal 2):
```bash
cd frontend && npm run dev
```

**VS Code Tasks**: Use pre-configured tasks `Run backend (uvicorn)` and `Run frontend (vite)`

### Environment Setup

Copy `.env.example` to `.env` and configure:
```
GEMINI_API_KEY=your_key_here
MODEL_NAME=gemini-3-flash-preview
```

Note: The backend maps `GEMINI_API_KEY` to `GOOGLE_API_KEY` for ADK compatibility.

## Coding Conventions

### Python (Backend)
- Use type hints for all functions
- Pydantic models for request/response validation
- Async patterns with `asyncio` for ADK pipelines
- Logging for agent activity tracking
- Handle `nest_asyncio` for nested event loops in FastAPI

### React (Frontend)
- Functional components with hooks
- State managed via `useState`
- Fetch API for backend communication
- CSS class naming: `kebab-case` for components, BEM-like for modifiers

### Agent Development
- Agents use `include_contents='none'` when reading from state
- Use state keys for inter-agent communication (`STATE_*` constants)
- `exit_loop` tool pattern for loop termination
- JSON output extraction with `_extract_json()` helper

## Common Tasks

### Adding a New Export Format
1. Add format handler in `app/agents/export_agent.py`
2. Add endpoint in `app/main.py`
3. Add UI button in `frontend/src/App.jsx` (Export tab)

### Modifying Test Case Fields
1. Update `TestCase` model in `app/models.py`
2. Update agent prompts in `app/agents/test_case_agent.py`
3. Update table/card rendering in `App.jsx`
4. Update export functions in `export_agent.py`

### Adding New Agent
1. Create agent file in `backend/app/agents/`
2. Follow ADK patterns: `Agent`, `LoopAgent`, `SequentialAgent`
3. Use state keys for data passing
4. Add tool functions with `ToolContext` for actions

## Files to Know

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app and routes |
| `backend/app/models.py` | Pydantic data models |
| `backend/app/config.py` | Settings and API key management |
| `backend/app/adk_client.py` | ADK pipeline helpers |
| `backend/app/agents/*.py` | AI agents for each feature |
| `frontend/src/App.jsx` | Main UI component |
| `frontend/src/App.css` | Complete styling |

## Troubleshooting

- **Import errors**: Ensure virtual env is active and dependencies installed
- **API auth errors**: Verify `GEMINI_API_KEY` in `.env`
- **CORS issues**: Backend allows `localhost:5173` and `127.0.0.1:5173`
- **Agent loops not terminating**: Check `APPROVAL_PHRASE` matching in prompts
- **Empty test cases**: Check logging output for agent pipeline errors

## Future Enhancements (Stubs)

- [ ] JIRA integration - implement `app/adapters/jira.py`
- [ ] Playwright POM generation - expand `automation_agent.py`
- [ ] Additional integrations: Xray, TestRail, qTest, Azure DevOps
