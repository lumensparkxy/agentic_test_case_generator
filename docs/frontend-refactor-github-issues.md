# Frontend Architecture Refactor — Historical GitHub Issue Plan

> Historical note as of 2026-06-12: GitHub issue and PR automation is now
> available for this repository. This file is retained as the original frontend
> refactor issue plan, not the active source of truth. Current issue-backed
> roadmap status lives in `docs/github-issues-backlog.md` and implementation
> evidence lives in `docs/requirements_traceability.md`.

## Current status

- Workflow state extraction completed under [#39](https://github.com/lumensparkxy/agentic_test_case_generator/issues/39), with domain hooks in `frontend/src/hooks/`.
- CSS modularization completed under [#40](https://github.com/lumensparkxy/agentic_test_case_generator/issues/40), with feature-owned styles under `frontend/src/styles/`.
- High-traffic frontend API contract generation completed under [#43](https://github.com/lumensparkxy/agentic_test_case_generator/issues/43), with generated contracts under `frontend/src/api/generated/`.
- API base URL, request ID, API error, JSON, and blob helpers live in `frontend/src/services/apiClient.js`.
- Reusable layout, workflow, integration, requirement, generation, automation, and export components live in `frontend/src/components/`.
- `frontend/src/App.jsx` remains the top-level workflow/layout orchestrator; new frontend architecture work should be captured as fresh issue-scoped follow-up under [#35](https://github.com/lumensparkxy/agentic_test_case_generator/issues/35).

## Suggested labels

- `type:epic`
- `type:refactor`
- `area:frontend`
- `area:architecture`
- `area:qa`
- `phase:frontend-refactor`

## Suggested milestone

- `Frontend Architecture Refactor`

## Epic 1 — Refactor monolithic frontend architecture

**Labels:** `type:epic`, `type:refactor`, `area:frontend`, `area:architecture`

### Summary

Break down the monolithic `frontend/src/App.jsx` and former
`frontend/src/App.css` into services, utilities, hooks, focused components, and
modular styles while preserving the current user workflow.

Current implementation note: frontend styles enter through
`frontend/src/styles/index.css`, with shared foundations in `tokens.css` and
`base.css` and feature-owned selectors in named style modules.

### Acceptance criteria

- [ ] `App.jsx` is reduced to orchestration and top-level layout responsibilities
- [ ] Shared constants and pure helpers live outside `App.jsx`
- [ ] Backend API calls are centralized behind service helpers
- [ ] High-traffic UI sections are reusable components
- [ ] Workflow state is grouped into custom hooks or reducers where practical
- [ ] CSS is split into shared foundation and feature/component styles
- [ ] Frontend production build remains green
- [ ] Existing E2E workflows remain compatible

---

## Issue 1 — Phase 1: extract frontend constants and pure helpers

**Labels:** `type:refactor`, `area:frontend`, `area:architecture`, `phase:frontend-refactor`

**Status:** Completed in the current source tree. Constants and pure helpers live
in `frontend/src/constants/workflow.js`, `frontend/src/utils/workflow.js`,
`frontend/src/utils/requirements.js`, and `frontend/src/utils/usage.js`.

### Summary

Move static constants and pure helper functions out of `App.jsx` into dedicated modules.

### Scope

- Add shared workflow/source/status constants
- Extract requirement metadata helpers
- Extract usage and billing formatting helpers
- Extract workflow score/settings helpers
- Keep behavior unchanged

### Acceptance criteria

- [ ] `App.jsx` imports constants/helpers instead of defining them inline
- [ ] Extracted helpers have no React dependency
- [ ] Existing frontend build succeeds

---

## Issue 2 — Phase 2: add frontend API service layer

**Labels:** `type:refactor`, `area:frontend`, `area:architecture`, `phase:frontend-refactor`

**Status:** Completed for shared client helpers. `frontend/src/services/apiClient.js`
owns base URL resolution, request ID headers, API error parsing, and blob
downloads. Generated endpoint constants for high-traffic workflows live in
`frontend/src/api/generated/`.

### Summary

Centralize API base URL resolution, request ID generation, API error parsing, and download helpers.

### Scope

- Add `frontend/src/services/apiClient.js`
- Move `API_BASE`, `createRequestId`, and `parseApiError`
- Add helpers for JSON parsing and blob downloads where useful
- Preserve auth injection in `App.jsx` until workflow hooks are extracted

### Acceptance criteria

- [ ] API helper functions are imported from the service layer
- [ ] Auth/session behavior remains unchanged
- [ ] Frontend build succeeds

---

## Issue 3 — Phase 3: extract reusable UI components

**Labels:** `type:refactor`, `area:frontend`, `area:architecture`, `phase:frontend-refactor`

**Status:** Completed for high-traffic sections in the current source tree.
Reusable components live under `frontend/src/components/`, including auth,
layout, settings, workflow, requirements, integrations, generation, automation,
and export areas.

### Summary

Move repeated or self-contained JSX sections into focused components.

### Scope

- Extract `AuthProviderIcon`
- Extract workflow diagnostics rendering
- Extract workflow settings panel rendering
- Extract status/billing display where practical
- Keep container state in `App.jsx` for this phase

### Acceptance criteria

- [ ] Components receive data and callbacks through props
- [ ] No behavior changes to dialogs, settings, or diagnostics
- [ ] Frontend build succeeds

---

## Issue 4 — Phase 4: group workflow state into custom hooks

**Labels:** `type:refactor`, `area:frontend`, `area:architecture`, `phase:frontend-refactor`

**Status:** Completed under [#39](https://github.com/lumensparkxy/agentic_test_case_generator/issues/39).
Workflow state hooks live in `frontend/src/hooks/`.

### Summary

Move related state and side-effect orchestration into custom hooks to reduce `App.jsx` state explosion.

### Scope

- Add `useRequirementWorkflow`
- Add `useTestCaseWorkflow`
- Add `useJiraIntegration`
- Add `useAzureDevOpsIntegration`
- Add `useBillingUsage` or equivalent
- Avoid changing backend contracts

### Acceptance criteria

- [ ] Related state and handlers are grouped by domain
- [ ] Integration flows still work with existing endpoints
- [ ] Frontend build succeeds
- [ ] E2E smoke workflow remains green

---

## Issue 5 — Phase 5: modularize CSS and remove dead styles

**Labels:** `type:refactor`, `area:frontend`, `area:architecture`, `area:qa`, `phase:frontend-refactor`

**Status:** Completed under [#40](https://github.com/lumensparkxy/agentic_test_case_generator/issues/40).
Feature-owned style modules live under `frontend/src/styles/` and are imported
through `frontend/src/styles/index.css`.

### Summary

Split the large global stylesheet into shared foundations and feature/component styles, then remove styles no longer referenced by the UI.

### Scope

- Create shared base/style-token files
- Move auth/settings/integration/generation styles into feature files
- Remove duplicate selectors where safe
- Audit likely dead classes such as legacy export/integration/context review styles

### Acceptance criteria

- [ ] Global CSS contains only shared tokens/base/layout primitives
- [ ] Feature styles are grouped by ownership
- [ ] No visible regression in the main workflow
- [ ] Frontend build succeeds

---

## Issue 6 — Phase 6: add regression validation for the refactor

**Labels:** `type:refactor`, `area:frontend`, `area:qa`, `phase:frontend-refactor`

**Status:** Completed through CI and issue-specific validation evidence. The
current CI workflow runs frontend lint, formatting checks, production build, and
focused mocked Playwright E2E on PRs.

### Summary

Add or update validation coverage so the architecture refactor can continue safely.

### Scope

- Run production build
- Run existing Playwright workflow tests when environment allows
- Add focused tests for extracted pure helpers if a frontend test runner is introduced
- Document any manual validation gaps

### Acceptance criteria

- [ ] `npm run build` succeeds
- [ ] E2E smoke tests pass or documented blockers are captured
- [ ] Refactor follow-up risks are documented
