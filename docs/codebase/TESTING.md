# Testing Patterns

This guide explains the current validation gates and how to reproduce the first
next-version end-to-end Playwright documentation workflow.

## 1) Test Stack and Commands

Primary backend framework: Python `unittest`.

Primary frontend/E2E framework: Playwright Test.

### Select local checks by changed behavior

Use this matrix before running commands. The critical CI gate remains required on
PRs; it need not be repeated locally for every small edit. Choose the union of
checks for affected areas and broaden when shared behavior or uncertainty warrants
it. Add focused regression tests for changed behavior, not tests of prose or
implementation details. Record results once in the PR.

| Change | Local verification |
| --- | --- |
| Documentation, instructions, or skill metadata | `git diff --check`; check changed local links, referenced commands, policy consistency, and skill metadata/behavior when relevant. No application suite is needed for prose alone. |
| Local backend behavior | Affected unittest modules; Ruff lint and formatting on changed Python files. |
| UI behavior or styling | Frontend build, relevant changed-file lint/format checks, and affected browser specs. Inspect the rendered interaction; for layout changes include the affected desktop/mobile sizes. |
| API contracts | Backend contract tests, OpenAPI export, generated-type freshness, and affected frontend build/browser checks. |
| Generation or orchestration | Affected backend tests and the corresponding strict offline requirements, generation, or orchestrator benchmark. Add browser checks when workflow behavior changes. |
| Shared infrastructure or dependencies | Broader affected backend/frontend/runtime suites, relevant offline benchmarks, and contract checks. A shared cross-stack change requires both backend and frontend gates. |
| Execution runtime | Affected compiler/execution tests and runtime Playwright discovery; execute the affected synthetic scenario when behavior changes. Listing tests alone does not establish execution correctness. |

Reuse working dependencies and servers. Install only when absent, stale, or
changed by the task. A new backend environment needs both `backend/requirements.txt`
and `backend/requirements-dev.txt`; use the repo-local `.venv`. For a new frontend
or runtime environment use `npm ci` in that component. Do not run package upgrades,
skill installers, Firebase login, or cloud setup as routine local validation.
Rerun a passing check after relevant edits, dependency/base changes, failures, or
new evidence; do not rerun it solely to repeat the result.

### Focused command examples

Run Python commands from the repository root after activating `.venv`. Replace
the example targets with the files/tests affected by the task:

```bash
source .venv/bin/activate
python -m unittest discover -s backend/tests -p 'test_config.py'
python -m ruff check backend/app/config.py backend/tests/test_config.py
python -m ruff format --check backend/app/config.py backend/tests/test_config.py
```

Run frontend commands from `frontend/` with the existing dependencies:

```bash
npm exec -- eslint src/app/workflowRoutes.js
npm exec -- prettier --check src/app/workflowRoutes.js
npm run build
E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e -- e2e/workflow-navigation.spec.js
```

Read `frontend/package.json` and neighboring specs when selecting targets. Use
mocked API fixtures and synthetic sessions for local UI checks unless the task
requires a live integration. For a reusable browser workflow, see
[repository-qa](../../.agents/skills/repository-qa/SKILL.md).

### Broader checks when the change requires them

From the repository root, with `.venv` activated:

```bash
python -m ruff check backend scripts
python -m ruff format --check backend scripts
python -m unittest discover -s backend/tests -p 'test_*.py'
python scripts/evaluate_requirements.py --offline --strict
python scripts/evaluate_generation.py --offline --strict
python scripts/evaluate_orchestrator.py --offline --strict
python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json --indent 0
python scripts/generate_frontend_api_types.py --check
```

From `frontend/`:

```bash
npm run lint
npm run format:check
npm run build
E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e:home-first
```

From `backend/execution_runtime/` when runtime code changes:

```bash
npm run test:playwright -- --list
```

Codebase scans are for explicit mapping/documentation tasks and are not a
validation prerequisite. Instruction/skill maintenance is described in
[Developer skills](../developer-skills.md).

The Playwright config expects an existing `E2E_BASE_URL`. For local frontend
E2E runs, start `npm run dev -- --host 127.0.0.1` in a separate frontend shell
and run the specs with `E2E_BASE_URL=http://127.0.0.1:5173`; CI performs the
same server startup and readiness check explicitly. The protected frontend
gate runs only Priority 1 (P1) cases:

```bash
CI=1 E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e:p1
```

The P1 script filters the existing `test:e2e:home-first` file list by `@p1`,
so it cannot accidentally include the separate live-service workflow specs.
An empty selection fails through Playwright's default no-tests behavior.
The script is intentionally synthetic: it uses mocked API fixtures, a seeded
test session, and local Vite only. It does not call Firebase, a model, a live
backend, production, or an external integration.

To run every offline browser spec, start Vite from `frontend/` with dummy
Firebase web settings. These settings expose the anonymous provider chooser for
the UI checks. The tests use a disposable JWT for authenticated fixtures.

```bash
VITE_API_BASE=http://127.0.0.1:8000 \
VITE_FIREBASE_API_KEY=offline-ui-smoke-key \
VITE_FIREBASE_AUTH_DOMAIN=offline-ui-smoke.example.test \
VITE_FIREBASE_PROJECT_ID=demo-offline-ui-smoke \
VITE_FIREBASE_APP_ID=1:12345:web:0000000000000000000000 \
VITE_FIREBASE_ENABLE_GOOGLE_AUTH=true \
VITE_FIREBASE_ENABLE_MICROSOFT_AUTH=true \
VITE_FIREBASE_ENABLE_APPLE_AUTH=true \
npm run dev -- --host 127.0.0.1 --strictPort
```

In another `frontend/` shell, run the complete selection with the live suites
disabled:

```bash
JWT_SECRET_KEY=local-chromium-regression-test-secret \
E2E_BASE_URL=http://127.0.0.1:5173 E2E_LIVE=0 E2E_LOCAL_WORKBENCH=0 \
npm run test:e2e -- --workers=4 --retries=0
```

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
- The P1 mocked E2E gate described below. The full responsive, accessibility,
  presentation, and edge-case matrices remain available separately.
- Playwright HTML reports, failure screenshots/videos, and retry traces uploaded
  as `frontend-playwright-<run>-<attempt>` Actions artifacts for 14 days. No
  generated artifact is committed.

Evidence: `.github/workflows/ci.yml`.

### Critical regression selection

Routine CI runs **67 selected backend methods and 28 browser cases**. This
replaces 577 backend methods and 50 previously tagged browser cases in the
required gate. Offline benchmarks, API contracts, lint, formatting and frontend
build checks remain required. Test selection is independent of priorities in
generated customer test suites.

Run the small gate:

```bash
# Repository root; use .venv
python scripts/run_backend_p1.py --list
python scripts/run_backend_p1.py

# frontend/; start Vite or use an existing local server
npm test -- --list
E2E_BASE_URL=http://localhost:5173 npm test
```

The backend allowlist is [priority1.txt](../../backend/tests/priority1.txt).
It selects explicit methods, with a failing command if an entry is missing,
duplicated or accidentally expands to a whole module/class. It covers auth and
ownership, unsafe artifact URLs, billing admission, import/review consistency,
concurrent updates, retained source approval, truthful model output, durable
repair, export gates and execution admission. A test with multiple subcases
still counts as one method; counts are not a measure of complete coverage.

Browser `@p1` tags select the following distinct critical outcomes:

| Spec | Cases | Critical outcome |
| --- | ---: | --- |
| `quality-feedback` | 1 | Record a missing login, backfill a non-secret reference, propose scoped learning and retain pending verification |
| `home-workspace` | 2 | Create a durable project; reject prior-user state after logout |
| `requirement-import-review` | 2 | Preserve imported identities/approvals; reject stale apply |
| `use-case-generation` | 3 | New generation requires review; unapproved requirements block generation; guidance stays singular across generation/review/reload without React key errors |
| `use-case-review` | 2 | Review exact snapshot/revision; preserve feedback on conflict |
| `scenario-review-table` | 2 | Edits revoke approval; structural score cannot replace semantics/human review |
| `context-notes` | 1 | Save notes without links, preserve approved requirements, and restore exact notes after reload |
| `contextual-next-action` | 2 | Confirm regeneration safely; saved enriched context survives reload |
| `test-generation-repair` | 2 | Explicit repair requires review; failure preserves the suite |
| `impact-application` | 4 | Submit once; recover across tabs/refresh; reconcile lost success; reject cross-project completion |
| `improve-tests` | 1 | Failed save preserves feedback and suite for retry |
| `export-approval-gate` | 1 | Draft override needs a reason and exports inspectable formats |
| `automation-preview-consistency` | 2 | Only selected executable IDs run; blocked readiness sends no run request |
| `knowledge` | 2 | Effective guidance/holds are visible; unavailable capability never implies enabled |
| `accessibility-navigation` | 1 | Use Cases has no serious/critical Axe violations at 1440px |

The complete backend and **282 mocked browser** suites remain available on
demand. No assertions or tests were deleted. The critical gate omits alternate
viewports, secondary navigation/filter/report cases, mocked JIRA journeys and
many alternate failure variants. Run affected specs/modules for those changes;
use full suites for shared changes or release validation:

```bash
# Repository root
python -m unittest discover -s backend/tests -p 'test_*.py'
# frontend/
npm run test:e2e:home-first -- --list
E2E_BASE_URL=http://localhost:5173 npm run test:e2e:home-first
```

`npm run test:e2e -- <spec>` remains available for an affected spec without the
P1 filter. `npm test` / `npm run test:e2e:p1` are the default critical selection.
The browser cases use mocked APIs; they do not establish live model, Firebase,
external integration or complete responsive/accessibility behavior. Add a P1
entry only for a distinct critical risk; avoid tagging every equivalent matrix
variant. Review discovery output whenever the selection changes.

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
| Frontend orchestrator cockpit | Yes | First-time project actions, resumed stale-impact action priority, contextual guidance after reload, and absence of the duplicate project information rail | `frontend/e2e/orchestrator-cockpit.spec.js` uses mocked project and status payloads |
| Frontend contextual task | Yes | Workbench-scoped and empty recommendations, one primary CTA and reason, hidden provenance/diagnostics/secondary actions, secondary-only regeneration, disabled blockers without mutation, semantic action routing, Use Cases approval routing without legacy generation or refinement bypass, incremental-update priority, and keyboard-safe full-regeneration cancel/confirm/failure/double-submit behavior with suite preservation and valid focus restoration after success | `frontend/e2e/contextual-next-action.spec.js` uses deterministic project, pending-approval, status, generation-success, delayed-success, and failure fixtures; `frontend/e2e/workflow-navigation.spec.js`, `frontend/e2e/use-case-review.spec.js`, and `frontend/e2e/impact-update-flow.spec.js` preserve shared routing and adjacent workflow behavior |
| Frontend responsive shell | Yes | Global/project containment at 320/390/640/760/900/1280/1440/1920 CSS pixels, initial-viewport task visibility, compact disclosure keyboard behavior and focus restoration, exactly one route-current item, non-color navigation-state semantics, two-pixel overflow tolerance, and absence of the duplicate status rail | `frontend/e2e/responsive-reflow.spec.js` uses deterministic long project/localized-label fixtures and shared bounding-box, viewport, active-state, and scroll-region assertions from `frontend/e2e/support/layout.js`; `frontend/e2e/frontend-css-smoke.spec.js` and `frontend/e2e/workflow-navigation.spec.js` preserve adjacent shell surfaces and route behavior |
| Frontend orchestrator lifecycle | Yes | Create project, generate v1, reload, change requirements, impact update, execute, review, and report | `frontend/e2e/orchestrator-lifecycle.spec.js` uses synthetic project and orchestrator fixtures |
| Frontend Use Cases workbench | Yes | Exact-snapshot rendering, canonical scenario counts and requirement grouping, Use Cases coverage metrics, machine-versus-human review state, truthful prerequisites and headers, approve/request-changes payloads, durable reloads, stale conflicts, partial-refresh recovery, retry idempotency, reviewer provenance, required-field and focus semantics, 390/1440/1920 reflow, double-submit prevention, and late-response route isolation | `frontend/e2e/use-case-review.spec.js` uses deterministic project, workspace, orchestrator, success, conflict, superseding-artifact, and service-failure fixtures from `frontend/e2e/support/use-case-review.js`; `frontend/e2e/orchestrator-lifecycle.spec.js` preserves the adjacent project workflow |
| Multi-environment execution orchestration | Yes | Approved-suite automation recommendations, source test-case snapshot linkage, named environment run records, idempotent reruns, failed-run review signal, and project history visibility | `backend/tests/test_orchestrator_service.py`, `backend/tests/test_workflow_project_service.py`, `backend/tests/test_automation_endpoint.py`, and `frontend/e2e/multi-environment-execution.spec.js` use synthetic project and execution fixtures |
| Automation preview consistency | Yes | Empty, executable, mixed, unsupported, and invalid count/list invariants; malformed summaries and responses; duplicate/slug-colliding candidate IDs with repeated source IDs; persisted/live hydration; target-change and execution reset; latest-response ownership; selected-candidate Run counts and request IDs; zero-selection request suppression; contextual preview-before-run behavior; and distinct no-preview versus zero-executable states | `backend/tests/test_execution_service.py` and `backend/tests/test_automation_endpoint.py` cover the producer contract, collision-safe candidate execution, and persisted snapshot fields; `frontend/e2e/automation-preview-consistency.spec.js` covers mismatched live summaries, persisted compact previews, stale-snapshot refresh gating, selection preservation, preview/run response races, target reset, malformed payloads, contextual Execute, and empty-state consumers; `frontend/e2e/multi-environment-execution.spec.js` preserves named-environment run compatibility |
| Evidence-backed reporting | Yes | Report source snapshot IDs, execution run IDs, stale report regeneration, review/report actions, and latest report evidence visibility | `backend/tests/test_export_endpoint.py`, `backend/tests/test_orchestrator_service.py`, and `frontend/e2e/report-evidence.spec.js` use synthetic project/report fixtures |
| Workspace summary read model | Yes | Empty accounts, owner/archive isolation, authoritative stage/action normalization, Use Cases review counts, deterministic ranking/deduplication, bounded runs/reports, query validation, and safe persistence failure | `backend/tests/test_workspace_summary_service.py` and `backend/tests/test_workspace_summary_endpoint.py` use synthetic project fixtures and patched Firestore/service boundaries |
| Frontend Home and Projects | Yes | Zero/one/many-project Home states, subject-scoped and refreshed server ranking, stable My work groups, stale stored selection cleanup, bounded client search, create/open/clear behavior, delayed-create route/logout races, latest-wins project-list reads, loading/retry semantics, populated reflow at 320/390/640/760/900/1280/1440/1920, status containment, and canonical project links | `frontend/e2e/home-workspace.spec.js` uses deterministic workspace/project fixtures from `frontend/e2e/support/workspace.js`; `frontend/e2e/workflow-navigation.spec.js` preserves URL and browser-history authority |
| Frontend Review Inbox | Yes | Server-ranked ordering, exact project/stage/snapshot deduplication, distinct-snapshot preservation, actionable versus informational/completed views, stage/status filters without refetch, canonical rendered Use Cases/Requirements/Test Cases destinations, cold and cached loading/error/retry states, invalid-filter normalization, empty and filtered-empty states, route and dynamic-action focus, full keyboard order, long-name containment and reflow at 390/639/640/899/900/1280/1920, and removal after a durable Use Cases decision refreshes the shared summary | `frontend/e2e/review-inbox.spec.js` uses bounded synthetic workspace and Use Cases review fixtures; `frontend/e2e/workflow-navigation.spec.js` preserves global navigation and route authority |
| Frontend Runs and Reports indexes | Yes | Bounded workspace-summary projections, local search/status/environment/type/format filters without refetch or mutation, exact durable run status/count/timestamp rendering, non-color approved/draft/stale report status and evidence identity, canonical project Automation/Reports links, source-empty and filtered-empty states, cold loading/error/retry behavior, predictable route/filter/retry focus, and long-content containment from 320px through 1920px | `frontend/e2e/runs-reports-index.spec.js` uses deterministic workspace activity fixtures; `frontend/e2e/workflow-navigation.spec.js` preserves global destination, URL, and browser-history authority |
| Home-first UX and accessibility release gate | Yes | Zero/one/many-project route truth, stale-state isolation, exact action routing, review durability, mixed/empty/inconsistent Automation previews, selected candidate IDs, Home/Overview/Use Cases/contextual task/Automation/Reports reflow at 320/390/640/760/900/1280/1440/1920, global skip link, route and history focus, compact disclosure restoration, persistent polite status, assertive errors, busy states, and WCAG A/AA Axe scans at 390/1440 | `npm run test:e2e:home-first` retains the full mocked regression suite; required CI runs only its `@p1` subset via `npm run test:e2e:p1`. `frontend/e2e/accessibility-navigation.spec.js` and `frontend/e2e/support/accessibility.js` scan empty/populated Home, Overview, Use Cases, and Automation with no Axe exclusions and fail on serious/critical violations. Manual VoiceOver + Chrome steps and results live in `docs/home-first-accessibility-smoke.md`; this evidence is not WCAG certification. |
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
- Automation preview backend tests deliberately provide mismatched summaries
  and duplicate or slug-colliding IDs; browser tests provide a mismatched live
  summary, repeated source IDs with unique candidate IDs, a stale compact
  project snapshot, zero executable collections, malformed response bodies,
  changed targets, and deliberately out-of-order preview/run responses.
  Together they prove every emitted and persisted count matches its collection,
  unsafe or obsolete data cannot become runnable, contextual Execute stops for
  candidate review, and the run payload contains only selected candidate IDs
  from the current executable collection.
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
- The Home-first accessibility helper disables animation only to obtain a
  deterministic final visual state, applies supported WCAG A/AA rule tags, and
  does not exclude selectors or rules. Empty and populated Home scans wait for
  their distinct final states before analysis.
- Playwright keeps a trace on first retry, a screenshot on failure, and video on
  failure. CI uploads the report and result directories even when a retry later
  passes, then expires the diagnostic artifact after 14 days.
- Projects menu E2E coverage verifies project selection, refresh, inline
  creation, and the absence of the former QA Project workspace card.
- Workflow shell collapse tests keep the left-navigation localStorage-backed
  preference browser-local and verify that it does not change workflow behavior.
  Icon-specific assertions check that destination markers and the collapse
  control render as SVG icons while retaining accessible names.
- Workflow shell responsive tests cover 320px and 640px reflow proxies plus
  390px, 760px, 900px, 1280px, 1440px, and 1920px shell states. They verify
  compact global/project disclosures, initial-viewport task access, long-label
  containment, absence of the project information rail, and document-level
  overflow with a two-pixel rendering tolerance.
  Manual PR evidence at actual Chrome 200% and 400% zoom from a 1280px window is
  still required; automated proxy widths do not replace browser zoom validation.
- Horizontal scrolling assertions allow only focusable, named `role="region"`
  containers that are, or directly contain, an intrinsic table or code surface.
  Ordinary navigation, status, tabs, and page layout must reflow without their
  own horizontal scrollers.
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

Run this live script when the requested integration or release validation needs
it and the environment/model calls are authorized. It is slower and more
environment-sensitive than offline checks; it is not a prerequisite for a local
parsing or UI fix and does not replace unit or offline benchmark evidence.

### Live Room Booking business journey

Coverage normalization preserves nonempty model scenario groups without adding
generic scenarios for absent categories. Missing or empty groups still receive
fallback scaffolding, which is not semantic approval. Regression coverage in
`test_analysis_coverage_metrics.py` and `test_use_case_agent.py` checks repeated
normalization, identifiers, ownership, sequential/parallel preservation, and
fallback review behavior. The structural and semantic quality gates remain in
force; removing server-added padding does not prove the model output is adequate.

Use Case semantic review compares the complete plan across requirement groups,
including groups planned by separate workers. `test_scenario_assessment.py` and
`test_use_case_agent.py` cover exact cross-group duplicate warnings, whole-plan
review binding, source mapping findings, and bounded reviewer failure without
discarding scenarios or claiming a pass. Paraphrase and business ownership checks
still require the semantic reviewer and inspection of the live delivered plan;
mocked verdicts establish transport/validation behavior, not model accuracy.

When a current Use Cases snapshot has explicit approval, Test Case generation
uses its saved plan and keeps its review and snapshot identity. The generation
source and parallel generation tests cover server-side plan selection, exact
scenario IDs, legacy requests and revision conflicts. In Chrome, compare the
approved Use Cases version and decision before and after generation and reload,
then verify that the downloaded Excel audit names the same Use Cases snapshot.

Stage 5 regressions in `test_substantive_review.py`, `test_test_case_quality.py`
and `improve-tests.spec.js` cover bounded recovery from transient assessment
failure, rejection of invalid/stale evidence, and unchanged score/actionable
findings and delivered traceability metrics after response, persistence and
reload. Legacy placeholder rows still
invalidate the scored suite. The Room Booking Chrome check additionally reviews
actor/state sequencing, unsupported permission or request-level assumptions,
and the retry identity/count assertions; mocked checks do not establish live
model quality or provision the synthetic execution harness.

The v3 delivered-suite reviewer cites a requirement ID and `source_field`
(`text` or nonempty `source_excerpt`). The server resolves the exact source text
from the same hash-bound review input, as it does for compact step references.
Legacy full-text citations still require an exact match; paraphrases are never
silently corrected. Unknown, empty or conflicting references fail closed. Safe
validation categories distinguish citation, binding, completeness and schema
failures without exposing raw model values. Cancellation review permits any
source-supported denial mechanism, including a visible action denied on use;
it does not invent a hidden/disabled-control requirement.

The Room Booking response-loss prerequisite now has a reusable synthetic harness
in `scripts/response_loss_proxy.py`. Verify its transport contract separately:

```bash
.venv/bin/python -m unittest discover -s backend/tests -p test_response_loss_proxy.py -v
```

These tests start and clean up loopback listeners. The proxy forwards the initial
submission, completely reads the successful response and records its ID before
closing the client connection. It forwards the identical retry without adding
deduplication. Error responses and unrelated requests do not consume the loss;
missing ID/evidence-write failure cannot claim successful capture. The reference
stub intentionally returns a stable ID: this verifies the proxy, not application
retry behavior, browser integration, or durable storage across server restarts.
The main transport test is included in the critical backend gate.

For a separately running synthetic HTTP target, use
`python scripts/response_loss_proxy.py --help` to supply its loopback port, exact
submission path, top-level JSON string ID field, and an evidence file outside
the repository. Each instance suppresses only its first matching 2xx response;
restart it to arm a new isolated test. Only small GET/POST fixtures are supported
(1 MiB bodies, five-second socket timeouts); it is not an HTTPS interception or
general production proxy. The original response and retry are never synthesized.

### Synthetic Room Booking execution target

Run the authored Chromium controls against a healthy loopback app and six
independently faulted variants. The command uses the installed frontend
Playwright and Chromium from the usual local test setup.

```bash
.venv/bin/python scripts/evaluate_room_booking.py --output /tmp/tcg-room-booking-matrix
```

Use a new output directory for each run. The matrix lists the expected and
observed checks for each control. Detection requires that the same control
passes against the healthy fixture and produces the specified faulty result in
the broken version. Response-loss evidence includes the original ID and request
and response hashes. The browser compares the retry body hash and the displayed
record after reload. No provider key or Firebase configuration is required.

For manual exploration, start `.venv/bin/python scripts/room_booking_fixture.py`
and open the printed loopback URL. **Sign in as Alice** and **Sign in as Bob**
select synthetic actors within the same isolated browser session. A fresh
Chromium context starts from Alice's Confirmed Atlas booking `B-EXISTING`. The
form exposes exact labels **Room**, **Time slot**, **Attendees**, and **Book**.
The second listed slot starts free. **My bookings** shows the current actor's
bookings. `/bookings/B-EXISTING` opens the direct booking page. Cancel attempts
are checked by the server even when the direct page offers the button.

The retry control binds the existing proxy to `POST /api/bookings` and reads
its top-level `id`. The app owns the submission identity and booking state.
The proxy owns the one-shot transport fault. Booking state persists through
browser reload for the life of the fixture process. These authored controls
establish synthetic target behavior and defect detection. Live generated-case
handoff and independent business acceptance require their own evidence.

`frontend/e2e/live-project-sync.spec.js` exercises the real-provider shared
correction journey on an isolated synthetic Draft order project. It imports and
reviews source, saves context, requires passing scenario and delivered-suite
quality gates, then changes the quantity range from 1–8 to 1–6 through the
shared-correction UI. It checks pinned source/decision manifests, corrected
output, unchanged independent cases, renewed review, durable reload and exact
request replay. Automation is optional and is not requested by this fixture.
With the local prerequisites below, run:

```bash
E2E_LIVE=1 E2E_API_BASE=http://127.0.0.1:8000 E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e -- e2e/live-project-sync.spec.js --workers=1 --retries=0
```

Use matching frontend API configuration and backend CORS origins if using other
loopback ports. Start the backend from the checkout being tested; an older
non-reloading process does not verify newly merged code. The test retains live
source, planning, generation and correction evidence and archives only its own
uniquely named project, including after a failed gate. Reviewer actions are
explicit synthetic setup. Passing this bounded design fixture does not prove
production behavior, execution against a target, or the separate Room Booking
acceptance criteria.

`frontend/e2e/workflow.spec.js` uses the current project workbenches. The live
scenario is opt-in; the default full suite reports it as skipped. Its anonymous
sign-in check remains offline. Start frontend and backend from the same checkout
with the README commands and matching `VITE_API_BASE`, API CORS origin and ports.
The backend needs its existing Gemini and Firestore credentials plus
`AUTH_TOKEN_MODE=firebase-or-backend-jwt`; frontend and backend must share the
local JWT secret. Do not use production or enable held guidance stages for this
check. `E2E_API_BASE` must be loopback.

From `frontend/`, against those running servers:

```bash
E2E_LIVE=1 E2E_API_BASE=http://127.0.0.1:8000 E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e -- e2e/workflow.spec.js
```

The live scenario creates one uniquely named, test-owned Room Booking project,
imports the real fixture through Requirements, explicitly reviews it, compares
and cancels an 8-to-6 change, enters synthetic notes in the Context workbench,
saves with Analyze Context, verifies reload persistence, and generates Use Cases.
The notes setup uses the UI rather than a direct enrichment API write.
`context-notes.spec.js` covers notes-only input, failure/retry, links, project
isolation, and reload without live services; downstream notes propagation is
also covered by `contextual-next-action.spec.js`.
It stops before synthetic reviewer approval when semantic or structural review
fails. If those gates pass, it generates real cases, checks structured quality,
reloads saved identities and inspects a downloaded JSON export. An incomplete
suite requires an explicit draft reason and still fails business acceptance.
There is no booking system to execute against; no test claims target execution
or real human acceptance. No model retries are configured at the test-runner
level; backend workflow retry settings remain visible in responses.

Each run saves source/response/manifest/revision evidence, checkout and source
hashes, timings and any blocking gate under `frontend/test-results/`. Traces and
videos are disabled for this live spec to avoid recording auth payloads. The
fixture archives only the exact project whose ID, owner and unique name match
this run, retaining final evidence even on failure. Keep failed-run evidence
outside `test-results` before the next run clears it. Actual model names are in
guidance manifests; correlate captured request IDs with backend logs for
provider and usage. Unavailable usage or cost is unknown, never zero.

The Room Booking context records the implemented, transport-verified response-loss
harness described above. Real-target binding and booking behavior still require
verification before execution. The live harness uses the current `Start generation`
and JSON export actions, with bounded UI waits and separate model-response waits.

This is a gated live journey, not complete release acceptance. The audit run
stopped on scenario semantics, so downstream generation, repair and export were
not exercised in that run. Source apply/re-review/targeted repair, stale/retry,
and draft/approved export regressions remain in the scoped
`requirement-import-review`, `use-case-review`, `impact-application`,
`test-generation-repair` and `export-approval-gate` specs. Those mocked checks do
not prove the entire source-change path against live generation. Resume that
live path after the upstream quality gate is resolved; do not bypass the gate
or substitute mocked output to make this scenario green.

### Browser fault-detection benchmark

With the local execution runtime and Chromium installed, run the authored
controls and an optional live comparison from the repository root.

```bash
.venv/bin/python scripts/evaluate_browser_handoff.py --output /tmp/tcg-browser-controls.json
.venv/bin/python scripts/evaluate_browser_handoff.py --live --output /tmp/tcg-browser-live.json
```

The live command uses the configured Gemini credentials and generates one
suite per synthetic workflow. The server binds to loopback. Use the same
`--port` to preserve the fixture input hashes between runs. The default port
is 58946. Both modes retain the report and Chromium artifacts outside the
repository. The live command also saves the generation evidence beside its
report.

A defect counts as detected when a case linked to its requirement passes on
the healthy fixture and fails at the intended check on the broken fixture.
The report lists missing cases, healthy-target failures, surviving defects,
failures at another check, and unavailable runtime evidence separately.
Manual audit coverage and generation approval have separate fields. A
nonzero exit signals an unmet benchmark criterion.

### Browser target binding

Run the opt-in Chromium test with the local execution dependencies installed.
It starts two loopback origins and compares their request logs. Relative,
same-origin, rebased and same-origin redirect cases pass on the selected
origin. Unverified authored URLs and direct off-origin links, including
popups, produce no requests to the foreign target. The cross-origin redirect
is classified as a target mismatch before a subsequent test action can post
to either origin. A connection error on the selected origin remains a
Playwright failure.

```bash
PETF_PLAYWRIGHT_BROWSER_CHANNEL=chromium PETF_EXECUTION_TARGET_CHROMIUM_TEST=1 \
  .venv/bin/python -m unittest discover -s backend/tests -p test_execution_target_runtime.py -v
```

### Isolated live browser workbench

Use the official Firestore emulator JAR, Java 21 or later, and the existing
Python, frontend, and execution-runtime dependencies. Install Chromium for
both Playwright packages as needed. Supply the Gemini API key through the
environment or the local repository `.env`.

Verify the emulator download against the
[Firebase CLI emulator manifest](https://github.com/firebase/firebase-tools/blob/main/src/emulator/downloadableEmulatorInfo.json).
For example, the manifest records this artifact and SHA-256 for version
`1.22.0`.

```bash
mkdir -p /tmp/tcg-emulator
jar=/tmp/tcg-emulator/cloud-firestore-emulator-v1.22.0.jar
curl --fail --location \
  https://storage.googleapis.com/firebase-preview-drop/emulator/cloud-firestore-emulator-v1.22.0.jar \
  --output "$jar"
printf '%s  %s\n' 9b6498b7f62714d67f48f59b3818883cd682dbcd46b9f59511de81c97bb5166c "$jar" | shasum -a 256 -c -
.venv/bin/python scripts/e2e_workbench_local.py \
  --emulator-jar "$jar" --output /tmp/tcg-live-browser-workbench
```

The runner starts the actual API and frontend against the loopback emulator
with a demo project ID and a disposable local JWT identity. The test uses live
Gemini for synthetic requirements, Use Cases and Test Cases. It records the
explicit synthetic Use Cases review, verifies execution admission for the
saved suite, runs healthy and faulty Directory responses, and inspects saved
report and export evidence. The fixture uses supplied context notes to ground
the loopback target. The export binds the approved Use Cases snapshot as
source evidence and reports the separate Test Cases machine review.
From the Runs index the smoke opens each saved run, reloads its exact Reports
link, and checks the case identities and recorded assertion for the seeded
failure.
For the healthy execution, the browser harness sends the actual request,
discards the committed response, reloads Automation, and checks its receipt.
It verifies the exact saved report link and rejects a replay of the original
request identity without adding another run.

To measure fresh generated cases on the stateful Room Booking fixture, use the
same runner with a new output directory.

```bash
.venv/bin/python scripts/e2e_workbench_local.py --fixture room-booking \
  --emulator-jar "$jar" --output /tmp/tcg-live-room-booking
```

The test imports the existing Room Booking requirements, supplies observed
same-origin page and dropdown declarations in Context, and reviews the exact
saved suite. The matrix records design and setup status, admission, and browser
eligibility separately. Each seeded fault is tried only with healthy-passing
cases linked to its source requirement. Detection requires a matching failed
browser assertion. The authored booking matrix separately exercises native
double-click and response-loss controls.

`test_workflow_project_service.py` covers multi-part execution snapshots,
ordered readback, atomic rollback, retry, incomplete and corrupted parts, and
bounded Unicode diagnostics. `test_quality_feedback.py` verifies that a passing
run can close a finding only when its saved case evidence is complete.

Choose a new output directory for each run. The runner keeps service logs,
browser evidence and execution artifacts there, archives the exact test-owned
project, and stops its local services when the run ends. Model quality and
admission gates stay active throughout the test.

### Generation source snapshot regression

`test_partial_shard_recovery.py` and the parallel-generation delivery regression
cover incomplete model output. A partial model shard retains its concrete,
contract-valid cases with exact scenario references and gets one additional call
for only its missing scenarios. Recovery is capped at 120 seconds (or the shorter
configured workflow timeout), runs within the existing worker limit, and never
recurses. Complete shards and deterministic fallbacks do not trigger recovery.
Malformed, placeholder, duplicate or out-of-scope recovery rows cannot claim
coverage. Exhaustion retains usable cases and leaves missing-scenario tasks;
independent assessment and the existing saved-suite rejection gate still apply.
Diagnostics record two attempts, recovered/remaining counts and scenario IDs;
raw model responses are not persisted. These synthetic tests prove recovery and
delivery behavior, not live model quality.

`test_generation_source_snapshot.py` checks that Test Case generation/refinement
retains the exact current Use Cases snapshot when its nonempty coverage plan is
unchanged and not stale. Downstream analysis and review must not replace the
source semantic assessment or human decisions. Project refinement loads the
current plan server-side, seeds the agent session with that plan and analysis,
and skips upstream planning. It rejects missing/stale sources and refuses to
replace a changed source during persistence. Standalone refinement retains its
planning path. Generation with a changed plan still creates an unapproved source
snapshot. Reusing a source keeps the client revision check on the Test Cases
write. These checks do not establish generated case quality or live execution.

## 6) Additional affected-test targets

The selection matrix in section 1 is authoritative for local gate size. These
are useful targets for particular behavior changes, not additional mandatory
full-suite gates:

| Affected behavior | Relevant targets |
| --- | --- |
| Orchestrator decisions, impact routing, or governance | Affected orchestrator service tests and the strict orchestrator benchmark; `frontend/e2e/orchestrator-lifecycle.spec.js` when a stitched browser flow changes. |
| Project execution history | Workflow-project, automation-endpoint, and orchestrator-service tests; `frontend/e2e/multi-environment-execution.spec.js`. |
| Automation preview, selection, or Run availability | Execution-service/automation-endpoint tests; `frontend/e2e/automation-preview-consistency.spec.js`; multi-environment specs when that behavior is affected. |
| Home, navigation, reviews, responsive layout, or accessibility | The affected specs from `test:e2e:home-first`; use the full script for shared cross-flow changes. Consult [manual accessibility smoke](../home-first-accessibility-smoke.md) when semantics or focus behavior changes. |
| Firestore indexes or production rollout | Relevant manifest tests, JSON/config validation, and deployment-script checks. Follow [production rollout](../home-workspace-production-rollout.md) for actual live operations; local validation does not authorize applying them. |
| Report/export evidence or stale-report decisions | Affected export-endpoint/orchestrator tests and report evidence E2E specs. |

When a check cannot run, record the command, blocker, and remaining risk in the
PR. Add an issue comment only when it communicates a new blocker or decision.

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
- `frontend/e2e/support/accessibility.js`
- `docs/home-first-accessibility-smoke.md`
- `backend/execution_runtime/package.json`
- `backend/execution_runtime/playwright.config.ts`
- `scripts/evaluate_requirements.py`
- `scripts/evaluate_generation.py`
- `scripts/evaluate_orchestrator.py`
- `scripts/export_openapi.py`
- `scripts/deploy_firestore_indexes.py`
- `docs/home-workspace-production-rollout.md`
- `scripts/e2e_playwright_workflow.py`
- `docs/requirements_traceability.md`
- `schemas/spec.schema.json`
- `schemas/ir.schema.json`
