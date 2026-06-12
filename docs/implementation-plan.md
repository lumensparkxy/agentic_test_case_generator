# Test Case Engine Improvement Implementation Plan

## Summary

**Status as of 2026-05-08:** the original coverage-intelligence and grounded-context phases are largely implemented. Treat this document as both implementation history and a baseline for the next hardening phase: contract alignment, modularization, CI, external-sync maturity, and export/automation productization.

This plan delivers two high-impact upgrades to the test case generation engine:

1. **Coverage intelligence** between requirement extraction and test case generation.
2. **Grounded context enrichment** that turns links and artifacts into structured product knowledge.

The goal is to improve test-case specificity, edge-case depth, reviewer confidence, and future automation readiness without breaking the current human-in-the-loop workflow.

## Why this plan fits the current codebase

The existing architecture already provides strong insertion points:

- Requirement extraction and refinement are handled in `backend/app/adk_client.py` and `backend/app/agents/requirements_agent.py`.
- Test-case generation already has a staged pipeline in `backend/app/agents/test_case_agent.py`.
- Scenario planning already exists and is surfaced in `frontend/src/App.jsx`.
- `/requirements/enrich` now returns grounded context using `backend/app/services/artifact_fetcher.py` and `backend/app/services/context_grounding.py`.

That means the next step is not a rewrite. It is a targeted stabilization and hardening pass on the current pipeline.

## Goals

- Increase meaningful negative, boundary, authorization, and workflow coverage.
- Generate test cases from structured rules and constraints, not only requirement prose.
- Turn app/prototype/diagram/image links into reusable grounded context.
- Preserve backward compatibility for existing UI flow and exports.
- Keep human review and refinement central to the workflow.

## Non-goals

- Replacing the current authentication model.
- Fully implementing Playwright generation in this phase.
- Reworking the export formats beyond what is required to carry new optional metadata.
- Adding a database or persistence layer.

## Success metrics

Baseline values should be captured in Phase 0 before any scoring target is enforced. Initial target thresholds:

- `traceability_coverage_ratio = 1.0`
- `must_have_scenario_coverage_ratio = 1.0`
- `rule_coverage_ratio >= 0.90`
- `constraint_coverage_ratio >= 0.85`
- `grounded_source_backed_case_ratio >= 0.70` when context artifacts are provided
- No regression in existing parse → generate → export E2E flow
- No regression in current approval/review payload structure for clients that ignore new fields

## Current state snapshot

### Strengths

- Multi-agent extraction, generation, review, and refinement are already implemented.
- Heuristic fallbacks protect the workflow from empty model output.
- Coverage plan and review data are already exposed to the frontend.
- E2E tests already verify basic structural quality of generated cases.

### Gaps

- Large modules remain a maintainability risk (`frontend/src/App.jsx`,
  `backend/app/main.py`, and `backend/app/agents/test_case_agent.py`);
  frontend styles are now split under `frontend/src/styles/` behind a shared
  cascade entry point.
- Frontend/backend contracts now have generated high-traffic API constants and
  freshness checks; broader low-traffic API surfaces still need incremental
  coverage.
- Requirement import/sync is implemented for JIRA/Azure DevOps, but test-case lifecycle sync/export is not productized.
- Artifact fetching needs stronger SSRF hardening before broad production use.
- CI runs backend lint, backend unittest, offline benchmark scripts, API
  contract checks, frontend lint, frontend build, and mocked Playwright checks
  on every PR.
- Formatter configs are tracked, but broad mechanical formatting cleanup and
  CI format gates are intentionally split into issue #65.

## Delivery phases

### Phase 0 — Evaluation harness and benchmark corpus

**Outcome:** establish a measurable baseline before upgrading the engine.

#### Backend changes

- Add `scripts/evaluate_generation.py` to run benchmark inputs through the current engine.
- Add fixture inputs under `scripts/benchmark_inputs/` and expected trait summaries under `scripts/benchmark_expectations/`.
- Reuse existing response fields (`review`, `coverage_metrics`, `coverage_plan`) and augment reporting with derived metrics.

#### Frontend and E2E changes

- Expand `frontend/e2e/workflow.spec.js` to assert richer quality characteristics.
- Keep the current happy-path smoke flow intact.

#### Acceptance criteria

- Benchmark script runs locally against the existing app payload format.
- Results capture baseline values for approval rate, scenario coverage, and structural quality.
- Existing E2E flow remains green.

### Phase 1 — Coverage intelligence layer

**Outcome:** move from scenario-only planning to rule-aware, constraint-aware test design.

#### 1A. Data contracts and parsing

Add new optional models in `backend/app/models.py`:

- `BusinessRule`
- `FieldConstraint`
- `RolePermission`
- `StateTransition`
- `RiskSignal`
- `RequirementAnalysis`

Update response models so generated test-case results can optionally include requirement analysis and richer coverage metrics.

Add new JSON parsing helpers in `backend/app/utils/llm_json.py`:

- `parse_requirement_analysis_json()`
- optional helpers for rule and transition coverage summaries

#### 1B. Analysis agent and workflow integration

Create `backend/app/agents/analysis_agent.py`.

Responsibilities:

- Read approved requirements.
- Extract structured rules, constraints, roles, transitions, dependencies, and risks.
- Normalize analysis objects into safe defaults.
- Return deterministic fallback analysis when model output is incomplete.

Integrate the analysis output into `backend/app/agents/test_case_agent.py` so the generation path becomes:

1. Requirements
2. Requirement analysis
3. Coverage planning
4. Test-case generation
5. Validation/refinement

#### 1C. Validator and metrics upgrade

Extend heuristic validation in `backend/app/agents/test_case_agent.py` to measure:

- `business_rules_total`
- `business_rules_covered`
- `rule_coverage_ratio`
- `constraints_total`
- `constraints_covered`
- `constraint_coverage_ratio`
- `transitions_total`
- `transitions_covered`
- `transition_coverage_ratio`
- `high_risk_items_without_tests`

The validator should keep current approval semantics while adding clearer blocking issues when extracted rules are not covered.

#### 1D. Frontend visibility

Update `frontend/src/App.jsx` to show:

- a compact requirement-analysis panel
- extracted rule summaries per requirement
- coverage-gap warnings after generation
- clear distinction between scenario coverage and rule/constraint coverage

#### Acceptance criteria

- Requirement analysis is optional and backward compatible.
- Generated output improves rule and constraint coverage on benchmark inputs.
- The UI can display analysis summaries without blocking existing flow.
- Review payloads remain valid for export and refinement workflows.

### Phase 2 — Grounded context enrichment

**Outcome:** replace link-as-text context with analyzed, reusable product facts.

#### 2A. Data contracts

Add new optional models in `backend/app/models.py`:

- `ArtifactSource`
- `GroundedUIElement`
- `GroundedApiSurface`
- `GroundedWorkflow`
- `GroundedContext`
- `EnrichResponse` or an equivalent enriched response contract

Keep the existing `EnrichInput` payload shape stable where possible, but return richer structured output from `/requirements/enrich`.

#### 2B. Safe fetch and grounding services

Create:

- `backend/app/services/artifact_fetcher.py`
- `backend/app/services/context_grounding.py`

Responsibilities:

- fetch only supported remote artifacts with timeouts and size limits
- validate schemes and block unsafe requests
- parse HTML into visible UI facts
- summarize diagrams and screenshots into structured workflows or UI elements
- normalize all grounded artifacts into consistent response models

#### 2C. Backend integration

Update `backend/app/main.py` so `/requirements/enrich`:

- fetches and analyzes provided links
- returns grounded context for user review
- gracefully degrades when some artifacts fail analysis

Update `backend/app/agents/test_case_agent.py` so grounded context is used by:

- the coverage planner
- the generator
- the validator

Also consider adding optional `source_refs` or equivalent metadata to generated test cases so future automation and audits can trace where a case came from.

#### 2D. Frontend workflow

Update `frontend/src/App.jsx` to support:

- “Analyze context” action in the Context tab
- preview of extracted screens, fields, workflows, and artifacts
- user approval or exclusion of grounded items before generation
- richer context summary in the Generate tab

#### Acceptance criteria

- Context links are no longer only passed through as raw strings.
- Users can preview grounded context before generation.
- Generated test cases become more product-specific when artifacts are supplied.
- Existing generation still works when no context artifacts are provided.

## File-by-file change map

| File | Planned change |
| --- | --- |
| `backend/app/models.py` | Add requirement analysis and grounded-context models; extend response contracts with optional fields |
| `backend/app/utils/llm_json.py` | Add parsers for requirement analysis and grounded context payloads |
| `backend/app/agents/analysis_agent.py` | New analysis pipeline for rule/constraint/risk extraction |
| `backend/app/agents/test_case_agent.py` | Consume requirement analysis and grounded context; expand validation metrics |
| `backend/app/main.py` | Hosts `/requirements/enrich`, generation/refinement, integrations, billing, and export endpoints; should be split into routers next |
| `backend/requirements.txt` | Includes runtime dependencies for ADK, document parsing, FastAPI, integrations, and Firestore-backed services |
| `backend/app/services/artifact_fetcher.py` | Implemented safe artifact retrieval service with timeouts and size caps |
| `backend/app/services/context_grounding.py` | Implemented grounding and normalization service |
| `frontend/src/App.jsx` | Implements analysis visibility, context preview, integration import/sync, and workflow UI; should be split next |
| `frontend/e2e/workflow.spec.js` | Contains authenticated happy-path checks; add more focused analysis/grounding assertions |
| `scripts/evaluate_generation.py` | Implemented evaluation harness for generation quality |

## Testing strategy

### Backend unit tests

- normalization of analysis models
- parser behavior on malformed JSON
- heuristic coverage scoring
- safe artifact fetching edge cases
- grounding fallbacks and partial failures

### Frontend and E2E tests

- current authenticated parse/generate/export flow stays green
- analysis summaries render after generation
- grounded context preview renders after context analysis
- generation still succeeds when enrichment fails or is skipped

### Benchmark verification

Use the evaluation harness to compare:

- baseline engine
- Phase 1 engine
- Phase 2 engine

The harness should report metric deltas, not just pass/fail output.

## Rollout and compatibility

- Keep all new fields optional in API responses.
- Preserve the current shape of `GenerateTestCasesResponse` while appending new data.
- Default to current behavior when requirement analysis or grounding is unavailable.
- Treat grounding failures as warnings, not fatal errors.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Analysis output becomes too verbose or inconsistent | Normalize aggressively and enforce compact schemas |
| Grounding introduces slow network-dependent paths | Use timeouts, size caps, and partial-success responses |
| UI becomes overloaded with detail | Default to summaries with expandable detail |
| Approval scores regress unexpectedly | Gate changes behind benchmark comparisons and expanded E2E checks |
| Artifact fetching creates security concerns | Block unsafe schemes, cap response sizes, and validate content types |

## Recommended GitHub tracking model

Use three milestones:

1. `Phase 0 - Evaluation`
2. `Phase 1 - Coverage Intelligence`
3. `Phase 2 - Grounded Context`

Suggested labels:

- `type:enhancement`
- `type:epic`
- `area:backend`
- `area:frontend`
- `area:qa`
- `phase:0`
- `phase:1`
- `phase:2`

The issue-ready backlog is in `docs/github-issues-backlog.md`.

## Recommended implementation order

1. Stabilize current contracts and benchmark scripts.
2. Add CI for unittest, offline benchmark scripts, frontend build, and mocked E2E checks.
3. Split `backend/app/main.py` into routers and `frontend/src/App.jsx` into workflow components.
4. Split `backend/app/agents/test_case_agent.py` into orchestration, coverage, normalization, fallback, and prompt modules.
5. Introduce shared/generated frontend API types from FastAPI OpenAPI.
6. Harden artifact fetching and integration URL validation.
7. Productize export approval gates, draft overrides, and test-case lifecycle integrations.

## Modularization roadmap

Use behavior-preserving slices so each refactor can be validated by the same CI suite:

1. **Backend routers** — move route groups from `backend/app/main.py` into `backend/app/routers/` while keeping endpoint paths and response models unchanged.
	- Completed first slice: export endpoints now live in `backend/app/routers/export.py` and are registered from `backend/app/main.py`.
	- Completed second slice: test-case generation/refinement endpoints now live in `backend/app/routers/testcases.py`.
	- Completed third slice: requirement parsing/refinement/enrichment endpoints now live in `backend/app/routers/requirements.py`.
	- Completed fourth slice: JIRA connection/import/sync endpoints now live in `backend/app/routers/integrations_jira.py`.
	- Completed fifth slice: Azure DevOps connection/import/sync endpoints now live in `backend/app/routers/integrations_azure_devops.py`.
	- Completed sixth slice: authentication endpoints now live in `backend/app/routers/auth.py`.
	- Completed seventh slice: billing entitlement, ledger, credit grant, allocation, and org-summary endpoints now live in `backend/app/routers/billing.py`.
	- Completed eighth slice: usage reporting endpoints now live in `backend/app/routers/reports.py`.
	- Completed ninth slice: Playwright automation generation endpoint now lives in `backend/app/routers/automation.py`.
	- `testcases.py`
	- `requirements.py`
	- `export.py`
2. **Test-case agent internals** — split `backend/app/agents/test_case_agent.py` into orchestration, prompt builders, normalization, coverage metrics, fallback generation, and response hydration modules.
	- Completed first slice: coverage metrics, review helpers, deterministic fallback generation, and response hydration now live in `backend/app/agents/test_case_coverage.py`, `backend/app/agents/test_case_review.py`, `backend/app/agents/test_case_fallback.py`, and `backend/app/agents/test_case_hydration.py`; `test_case_agent.py` retains orchestration and prompt builders.
3. **Frontend workflow modules** — extract API calls into `frontend/src/api/`, shared workflow state into hooks, and large tab bodies into focused components.
4. **Shared contract generation** — use `scripts/export_openapi.py` in CI and generate frontend TypeScript types from that schema before larger UI refactors.
	- Completed first slice: `scripts/generate_frontend_api_types.py` emits committed declarations and endpoint constants for high-traffic workflow contracts, and CI checks they stay fresh.
5. **Integration provider abstraction** — introduce a common provider interface after router extraction so Jira/Azure DevOps sync stays behavior-compatible.

Do not combine these steps in one pull request. Each slice should pass backend unittest, offline benchmarks, frontend build, and the relevant mocked Playwright tests.

## Definition of done

This roadmap is complete when:

- benchmark inputs show measurable improvement over the baseline
- current workflow remains stable without optional context analysis
- reviewers can see why coverage is adequate or inadequate
- grounded artifacts materially improve specificity when supplied
- the issue backlog is fully tracked in GitHub and tied to the above milestones
