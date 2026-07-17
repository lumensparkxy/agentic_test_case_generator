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
python scripts/evaluate_orchestrator.py --offline --strict
python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json --indent 0
python scripts/generate_frontend_api_types.py --check
python scripts/scan_codebase.py

cd frontend
npm ci
npm run lint
npm run format:check
npm run build
npm run test:e2e -- e2e/home-workspace.spec.js e2e/workflow-navigation.spec.js
npm run test:e2e -- e2e/use-case-review.spec.js e2e/orchestrator-lifecycle.spec.js
npm run test:e2e -- e2e/export-approval-gate.spec.js

cd backend/execution_runtime
npm ci
npm run test:playwright -- --list
```

The Playwright config expects an existing `E2E_BASE_URL`. For local frontend
E2E runs, start `npm run dev -- --host 127.0.0.1` in a separate frontend shell
and run the specs with `E2E_BASE_URL=http://127.0.0.1:5173`; CI performs the
same server startup and readiness check explicitly.

CI currently runs:

- Backend Ruff lint check.
- Backend Ruff format check.
- Backend unittest suite.
- Offline requirement evaluation.
- Offline generation evaluation.
- Offline orchestrator lifecycle evaluation.
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
- Orchestrator benchmark inputs and expectations:
  `scripts/benchmark_orchestrator_inputs/` and
  `scripts/benchmark_orchestrator_expectations/`.
- API payload fixtures: `scripts/api_payloads/`.
- Source-focused codebase scan: `scripts/scan_codebase.py`, with output in
  `docs/codebase/.codebase-scan.txt`.

## 3) Test Scope Matrix

| Scope | Covered? | Typical target | Notes |
|-------|----------|----------------|-------|
| Backend unit | Yes | Config parsing, auth, model parsing, agents, services, billing, reporting, grounding, execution conversion | Uses `unittest` and `unittest.mock.patch` |
| Specialist task contracts | Yes | Orchestrator agent registry manifest, typed input/output dispatch, trace propagation, malformed output diagnostics, and use-case dispatch without full test-case generation | `backend/tests/test_specialist_agent_registry.py` uses synthetic fixtures and patched local agents |
| Use-case planning coordinator | Yes | Requirement shard planning, original-order merge, scenario ID normalization, per-shard fallback, and parallel diagnostics | `backend/tests/test_use_case_agent.py` patches shard workers with synthetic fixtures |
| Test-case generation coordinator | Yes | Reuse of approved use-case artifacts, safe-size threshold routing, duplicate global ID remap, traceability repair, failed-shard fallback, and endpoint billing/versioning boundaries | `backend/tests/test_parallel_test_case_generation.py`, `backend/tests/test_config.py`, and `backend/tests/test_main_audit_logging.py` use synthetic fixtures and patched model workers |
| Automation fragment coordinator | Yes | Component/page grouping, all-case representation past old caps, manual/unsupported diagnostics, duplicate fragment/symbol handling, failed-shard fallback, and endpoint billing boundaries | `backend/tests/test_automation_agent.py`, `backend/tests/test_config.py`, and `backend/tests/test_automation_endpoint.py` use synthetic fixtures and patched automation workers |
| Orchestrator decisions | Yes | Stage health, approval blockers, stale downstream state, impact-analysis priority, apply-update blockers, execution/review/report actions | `backend/tests/test_orchestrator_service.py` uses fake Firestore project snapshots and `TestClient` |
| Use Cases review decisions | Yes | Machine-versus-human approval separation, matching-snapshot approval, approval and requested-change transitions, required comments, ownership, reviewer/timeline provenance, stale snapshot and revision conflicts, idempotent retries, snapshot immutability, downstream-staleness regression, and recomputed orchestrator actions | `backend/tests/test_main_audit_logging.py`, `backend/tests/test_orchestrator_service.py`, and `backend/tests/test_use_case_review_service.py` use synthetic generation responses and transaction-aware fake Firestore documents to verify decision-derived blockers, actions, and provenance |
| Orchestrator run persistence | Yes | Run creation/resume, idempotent events, checkpoint history, blockers, completion links, timeline endpoint payloads | `backend/tests/test_orchestrator_run_service.py` uses fake Firestore subcollections and `TestClient` |
| Offline orchestrator lifecycle benchmarks | Yes | v1 first-generation routing, v2 two-requirement impact precision, unchanged-test preservation versus full regeneration, resumability, and governance gates | `scripts/evaluate_orchestrator.py`, `backend/tests/test_orchestrator_evaluation.py`, `scripts/benchmark_orchestrator_*` |
| Frontend orchestrator cockpit | Yes | First-time project actions, resumed stale-impact action priority, collapsible right rail status overview, blockers, run/checkpoint timeline output, project evidence, last run, and reload restore | `frontend/e2e/orchestrator-cockpit.spec.js` uses mocked project, status, run, event, and checkpoint payloads |
| Frontend contextual task | Yes | Workbench-scoped and empty recommendations, one primary CTA and reason, hidden provenance/diagnostics/secondary actions, secondary-only regeneration, disabled blockers without mutation, semantic action routing, Use Cases approval routing without legacy generation or refinement bypass, incremental-update priority, and keyboard-safe full-regeneration cancel/confirm/failure/double-submit behavior with suite preservation and valid focus restoration after success | `frontend/e2e/contextual-next-action.spec.js` uses deterministic project, pending-approval, status, generation-success, delayed-success, and failure fixtures; `frontend/e2e/workflow-navigation.spec.js`, `frontend/e2e/use-case-review.spec.js`, and `frontend/e2e/impact-update-flow.spec.js` preserve shared routing and adjacent workflow behavior |
| Frontend orchestrator lifecycle | Yes | Create project, generate v1, reload, change requirements, impact update, execute, review, and report | `frontend/e2e/orchestrator-lifecycle.spec.js` uses synthetic project and orchestrator fixtures |
| Frontend Use Cases workbench | Yes | Exact-snapshot rendering, canonical scenario counts and requirement grouping, Use Cases coverage metrics, machine-versus-human review state, truthful prerequisites and headers, approve/request-changes payloads, durable reloads, stale conflicts, partial-refresh recovery, retry idempotency, reviewer provenance, required-field and focus semantics, 390/1440/1920 reflow, double-submit prevention, and late-response route isolation | `frontend/e2e/use-case-review.spec.js` uses deterministic project, workspace, orchestrator, success, conflict, superseding-artifact, and service-failure fixtures from `frontend/e2e/support/use-case-review.js`; `frontend/e2e/orchestrator-lifecycle.spec.js` preserves the adjacent project workflow |
| Multi-environment execution orchestration | Yes | Approved-suite automation recommendations, source test-case snapshot linkage, named environment run records, idempotent reruns, failed-run review signal, and project history visibility | `backend/tests/test_orchestrator_service.py`, `backend/tests/test_workflow_project_service.py`, `backend/tests/test_automation_endpoint.py`, and `frontend/e2e/multi-environment-execution.spec.js` use synthetic project and execution fixtures |
| Evidence-backed reporting | Yes | Report source snapshot IDs, execution run IDs, stale report regeneration, review/report actions, and latest report evidence visibility | `backend/tests/test_export_endpoint.py`, `backend/tests/test_orchestrator_service.py`, and `frontend/e2e/report-evidence.spec.js` use synthetic project/report fixtures |
| Workspace summary read model | Yes | Empty accounts, owner/archive isolation, authoritative stage/action normalization, Use Cases review counts, deterministic ranking/deduplication, bounded runs/reports, query validation, and safe persistence failure | `backend/tests/test_workspace_summary_service.py` and `backend/tests/test_workspace_summary_endpoint.py` use synthetic project fixtures and patched Firestore/service boundaries |
| Frontend Home and Projects | Yes | Zero/one/many-project Home states, subject-scoped and refreshed server ranking, stable My work groups, stale stored selection cleanup, bounded client search, create/open/clear behavior, delayed-create route/logout races, latest-wins project-list reads, loading/retry semantics, populated reflow at 390/760/900/1280/1440/1920, status containment, and canonical project links | `frontend/e2e/home-workspace.spec.js` uses deterministic workspace/project fixtures from `frontend/e2e/support/workspace.js`; `frontend/e2e/workflow-navigation.spec.js` preserves URL and browser-history authority |
| Frontend Review Inbox | Yes | Server-ranked ordering, exact project/stage/snapshot deduplication, distinct-snapshot preservation, actionable versus informational/completed views, stage/status filters without refetch, canonical rendered Use Cases/Requirements/Test Cases destinations, cold and cached loading/error/retry states, invalid-filter normalization, empty and filtered-empty states, route and dynamic-action focus, full keyboard order, long-name containment and reflow at 390/639/640/899/900/1280/1920, and removal after a durable Use Cases decision refreshes the shared summary | `frontend/e2e/review-inbox.spec.js` uses bounded synthetic workspace and Use Cases review fixtures; `frontend/e2e/workflow-navigation.spec.js` preserves global navigation and route authority |
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
| Backend execution runtime | Partial | Playwright Test availability, generated spec execution, consolidated run report mapping | Unit tests cover one Playwright invocation per run and per-case status mapping from the consolidated JSON report; runtime list check remains in validation; full run covered by `scripts/e2e_playwright_workflow.py` when backend and credentials are available |
| Coverage thresholds | [TODO] | [TODO] | No tracked coverage tool or threshold was found |

## 4) Mocking and Isolation Strategy

- Backend tests patch the shared Firestore adapter, provider adapters, auth
  dependencies, billing services, repository hooks, and agent calls at module
  boundaries.
- Orchestrator run persistence tests use project-scoped fake Firestore
  subcollections and request IDs/idempotency keys to prove retries do not create
  duplicate runs, events, snapshots, execution records, or checkpoints.
- Use Cases review tests exercise one transaction across the owned project,
  immutable snapshot, durable review decision, and project timeline event. They
  prove stale optimistic-concurrency guards write nothing and an identical
  request ID does not increment the revision or duplicate evidence.
- Multi-environment execution tests use named synthetic environments and
  source snapshot IDs to prove reruns preserve environment-specific history and
  failed executions become review signals without mutating upstream snapshots.
- Report evidence tests mock current project snapshots and execution runs so
  exported report snapshots cite exact source IDs and stale reports are visible
  as regeneration actions in the frontend.
- Orchestrator lifecycle evaluation uses deterministic project fixtures to
  compare full regeneration with targeted impact update behavior without live
  model calls or real operational data.
- Local JWT based browser/API tests are compatibility workflows. Real-backend
  runs must use `AUTH_TOKEN_MODE=firebase-or-backend-jwt`; production validation
  should exercise Firebase ID token verification.
- JIRA and Azure DevOps sync tests verify direct source metadata paths avoid
  unnecessary Firestore mapping reads where possible.
- Frontend focused E2E tests mock API responses and use a local Vite server.
- Projects menu E2E coverage verifies project selection, refresh, inline
  creation, and the absence of the former QA Project workspace card.
- Workflow shell collapse tests keep localStorage-backed layout preferences
  browser-local and verify that collapsed left/right rails do not change
  workflow behavior. Icon-specific assertions check that destination markers
  and collapse controls render as SVG icons while retaining accessible names.
- Workflow shell responsive tests cover 390px, 1280px, 1440px, and 1920px
  viewports. They verify the mobile stack, the laptop two-column shell with the
  project rail below the center, the desktop three-column shell, center-width
  targets, the width reclaimed by collapsed rails, and document-level overflow.
- Brighter Executive Cockpit design QA maps the selected visual target to
  tested surfaces in `docs/brighter-executive-cockpit-design-qa.md`.
- Offline evaluation scripts use deterministic fallback behavior instead of
  live Gemini calls.
- Execution service tests validate preview classification, conversion, and
  local runner behavior without requiring every generated case to hit a live
  product site.
- Execution assertion DSL supports deterministic page title, role-visible,
  CSS/test-id visible, checked/not checked, enabled/disabled, attribute
  equality, and count equality assertions. Unsupported runtime/CLI assertions,
  page-title-as-visible text, and attribute-name-as-visible text must remain
  preview diagnostics instead of generated `getByText(...)` checks.

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
9. Selected Playwright execution run with one consolidated report artifact and per-case result rows.

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
- Orchestrator decision, impact-routing, project lifecycle, or governance
  change: backend unittest plus `scripts/evaluate_orchestrator.py --offline
  --strict`; run `frontend/e2e/orchestrator-lifecycle.spec.js` for stitched
  browser workflow changes.
- API contract change: OpenAPI export.
- Frontend UI or API call change: frontend build and focused E2E if the flow is
  covered.
- Execution conversion/runtime change: backend execution tests and
  `backend/execution_runtime` Playwright list check.
- Project execution history or orchestrator execution change: workflow project,
  automation endpoint, orchestrator service, and focused multi-environment
  E2E tests.
- Report/export evidence or stale report decision change: export endpoint,
  orchestrator service, and focused report evidence E2E tests.
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
- `scripts/evaluate_orchestrator.py`
- `scripts/export_openapi.py`
- `scripts/e2e_playwright_workflow.py`
- `docs/requirements_traceability.md`
- `schemas/spec.schema.json`
- `schemas/ir.schema.json`
