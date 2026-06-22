# Architecture

## 1) Architectural Style

Primary style: modular monolith with a layered backend, a React single-page
frontend, and an isolated Playwright execution runtime.

Why this classification:

- `backend/app/main.py` creates one FastAPI application and registers feature
  routers from `backend/app/routers/`.
- Routers delegate domain behavior to service modules, agent modules, adapters,
  and domain-owned Pydantic contracts in `backend/app/contracts/`.
  `backend/app/models.py` remains a compatibility facade for existing imports.
- The frontend is one Vite React app with `App.jsx` as top-level workflow
  orchestration and focused components under `frontend/src/components/`.
- Browser execution is split into a backend Python conversion/service path and
  a Node Playwright runtime under `backend/execution_runtime/`.

Primary constraints:

- User-facing workflow is human-in-the-loop: requirements are parsed/reviewed,
  context can be enriched, test cases are generated/reviewed, exports are gated,
  and execution candidates are previewed before running.
- Durable QA projects can now wrap the staged workflow so requirement, context,
  use-case, impact-analysis, test-case, execution, and report snapshots survive
  browser reloads.
- Orchestrator next actions are backed by stable specialist task contracts for
  requirements, use cases, impact, test cases, automation, execution, review,
  and reporting, with local adapters registered behind an ADK-compatible
  dispatcher boundary.
- Use-case planning is split from full test-case generation. The orchestrator
  `use_cases` specialist uses a bounded backend coordinator that shards
  requirements, merges requirement analysis and coverage plans centrally, and
  leaves project snapshot writes to the existing router/service flow.
- Large test-case generation can reuse approved requirement-analysis and
  coverage-plan artifacts. A bounded backend coordinator shards planned
  scenarios by requirement group, runs draft-only workers, remaps duplicate
  `TC-*` IDs, restores coverage-plan order, and applies one suite-level
  heuristic review before the router records billing, usage, versions, or
  project snapshots.
- Planned scenario coverage is exact-ID first. Generated cases should preserve
  `scenario_refs` for every covered coverage-plan scenario; scenario-type
  inference remains a degraded compatibility fallback for legacy or malformed
  cases and is reported in workflow diagnostics.
- Automation generation is assembled centrally from bounded component/page
  fragments. Workers may draft generated test modules only; shared project
  files, final paths, duplicate symbol handling, and case-level manual or
  unsupported diagnostics are owned by `backend/app/agents/automation_agent.py`,
  while `/automation/playwright` remains the audit and billing boundary.
- Orchestrator run records, timeline events, and checkpoints are persisted
  under QA projects so action progress, blockers, retries, produced snapshots,
  and execution links can resume after reloads or backend restarts.
- Agent output must be resilient: parsers, deterministic fallbacks, retry
  diagnostics, and heuristic quality gates protect the workflow from malformed
  model output.
- Source artifacts and credentials must not be retained in git. Generated
  execution artifacts, screenshots, exports, and client briefs are ignored.

## 2) Visual Architecture

```mermaid
flowchart LR
    user["Signed-in user"]
    browser["React/Vite frontend\nfrontend/src/App.jsx\nfrontend/src/components"]
    apiClient["API helper\nfrontend/src/services/apiClient.js"]

    app["FastAPI app\nbackend/app/main.py"]
    middleware["Request middleware\nrequest_id, trace_id,\nlogging, metrics"]
    routers["Feature routers\nprojects, requirements,\ntestcases, automation, export,\nintegrations, billing, reports"]
    auth["Auth layer\nFirebase ID token\nlegacy JWT\nGoogle credential login"]
    models["Pydantic contracts\nbackend/app/contracts\nbackend/app/models.py facade"]

    agents["Agent workflows\nADK/Gemini\nrequirements, use-case planning,\ntest generation, impact update,\nautomation"]
    services["Domain services\naudit, billing, versioning,\nproject lifecycle, orchestrator,\ngrounding, execution, reporting"]
    adapters["Provider adapters\nJIRA Cloud\nAzure DevOps"]
    petf["Plain-English test framework\nspec parser -> IR ->\nPlaywright generator"]
    runtime["Execution runtime\nbackend/execution_runtime\nnpx playwright test"]

    gemini["Gemini API"]
    firebase["Firebase Auth\nFirestore"]
    jira["JIRA Cloud API"]
    ado["Azure DevOps API"]
    artifacts["Local generated artifacts\n.execution_artifacts\n/tmp/pw_workflow_out"]
    metrics["/metrics\nPrometheus format\nconfig-gated"]

    user --> browser
    browser --> apiClient
    apiClient --> app
    app --> middleware
    middleware --> routers
    routers --> auth
    routers --> models
    routers --> agents
    routers --> services
    services --> adapters
    services --> petf
    petf --> runtime

    agents --> gemini
    auth --> firebase
    services --> firebase
    adapters --> jira
    adapters --> ado
    runtime --> artifacts
    services --> artifacts
    middleware --> metrics

    routers --> browser
```

The diagram shows the main request path and the two important side paths:
agent-backed generation through Gemini, and executable browser automation
through the plain-English framework plus the isolated Playwright runtime.

## 3) System Flow

```text
React UI -> FastAPI router -> billing/audit guard -> agent/service/adapters -> persistence repositories/Firestore/external API/local artifacts -> Pydantic response -> React UI
```

Typical generate flow:

1. The frontend sends authenticated API requests through helpers in
   `frontend/src/services/apiClient.js` and auth/session orchestration in
   `frontend/src/App.jsx`.
2. FastAPI middleware in `backend/app/main.py` attaches `X-Request-ID`, optional
   trace context, structured request logging, and HTTP metrics.
3. A router in `backend/app/routers/` resolves `AuthUser`, starts an audit
   workflow, checks billing where relevant, validates Pydantic request models,
   and delegates work.
4. Agent modules such as `backend/app/adk_client.py`,
   `backend/app/agents/analysis_agent.py`,
   `backend/app/agents/use_case_agent.py`, and
   `backend/app/agents/test_case_agent.py` orchestrate model calls, shard
   coordination, parsing, and workflow loops. Focused helper modules own
   coverage metrics, review scoring, deterministic fallback output, and
   response hydration.
5. Service modules persist audit/version/billing/integration metadata through
   repository boundaries and the shared Firestore adapter where configured, or
   return warnings/fallback behavior where the code explicitly supports missing
   Firestore.
6. The router completes audit and billing records, then returns Pydantic
   response models for the frontend to render diagnostics, coverage,
   traceability, exports, or execution results.

Project-scoped workflow flow:

```text
QA Project -> requirements snapshot -> context snapshot -> use-case snapshot -> impact-analysis snapshot -> test-case snapshot -> execution runs by environment -> report/export snapshots
```

When a request includes `project_id`, the relevant router appends an immutable
snapshot through `backend/app/services/workflow_project_service.py`. Upstream
changes mark downstream stages stale without deleting old versions, execution
runs, or report/export records. Calls without `project_id` keep the earlier
one-shot behavior.

Impact update flow:

```text
Stale upstream requirements/use cases -> /projects/{id}/impact-analysis -> changed-item and recommendation snapshot -> /projects/{id}/impact-update/apply -> versioned test-case snapshot
```

`backend/app/agents/impact_update_agent.py` compares the current requirement
and use-case snapshots with the source snapshots that produced the existing
test-case suite. Direct traceability uses `linked_requirement_ids` and
`scenario_refs`; semantic-neighbor candidates are suggested without being
accepted by default. `backend/app/services/impact_update_service.py` applies
only accepted recommendations, preserves unchanged test-case artifact versions,
and deprecates obsolete cases instead of hard deleting them.

Orchestrator status flow:

```text
QA Project snapshots -> /projects/{id}/orchestrator/status -> deterministic stage state, blockers, and next actions
```

`backend/app/services/orchestrator_service.py` derives workflow stage health and
recommended actions from persisted project snapshots, stage staleness, approval
flags, impact-analysis payloads, and execution history. Each recommended action
includes the registered specialist contract kind, version, and implementation
metadata from `backend/app/agents/specialist_registry.py`. The status service
does not call agents or decide human approvals; those remain explicit gates
represented as blockers.

Incremental impact updates are first-class orchestrator decisions. When a
baseline suite exists and downstream test cases are stale because requirements
or use cases changed, the orchestrator recommends impact analysis as the primary
path and keeps full regeneration secondary. Impact analysis can run before the
changed upstream artifact is approved, but apply is blocked until the changed
requirement/use-case stage and changed items are approved. Once
`impact.update.apply` writes the new test-case snapshot sourced from the current
impact snapshot, the suite is no longer treated as needing another incremental
update.

Orchestrator run persistence flow:

```text
Action request + request ID -> orchestrator run record -> events + checkpoints -> /projects/{id}/orchestrator/runs
```

`backend/app/services/orchestrator_run_service.py` stores run records,
idempotent event records, and resumable checkpoints in project subcollections.
Run records track current action/stage, status, actor, request ID, project
revision, blockers, produced snapshot IDs, and execution run IDs. Checkpoints
link source/output snapshots, agent output references, and execution records.
The `GET /projects/{id}/orchestrator/runs` endpoint returns timeline-friendly
runs, events, and checkpoints for the frontend cockpit tracked by issue #90.

Frontend orchestrator cockpit flow:

```text
Stored current project ID -> project reopen -> status/runs fetch -> cockpit recommended action, blockers, and timeline
```

Frontend workflow shell flow:

```text
Top command bar with Projects menu -> left workflow navigation -> active tab workspace -> right project information rail
```

`frontend/src/components/layout/WorkflowNavigationDrawer.jsx` renders the
Material 3-inspired workflow navigation introduced for issue #120. It consumes
the same tab IDs owned by `frontend/src/App.jsx` (`Upload`, `Context`,
`Template`, `Generate`, `Automation`, and `Export`) so existing tab state,
workflow actions, and `Next`/`Back` behavior remain unchanged while the
horizontal stepper is no longer the primary navigation surface. The rail uses
`lucide-react` line icons for the six workflow destinations and keeps the
button `aria-label` values as the stable navigation contract.

`frontend/src/components/projects/OrchestratorCockpitPanel.jsx` is embedded in
the center workspace as the action surface. It preserves
`aria-label="Orchestrator Cockpit"` and existing action handlers while removing
the repeated visible cockpit/run-details headers from the center workspace.
Project selection, refresh, and creation live in the top command bar Projects
menu so the center workspace stays focused on workflow actions.
`frontend/src/components/projects/ProjectInformationRail.jsx` owns the durable
status presentation in the right rail: status overview, stage progress,
blockers, agent timeline, project evidence, latest report evidence, and last
run details. Both components consume the same deterministic status and run
payloads, so backend status/runs contracts remain unchanged.
The selected Brighter Executive Cockpit visual target and QA mapping are
recorded in `docs/brighter-executive-cockpit-design-qa.md`.
The shell also supports independent local-only collapse preferences for the
left workflow navigation and right information rail. Directional icon controls
collapse the left navigation toward the left edge and the right rail toward the
right edge, preserving the same tab and orchestrator data contracts while
changing only presentation density.

Review and evidence-backed reporting flow:

```text
Approved test-case or execution evidence -> review action -> export/report snapshot with source snapshot IDs and execution run IDs -> stale report regeneration when upstream evidence changes
```

Report generation reuses the export endpoints in `backend/app/routers/export.py`
for CSV, Excel, and JSON outputs. When export requests include `project_id`, the
router loads the current QA project and writes a `reports` snapshot that records
`evidence.source_snapshot_ids`, `evidence.execution_run_ids`, and evidence refs
for each source project snapshot or execution run. The orchestrator exposes
review/report actions after approved test-case evidence and after execution; if
a downstream project change marks the `reports` stage stale, report regeneration
becomes the primary recommended action. The project workspace renders the latest
report status and evidence IDs so the audit trail is visible after reload.

Next-version execution flow:

```text
Approved test-case snapshot -> /automation/execution/preview for target environment -> executable candidates
selected candidates -> /automation/execution/run -> YAML spec -> IR JSON -> Playwright TS spec -> npx playwright test -> environment run history and report artifacts
```

The conversion and run path is implemented by
`backend/app/services/execution_service.py` and
`backend/plain_english_test_framework/`, with Node runtime configuration in
`backend/execution_runtime/playwright.config.ts`.
When execution requests include `project_id`, `backend/app/routers/automation.py`
records the target environment, optional target base URL, selected test-case
IDs, and source test-case snapshot ID. `workflow_project_service.py` stores
environment-specific execution records in the project `execution_runs`
subcollection, so staging, production-like, and other named runs remain visible
without overwriting each other. Failed execution records update execution/review
signals through orchestrator status but do not mutate requirement, use-case, or
test-case snapshots automatically.

## 4) Layer/Module Responsibilities

| Layer or module | Owns | Must not own | Evidence |
|-----------------|------|--------------|----------|
| FastAPI app | App construction, middleware, CORS, router registration, health, metrics | Feature endpoint logic beyond global middleware | `backend/app/main.py` |
| Routers | HTTP contracts, auth dependencies, audit lifecycle calls, billing access calls, endpoint-level errors | Provider HTTP implementation or model prompt design | `backend/app/routers/*.py` |
| Models | Pydantic request/response/data contracts grouped by domain with a compatibility facade | Runtime business behavior | `backend/app/contracts/*.py`, `backend/app/models.py` |
| Agents | Requirement extraction, analysis, bounded use-case planning, bounded test-case shard coordination, bounded automation fragment assembly, specialist task contracts/registry, impact recommendation logic, review/refinement loops, deterministic fallback generation, coverage metrics, response hydration, automation POM generation | HTTP transport, UI rendering, billing, persistence, or project snapshot writes | `backend/app/adk_client.py`, `backend/app/agents/*.py` |
| Services | Billing, audit, versioning, project lifecycle, orchestrator decisions, orchestrator run persistence, impact update apply, reporting, persistence repository boundaries, execution conversion/run, context grounding | Route decorators or React state | `backend/app/services/*.py` |
| Adapters | JIRA and Azure DevOps remote API calls and provider-specific normalization | Cross-provider workflow policy | `backend/app/adapters/*.py` |
| Auth | Firebase token verification, legacy JWT decoding, Google credential login, role/admin checks | Billing, generation, or integration sync logic | `backend/app/auth/*.py` |
| Observability | JSON logging, request context, metrics rendering, optional tracing | Business decisions | `backend/app/observability/*.py` |
| Plain-English framework | Spec parsing, secret detection, environment/data resolution, schema-valid IR generation, Playwright spec generation, local runner | User auth, billing, external integrations | `backend/plain_english_test_framework/*.py` |
| React app | Top-level workflow composition, auth session orchestration, domain workflow hooks, component props, API actions | Backend persistence or agent logic | `frontend/src/App.jsx`, `frontend/src/hooks/`, `frontend/src/components/` |
| Frontend styles | Shared design tokens, base rules, layout styles, and feature-owned selectors imported through one cascade entry point | React state, backend contracts, or visual redesign outside the owning feature | `frontend/src/styles/index.css`, `frontend/src/styles/*.css` |

## 5) Reused Patterns

| Pattern | Where found | Why it exists |
|---------|-------------|---------------|
| FastAPI dependency auth | `Depends(get_current_user)` in routers | Keeps protected endpoint identity resolution consistent |
| Pydantic boundary models | `backend/app/contracts/*.py`, `backend/app/models.py` facade | Keeps backend API, integration, billing, execution, and export payloads explicit while reducing cross-domain contract churn |
| Workflow audit lifecycle | `start_workflow_run()`, `complete_workflow_run()`, `record_usage_event()` | Links operations to request IDs, users, billing, reports, and trace metadata |
| Persistence repository boundary | `audit_repository.py`, `billing_repository.py`, `usage_event_repository.py`, `firestore_repository.py` | Keeps routers and agents insulated from Firestore-specific client setup and gives PostgreSQL adapters a defined insertion point |
| Durable project aggregate | `projects.py`, `workflow_project_service.py`, project contracts in `contracts/projects.py` | Gives users a resumable QA workspace while preserving legacy unscoped calls |
| Orchestrator decision model | `orchestrator_service.py`, orchestrator contracts in `contracts/orchestrator.py` | Derives deterministic next actions and blockers from durable project snapshots |
| Orchestrator run persistence | `orchestrator_run_service.py`, orchestrator run/checkpoint/event contracts in `contracts/orchestrator.py` | Keeps action progress, retries, blockers, produced snapshots, and execution links resumable across reloads and backend restarts |
| Specialist agent task registry | `specialist_contracts.py`, `specialist_registry.py` | Gives orchestrator actions stable typed task envelopes/results and lets local or future ADK adapters plug in behind the same contract |
| Use-case stage coordinator | `use_case_agent.py`, `test_case_coverage.py`, `analysis_agent.py` | Lets the orchestrator generate requirement analysis and scenario coverage plans without producing/discarding full test cases, while keeping merge validation deterministic |
| Test-case shard coordinator | `test_case_agent.py`, `test_case_coverage.py`, `test_case_fallback.py`, `test_case_review.py`, `test_case_hydration.py` | Reuses approved coverage plans for large suites, bounds parallel workers, merges draft cases centrally, enforces exact `scenario_refs` before heuristic coverage fallback, and keeps final approval at the whole-suite boundary |
| Automation fragment coordinator | `automation_agent.py` | Splits large approved suites by component/page group, assembles shared Playwright project files centrally, dedupes fragment names and symbols, and reports every case as generated, manual, unsupported, or fallback |
| Impact update snapshotting | `impact_update_agent.py`, `impact_update_service.py`, `versioning_service.py` | Lets changed requirement/use-case slices update the current suite without regenerating unchanged coverage |
| Deterministic fallback | Requirement/test-case agents and automation agent | Keeps workflow usable when model output is malformed, unavailable, or incomplete |
| Safe artifact fetch | `artifact_fetcher.py` plus `context_grounding.py` | Blocks unsafe or unsupported public URLs and returns partial enrichment warnings instead of crashing |
| Provider adapter plus service | JIRA and Azure DevOps adapter/service pairs plus shared credential crypto helper | Separates remote API mechanics, credential storage/rotation, and import/sync workflow policy |
| Local fake/patch tests | `backend/tests/test_*` | Keeps tests independent from real Firestore, JIRA, Azure DevOps, Firebase, and model calls |
| Generated artifact isolation | `.execution_artifacts/`, `client_submission/`, runtime artifact directories | Prevents local outputs, traces, screenshots, and exports from becoming source |

## 6) Known Architectural Risks

- Test-case generation is now split across focused backend helper modules:
  orchestration and prompt builders remain in
  `backend/app/agents/test_case_agent.py`, coverage helpers live in
  `backend/app/agents/test_case_coverage.py`, review helpers live in
  `backend/app/agents/test_case_review.py`, deterministic fallback helpers live
  in `backend/app/agents/test_case_fallback.py`, and response hydration helpers
  live in `backend/app/agents/test_case_hydration.py`. `frontend/src/App.jsx`
  is still the top-level workflow composer, but workflow state is now split into
  domain hooks under `frontend/src/hooks/`, and feature styles are split under
  `frontend/src/styles/` behind `frontend/src/styles/index.css`. Future changes
  should continue moving cohesive behavior and selectors behind those ownership
  boundaries.
- Pydantic contracts now live in domain modules under `backend/app/contracts/`,
  with `backend/app/models.py` kept as a compatibility facade. New contract
  changes should use the closest domain module and continue to run OpenAPI
  export plus generated frontend API contract checks.
- `docs/production-auth-policy-decision.md` accepts Firebase ID tokens as the
  production protected-endpoint token type. Backend-issued JWTs and
  `/auth/google/login` are isolated to local/test compatibility mode through
  `AUTH_TOKEN_MODE=firebase-or-backend-jwt`; production uses
  `AUTH_TOKEN_MODE=firebase-only`.
- Firestore is the current durable service path for audit, versioning, billing,
  integration mappings, QA project snapshots, orchestrator run/event/checkpoint
  records, and reports. `docs/persistence-target-decision.md`
  accepts a staged approach: keep Firestore as the transitional runtime store
  and target PostgreSQL for compliance-grade audit, billing, reporting, and
  versioned artifacts. Repository boundaries now isolate audit writes,
  reporting usage-event reads, billing repository access, and Firestore
  collection lookup. Audit dead letters can also be mirrored to an optional
  Firestore sink for compliance deployments. PostgreSQL schema, adapter, and
  migration work remain future implementation stories.
- The execution runtime shells out to `npx playwright test`. Each execution run
  compiles selected executable candidates into shared run-local specs, invokes
  Playwright once, and maps the consolidated JSON report back to per-case
  results through generated `caseId` annotations. The artifact root, runtime
  cwd, browser channel, and generated paths need careful configuration in every
  deployment environment.

## 7) Evidence

- `backend/app/main.py`
- `backend/app/contracts/`
- `backend/app/models.py`
- `backend/app/routers/requirements.py`
- `backend/app/routers/projects.py`
- `backend/app/routers/testcases.py`
- `backend/app/routers/automation.py`
- `backend/app/agents/impact_update_agent.py`
- `backend/app/agents/specialist_contracts.py`
- `backend/app/agents/specialist_registry.py`
- `backend/app/agents/test_case_agent.py`
- `backend/app/agents/test_case_coverage.py`
- `backend/app/agents/test_case_review.py`
- `backend/app/agents/test_case_fallback.py`
- `backend/app/agents/test_case_hydration.py`
- `backend/app/services/impact_update_service.py`
- `backend/app/services/orchestrator_service.py`
- `backend/app/services/execution_service.py`
- `backend/plain_english_test_framework/compiler.py`
- `backend/plain_english_test_framework/local_runner.py`
- `backend/app/services/artifact_fetcher.py`
- `backend/app/services/context_grounding.py`
- `docs/artifact-fetching-threat-model.md`
- `backend/app/services/audit_service.py`
- `backend/app/services/audit_repository.py`
- `backend/app/services/firestore_repository.py`
- `backend/app/services/usage_event_repository.py`
- `backend/app/services/workflow_project_service.py`
- `backend/app/services/billing_service.py`
- `frontend/src/App.jsx`
- `frontend/src/components/`
- `frontend/src/styles/`
- `frontend/src/services/apiClient.js`
- `backend/execution_runtime/playwright.config.ts`
