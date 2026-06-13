# Testing Patterns

This guide explains the current validation gates and how to reproduce the first
next-version end-to-end Playwright documentation workflow.

## 1) Test Stack and Commands

Primary backend framework: Python `unittest`.

Primary frontend/E2E framework: Playwright Test.

Primary quality gates:

```bash
source .venv/bin/activate
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
npm ci
npm run lint
npm run format:check
npm run build
npm run test:e2e -- e2e/export-approval-gate.spec.js

cd backend/execution_runtime
npm ci
npm run test:playwright -- --list
```

CI currently runs:

- Backend Ruff lint check.
- Backend Ruff format check.
- Backend unittest suite.
- Offline requirement evaluation.
- Offline generation evaluation.
- OpenAPI export.
- Frontend API contract type freshness check.
- Frontend ESLint check.
- Frontend Prettier format check.
- Frontend production build.
- Focused mocked frontend E2E spec `e2e/export-approval-gate.spec.js`.

Evidence: `.github/workflows/ci.yml`.

## 2) Test Layout

- Backend tests: `backend/tests/test_*.py`.
- Frontend browser specs: `frontend/e2e/*.spec.js`.
- Frontend E2E support: `frontend/e2e/support/`.
- Execution runtime smoke/list command: `backend/execution_runtime/package.json`.
- Plain-English test framework source: `backend/plain_english_test_framework/`.
- Plain-English schema contracts: `schemas/spec.schema.json` and
  `schemas/ir.schema.json`.
- Benchmark inputs and expectations: `scripts/benchmark_*`.
- API payload fixtures: `scripts/api_payloads/`.
- Source-focused codebase scan: `scripts/scan_codebase.py`, with output in
  `docs/codebase/.codebase-scan.txt`.

## 3) Test Scope Matrix

| Scope | Covered? | Typical target | Notes |
|-------|----------|----------------|-------|
| Backend unit | Yes | Config parsing, auth, model parsing, agents, services, billing, reporting, grounding, execution conversion | Uses `unittest` and `unittest.mock.patch` |
| Specialist task contracts | Yes | Orchestrator agent registry manifest, typed input/output dispatch, trace propagation, malformed output diagnostics | `backend/tests/test_specialist_agent_registry.py` uses synthetic fixtures and patched local agents |
| Orchestrator run persistence | Yes | Run creation/resume, idempotent events, checkpoint history, blockers, completion links, timeline endpoint payloads | `backend/tests/test_orchestrator_run_service.py` uses fake Firestore subcollections and `TestClient` |
| Backend integration-style | Yes | FastAPI endpoints, JIRA/Azure DevOps import/sync routes, audit hooks, billing access | Uses `TestClient` and patched dependencies |
| Integration observability | Yes | JIRA/Azure DevOps provider metrics, duration summaries, and safe structured logs | `backend/tests/test_integration_observability.py`, adapter tests, and `backend/tests/test_observability_metrics.py` |
| Backend lint | Yes | Python syntax/import safety baseline | `python -m ruff check backend scripts` |
| Backend format check | Yes | Ruff formatter baseline | `python -m ruff format --check backend scripts` |
| Offline quality benchmarks | Yes | Requirement extraction and test-case generation quality | `--offline --strict` avoids live model dependency |
| OpenAPI contract | Yes | FastAPI schema export and generated frontend contract types | `scripts/export_openapi.py`, `scripts/generate_frontend_api_types.py --check` |
| Codebase scan | Yes | Tracked source and relevant config/docs | `scripts/scan_codebase.py` uses `git ls-files` plus explicit generated-output exclusions |
| Frontend lint | Yes | JavaScript/JSX baseline linting | `npm run lint` |
| Frontend format check | Yes | Prettier formatting baseline | `npm run format:check` |
| Frontend build | Yes | React/Vite production build | `npm run build` |
| Frontend E2E | Yes | Mocked browser workflow slices | `frontend/e2e/export-approval-gate.spec.js`, `workflow.spec.js`, `jira-workflow.spec.js` |
| Backend execution runtime | Partial | Playwright Test availability and generated spec execution | Runtime list check in validation; full run covered by `scripts/e2e_playwright_workflow.py` when backend and credentials are available |
| Coverage thresholds | [TODO] | [TODO] | No tracked coverage tool or threshold was found |

## 4) Mocking and Isolation Strategy

- Backend tests patch the shared Firestore adapter, provider adapters, auth
  dependencies, billing services, repository hooks, and agent calls at module
  boundaries.
- Orchestrator run persistence tests use project-scoped fake Firestore
  subcollections and request IDs/idempotency keys to prove retries do not create
  duplicate runs, events, snapshots, execution records, or checkpoints.
- Local JWT based browser/API tests are compatibility workflows. Real-backend
  runs must use `AUTH_TOKEN_MODE=firebase-or-backend-jwt`; production validation
  should exercise Firebase ID token verification.
- JIRA and Azure DevOps sync tests verify direct source metadata paths avoid
  unnecessary Firestore mapping reads where possible.
- Frontend focused E2E tests mock API responses and use a local Vite server.
- Offline evaluation scripts use deterministic fallback behavior instead of
  live Gemini calls.
- Execution service tests validate preview classification, conversion, and
  local runner behavior without requiring every generated case to hit a live
  product site.

Common failure modes:

- Missing `.venv` dependencies for backend commands.
- Missing `frontend/node_modules` or `backend/execution_runtime/node_modules`.
- Browser channel mismatch for execution runtime; defaults target Microsoft
  Edge through `EXECUTION_BROWSER_CHANNEL` / `PETF_PLAYWRIGHT_BROWSER_CHANNEL`.
- Firestore/Firebase credentials missing in local runs that exercise real
  Firebase-backed paths.
- Live model or external documentation workflows taking longer than unit tests.

## 5) Reproduce the Next-Version E2E Success

The first successful next-version workflow is captured by
`scripts/e2e_playwright_workflow.py`. It exercises:

1. Local JWT minting in the documented compatibility mode.
2. Markdown requirement parsing from `scripts/playwright_docs_requirements.md`.
3. Human feedback requirement refinement.
4. Grounded context enrichment against Playwright for Python documentation URLs.
5. Test-case generation with analysis, coverage, traceability, and review.
6. CSV, XLSX, and JSON export.
7. Playwright POM stub generation.
8. Execution preview.
9. Selected Playwright execution run and report artifact generation.

Prerequisites:

```bash
source .venv/bin/activate
python -m pip install -r backend/requirements.txt

cd backend/execution_runtime
npm ci
cd ../..
```

Start the backend:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --app-dir backend --reload-dir backend
```

Run the E2E workflow in another terminal:

```bash
source .venv/bin/activate
python scripts/e2e_playwright_workflow.py
```

Expected outputs:

- JSON workflow snapshots and exports under `/tmp/pw_workflow_out`.
- Execution specs, IR, generated Playwright tests, and reports under
  `.execution_artifacts/exec_*` when execution is enabled.
- Console summary showing parsed/refined/enriched requirements, generated test
  cases, executable preview counts, and execution run status.

Generated E2E and execution outputs are ignored by git but can contain
screenshots, traces, reports, exports, and generated specs. Review the dry-run
cleanup plan before deleting old outputs:

```bash
python scripts/cleanup_generated_artifacts.py
```

The command defaults to files older than 14 days across `.execution_artifacts/`,
`client_submission/`, and `/tmp/pw_workflow_out`. Use `--apply` only after
reviewing the dry-run output, and use `--max-age-days 7 --apply` for approved
real-data cleanup. See `docs/artifact-retention-policy.md`.

Recorded validation evidence:

- Issue #15 evidence in `docs/requirements_traceability.md` records a full
  workflow against `https://playwright.dev/python/` that parsed requirements,
  generated approved test cases, exported CSV/XLSX/JSON, and generated
  Playwright POM stubs.
- Issue #16 evidence records grounded documentation URLs, extracted UI
  elements, executable preview, and 5 selected Playwright report cases passing.

Use this script for release confidence when changes affect requirement parsing,
grounded context, generation, exports, automation, execution preview, or the
plain-English framework. It is slower and more environment-sensitive than the
offline CI gates, so do not replace unit and offline benchmark checks with it.

## 6) Validation Gate Selection

Use the smallest gate that proves the change:

- Backend service/router/model change: backend unittest plus focused tests.
- Agent, fallback, coverage, or parsing change: backend unittest plus both
  offline evaluation scripts.
- API contract change: OpenAPI export.
- Frontend UI or API call change: frontend build and focused E2E if the flow is
  covered.
- Execution conversion/runtime change: backend execution tests and
  `backend/execution_runtime` Playwright list check.
- End-to-end workflow or release confidence change: run
  `scripts/e2e_playwright_workflow.py`.

When a gate cannot be run, record the command, blocker, and remaining risk in
the issue, PR, or handoff note.

## 7) Evidence

- `.github/workflows/ci.yml`
- `pyproject.toml`
- `backend/requirements-dev.txt`
- `frontend/eslint.config.js`
- `frontend/.prettierrc.json`
- `frontend/package.json`
- `backend/tests/`
- `frontend/e2e/`
- `frontend/playwright.config.js`
- `backend/execution_runtime/package.json`
- `backend/execution_runtime/playwright.config.ts`
- `scripts/evaluate_requirements.py`
- `scripts/evaluate_generation.py`
- `scripts/export_openapi.py`
- `scripts/e2e_playwright_workflow.py`
- `docs/requirements_traceability.md`
- `schemas/spec.schema.json`
- `schemas/ir.schema.json`
