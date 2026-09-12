---
name: repository-qa
description: Explore, debug, verify, or add Playwright coverage for a specific UI flow in this repository. Use existing JavaScript E2E conventions; a request to explore does not itself request generated test files or a full product audit.
---

# Repository browser QA

Identify the requested route, action/state, and expected result. Inspect
`frontend/package.json`, `frontend/playwright.config.js`, and a neighboring spec
for the relevant flow. Resolve the target from the request, existing server, and
configuration before asking for a URL. Follow [AGENTS.md](../../../AGENTS.md)
for scope and work-record selection.

Reuse the existing environment and server after checking readiness. If needed,
start Vite from `frontend/` with `npm run dev -- --host 127.0.0.1 --strictPort` and
verify the URL responds before testing. Keep any user-specified host/port. Prefer
existing mocked API fixtures and synthetic sessions for local regression work;
no Firebase login, live model, or production writes are needed for that path.

For an existing regression test, run the scoped spec with the repository script:

```bash
# From frontend/, with a ready local server:
E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e -- e2e/workflow-navigation.spec.js
```

Choose one available browser tool/workflow for interactive exploration. Inspect
fresh DOM/locators before interactions and check the expected state afterward.
Use screenshots for visual claims, and check relevant console/runtime errors.
For layout changes inspect the affected desktop/mobile sizes. Scope verification
to the requested journey; use a full product audit only for an audit request.

Exploration produces findings and evidence. Create/update test files only when
requested or needed to verify an authorized behavior change. Use JavaScript
`@playwright/test` in `frontend/e2e/*.spec.js`, matching nearby fixtures/support
helpers and stable accessible locators. Keep generated execution-runtime tests
separate. Run the changed spec, repair evidenced failures within scope, and stop
on an external blocker rather than retrying indefinitely or weakening assertions.

Do not use a demo URL, credentials, or form values from a template. A form fill
uses the user's target and supplied/inferred authorized values; submission needs
authorization covering that action. Preserve user-owned tabs and sessions; close
only resources created for the task when they are no longer needed.

Keep transient screenshots/traces in ignored output or outside the checkout.
Report the tested flow, observed result, relevant evidence, and blockers. Follow
[Testing Patterns](../../../docs/codebase/TESTING.md) for additional checks.
