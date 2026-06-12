# GitHub Issue Backlog for Test Case Engine Improvements

This file contains issue-ready drafts for the implementation plan in `docs/implementation-plan.md`.

## Created GitHub issues

The original Phase 0 through Phase 2 issues in this section are historical
planning records for implemented coverage-intelligence and grounded-context
work. Active roadmap work is tracked in the Phase 3 and Phase 4 sections below.

- Tracker: [#12](https://github.com/lumensparkxy/agentic_test_case_generator/issues/12) — Improve the test case engine with coverage intelligence and grounded context
- [#3](https://github.com/lumensparkxy/agentic_test_case_generator/issues/3) — Phase 0: add generation evaluation harness and benchmark fixtures
- [#4](https://github.com/lumensparkxy/agentic_test_case_generator/issues/4) — Phase 1: add requirement analysis models and JSON parsers
- [#5](https://github.com/lumensparkxy/agentic_test_case_generator/issues/5) — Phase 1: implement requirement analysis agent and pipeline integration
- [#6](https://github.com/lumensparkxy/agentic_test_case_generator/issues/6) — Phase 1: expand validation metrics for rules, constraints, and transitions
- [#2](https://github.com/lumensparkxy/agentic_test_case_generator/issues/2) — Phase 1: expose requirement analysis and coverage gaps in the UI
- [#7](https://github.com/lumensparkxy/agentic_test_case_generator/issues/7) — Phase 2: define grounded context models and enrich API contract
- [#8](https://github.com/lumensparkxy/agentic_test_case_generator/issues/8) — Phase 2: implement safe artifact fetching and grounding services
- [#9](https://github.com/lumensparkxy/agentic_test_case_generator/issues/9) — Phase 2: use grounded context during generation and validation
- [#10](https://github.com/lumensparkxy/agentic_test_case_generator/issues/10) — Phase 2: add context analysis preview and review controls to the UI
- [#11](https://github.com/lumensparkxy/agentic_test_case_generator/issues/11) — Phase 2: expand backend and E2E regression coverage
- [#17](https://github.com/lumensparkxy/agentic_test_case_generator/issues/17) — Update project dependencies to latest compatible versions

## Created concern hardening issues

These issues were created from `docs/codebase/CONCERNS.md` after the first
next-version end-to-end Playwright documentation workflow succeeded.

### Phase 3 - Architecture Hardening

Status markers below reflect the GitHub issue state as of 2026-06-12.

- Epic [#35](https://github.com/lumensparkxy/agentic_test_case_generator/issues/35) - Reduce high-churn architecture hotspots
  - Done: [#39](https://github.com/lumensparkxy/agentic_test_case_generator/issues/39) - Extract frontend workflow state from App.jsx into hooks
  - Done: [#40](https://github.com/lumensparkxy/agentic_test_case_generator/issues/40) - Modularize frontend CSS by feature ownership
  - Done: [#41](https://github.com/lumensparkxy/agentic_test_case_generator/issues/41) - Split test_case_agent.py into focused backend modules
  - Blocked: [#42](https://github.com/lumensparkxy/agentic_test_case_generator/issues/42) - Split backend Pydantic contracts by domain
- Epic [#36](https://github.com/lumensparkxy/agentic_test_case_generator/issues/36) - Strengthen contract safety and engineering hygiene
  - Done: [#43](https://github.com/lumensparkxy/agentic_test_case_generator/issues/43) - Generate frontend API types from FastAPI OpenAPI
  - Done: [#44](https://github.com/lumensparkxy/agentic_test_case_generator/issues/44) - Add formatter and linter baseline for backend and frontend
  - Done: [#65](https://github.com/lumensparkxy/agentic_test_case_generator/issues/65) - Apply formatter baseline cleanup and enable format checks in CI
  - Done: [#45](https://github.com/lumensparkxy/agentic_test_case_generator/issues/45) - Deduplicate execution settings in .env.example and docs
  - Done: [#46](https://github.com/lumensparkxy/agentic_test_case_generator/issues/46) - Refresh planning docs to distinguish history from roadmap
  - Done: [#47](https://github.com/lumensparkxy/agentic_test_case_generator/issues/47) - Exclude ignored generated artifacts from codebase scans

### Phase 4 - Operational Readiness

- Epic [#37](https://github.com/lumensparkxy/agentic_test_case_generator/issues/37) - Settle production persistence and auth architecture
  - Done: [#48](https://github.com/lumensparkxy/agentic_test_case_generator/issues/48) - Spike: decide Firestore versus Postgres persistence target
  - Done: [#49](https://github.com/lumensparkxy/agentic_test_case_generator/issues/49) - Introduce durable persistence boundary for audit and billing
  - Done: [#50](https://github.com/lumensparkxy/agentic_test_case_generator/issues/50) - Spike: define production auth policy for Firebase and JWT
  - Done: [#51](https://github.com/lumensparkxy/agentic_test_case_generator/issues/51) - Enforce accepted production auth mode explicitly
- Epic [#38](https://github.com/lumensparkxy/agentic_test_case_generator/issues/38) - Harden operational security and production readiness
  - Done: [#52](https://github.com/lumensparkxy/agentic_test_case_generator/issues/52) - Protect or scope the metrics endpoint for production
  - Done: [#53](https://github.com/lumensparkxy/agentic_test_case_generator/issues/53) - Define artifact retention policy and cleanup command
  - Done: [#54](https://github.com/lumensparkxy/agentic_test_case_generator/issues/54) - Document credential rotation for integrations and secrets
  - Done: [#55](https://github.com/lumensparkxy/agentic_test_case_generator/issues/55) - Add integration latency and error metrics for JIRA and Azure DevOps
  - Done: [#56](https://github.com/lumensparkxy/agentic_test_case_generator/issues/56) - Add durable audit dead-letter sink for compliance deployments
  - Done: [#57](https://github.com/lumensparkxy/agentic_test_case_generator/issues/57) - Harden artifact fetching threat model and validation
  - Ready: [#77](https://github.com/lumensparkxy/agentic_test_case_generator/issues/77) - Add seamless integration credential key rotation and re-encryption support

> Note: issue numbers do not strictly follow the planned sequence because several issues were created in parallel.

## Pending issue-ready drafts

### Draft - Document current codebase after first next-version E2E success

**Suggested labels:** `type:task`, `area:docs`, `priority:p2`, `status:ready`

**Suggested milestone:** `Phase 2 - Grounded Context`

#### Summary

Add repository-level codebase documentation after the first successful
next-version end-to-end Playwright documentation workflow so maintainers and
automation agents can understand the current architecture, validation gates,
integration boundaries, and remaining risks.

#### Acceptance criteria

- [ ] Scan the tracked codebase and existing planning docs.
- [ ] Add `docs/codebase/STACK.md`, `STRUCTURE.md`, `ARCHITECTURE.md`,
      `CONVENTIONS.md`, `INTEGRATIONS.md`, `TESTING.md`, and `CONCERNS.md`.
- [ ] Each codebase document includes evidence paths for non-trivial claims.
- [ ] Fold the first next-version E2E workflow into `docs/codebase/TESTING.md`.
- [ ] Update `README.md` with links to the new codebase documentation.
- [ ] Capture unresolved intent-dependent questions as `[ASK USER]` items.

#### Test plan

- [ ] Review the Markdown files for internal links, evidence paths, and
      unsupported claims.
- [ ] Run a lightweight file-presence check for the expected docs.
- [ ] Run `git diff --check`.

## Suggested milestones

- `Phase 0 - Evaluation`
- `Phase 1 - Coverage Intelligence`
- `Phase 2 - Grounded Context`
- `Phase 3 - Architecture Hardening`
- `Phase 4 - Operational Readiness`

## Suggested labels

- `type:enhancement`
- `type:epic`
- `type:story`
- `type:task`
- `type:spike`
- `area:backend`
- `area:frontend`
- `area:qa`
- `area:docs`
- `area:devops`
- `priority:p1`
- `priority:p2`
- `priority:p3`
- `status:ready`
- `status:blocked`
- `phase:0`
- `phase:1`
- `phase:2`

## Recommended issue order

| Order | Title | Milestone |
| --- | --- | --- |
| 1 | Phase 0: add generation evaluation harness and benchmark fixtures | Phase 0 - Evaluation |
| 2 | Phase 1: add requirement analysis models and JSON parsers | Phase 1 - Coverage Intelligence |
| 3 | Phase 1: implement requirement analysis agent and pipeline integration | Phase 1 - Coverage Intelligence |
| 4 | Phase 1: expand validation metrics for rules, constraints, and transitions | Phase 1 - Coverage Intelligence |
| 5 | Phase 1: expose requirement analysis and coverage gaps in the UI | Phase 1 - Coverage Intelligence |
| 6 | Phase 2: define grounded context models and enrich API contract | Phase 2 - Grounded Context |
| 7 | Phase 2: implement safe artifact fetching and grounding services | Phase 2 - Grounded Context |
| 8 | Phase 2: use grounded context during generation and validation | Phase 2 - Grounded Context |
| 9 | Phase 2: add context analysis preview and review controls to the UI | Phase 2 - Grounded Context |
| 10 | Phase 2: expand backend and E2E regression coverage | Phase 2 - Grounded Context |

---

## Issue 1 — Phase 0: add generation evaluation harness and benchmark fixtures

**Suggested labels:** `type:enhancement`, `area:backend`, `area:qa`, `phase:0`

**Dependencies:** none

### Summary

Add a repeatable benchmark harness so the current test-case engine can be measured before and after the planned quality improvements.

### Scope

- Add `scripts/evaluate_generation.py`
- Add benchmark inputs and expected trait summaries under `scripts/`
- Record current quality metrics from generation results
- Report baseline values for scenario coverage, approval score, and structural quality

### Acceptance criteria

- [ ] Benchmark script runs locally with documented inputs
- [ ] The script reports baseline quality metrics in a consistent format
- [ ] Existing parse → generate → export flow continues to work
- [ ] Results can be used to compare Phase 1 and Phase 2 improvements

---

## Issue 2 — Phase 1: add requirement analysis models and JSON parsers

**Suggested labels:** `type:enhancement`, `area:backend`, `phase:1`

**Dependencies:** Issue 1

### Summary

Define the data contract for structured requirement analysis and add parsing helpers for model output.

### Scope

- Update `backend/app/models.py` with requirement-analysis models
- Add parsing helpers in `backend/app/utils/llm_json.py`
- Keep response shapes backward compatible by making new fields optional

### Acceptance criteria

- [ ] Models exist for rules, constraints, roles, transitions, and risk signals
- [ ] Parsers safely handle malformed or partial JSON
- [ ] Existing payloads remain valid when analysis data is absent
- [ ] Model and parser tests cover fallback behavior

---

## Issue 3 — Phase 1: implement requirement analysis agent and pipeline integration

**Suggested labels:** `type:enhancement`, `area:backend`, `phase:1`

**Dependencies:** Issue 2

### Summary

Introduce a dedicated agent that extracts structured requirement intelligence and insert it into the test-case generation pipeline before scenario planning.

### Scope

- Create `backend/app/agents/analysis_agent.py`
- Extract business rules, constraints, permissions, transitions, dependencies, and risks
- Integrate analysis output into `backend/app/agents/test_case_agent.py`
- Add deterministic fallback analysis for empty model output

### Acceptance criteria

- [ ] Analysis is generated from approved requirements before coverage planning
- [ ] Fallback analysis is returned when model output is incomplete
- [ ] Existing generation still works when analysis is missing
- [ ] Benchmark inputs show improved coverage planning quality

---

## Issue 4 — Phase 1: expand validation metrics for rules, constraints, and transitions

**Suggested labels:** `type:enhancement`, `area:backend`, `area:qa`, `phase:1`

**Dependencies:** Issue 3

### Summary

Upgrade heuristic validation so approval is informed by coverage of extracted rules, constraints, role permissions, and state transitions.

### Scope

- Extend `coverage_metrics` generation in `backend/app/agents/test_case_agent.py`
- Add coverage ratios for business rules, constraints, and transitions
- Surface missing high-risk items as blocking issues or strong warnings

### Acceptance criteria

- [ ] Review payload includes new coverage metrics
- [ ] Missing critical rules can block approval
- [ ] Non-critical gaps are surfaced as suggestions or warnings
- [ ] Existing review structure remains backward compatible

---

## Issue 5 — Phase 1: expose requirement analysis and coverage gaps in the UI

**Suggested labels:** `type:enhancement`, `area:frontend`, `phase:1`

**Dependencies:** Issue 4

### Summary

Show users why the engine generated certain cases and where rule/constraint gaps still remain.

### Scope

- Update `frontend/src/App.jsx`
- Add analysis summary cards or panels in the Generate tab
- Display missing coverage signals separately from scenario coverage
- Keep the current flow usable when analysis data is absent

### Acceptance criteria

- [ ] Analysis summaries render when returned by the backend
- [ ] Missing coverage is visually distinguishable from covered items
- [ ] UI does not break when old payloads omit analysis fields
- [ ] E2E tests cover the new rendering path

---

## Issue 6 — Phase 2: define grounded context models and enrich API contract

**Suggested labels:** `type:enhancement`, `area:backend`, `phase:2`

**Dependencies:** Issue 1

### Summary

Define the data contract for grounded context and upgrade `/requirements/enrich` to return structured artifact analysis instead of a pass-through payload.

### Scope

- Add grounded-context models to `backend/app/models.py`
- Define response shape for artifact analysis results
- Update `backend/app/main.py` endpoint contract while preserving current inputs

### Acceptance criteria

- [ ] New grounded-context models exist and are optional
- [ ] `/requirements/enrich` can return structured artifact results
- [ ] Existing clients can still submit current `EnrichInput` payloads
- [ ] Response shape is documented in code and tests

---

## Issue 7 — Phase 2: implement safe artifact fetching and grounding services

**Suggested labels:** `type:enhancement`, `area:backend`, `phase:2`

**Dependencies:** Issue 6

### Summary

Add services that safely retrieve remote artifacts and normalize them into structured UI and workflow facts.

### Scope

- Add `backend/app/services/artifact_fetcher.py`
- Add `backend/app/services/context_grounding.py`
- Introduce timeouts, size caps, content-type checks, and graceful partial failure behavior
- Parse HTML and summarize diagram/image inputs into normalized structures

### Acceptance criteria

- [ ] Supported artifacts can be fetched and normalized
- [ ] Unsafe or unsupported requests are blocked cleanly
- [ ] Partial failures do not crash the enrichment flow
- [ ] Service tests cover timeout and validation behavior

---

## Issue 8 — Phase 2: use grounded context during generation and validation

**Suggested labels:** `type:enhancement`, `area:backend`, `phase:2`

**Dependencies:** Issue 7

### Summary

Feed grounded context into the coverage planner, generator, and validator so generated cases become more specific to the product artifacts supplied by the user.

### Scope

- Integrate grounded context into `backend/app/agents/test_case_agent.py`
- Optionally add `source_refs` or equivalent metadata to generated test cases
- Ensure validators can score whether grounded artifacts influenced the result

### Acceptance criteria

- [ ] Generation uses grounded context when it is available
- [ ] Generation still succeeds without grounded context
- [ ] Review output can highlight missing source-backed coverage
- [ ] Benchmark runs show more specific, artifact-backed cases

---

## Issue 9 — Phase 2: add context analysis preview and review controls to the UI

**Suggested labels:** `type:enhancement`, `area:frontend`, `phase:2`

**Dependencies:** Issue 8

### Summary

Add a human-in-the-loop review step for grounded context so users can inspect and curate artifact analysis before generating test cases.

### Scope

- Update `frontend/src/App.jsx`
- Add an “Analyze context” action in the Context tab
- Render extracted screens, fields, workflows, and source summaries
- Allow users to exclude low-value grounded items before generation

### Acceptance criteria

- [ ] Users can trigger context analysis from the UI
- [ ] Users can review analyzed artifacts before generation
- [ ] The UI remains usable when context analysis is skipped or partially fails
- [ ] E2E coverage includes the context preview path

---

## Issue 10 — Phase 2: expand backend and E2E regression coverage

**Suggested labels:** `type:enhancement`, `area:backend`, `area:frontend`, `area:qa`, `phase:2`

**Dependencies:** Issues 5, 8, and 9

### Summary

Extend automated coverage so analysis and grounding behavior stay stable while the engine evolves.

### Scope

- Add backend tests for models, parsing, validation, fetching, and grounding
- Expand `frontend/e2e/workflow.spec.js` to verify analysis and grounding UX
- Wire the benchmark harness into regular validation runs where practical

### Acceptance criteria

- [ ] New backend logic is covered by targeted tests
- [ ] E2E tests cover analysis visibility and grounded-context preview
- [ ] Existing export flow remains green
- [ ] Benchmark regression output is easy to compare across changes
