# Home-first VoiceOver and Chrome smoke

This is the manual assistive-technology companion to the deterministic
Playwright and Axe gate for issue #202. It checks the Home and Use Cases paths
with macOS VoiceOver in Google Chrome. It is a targeted product smoke check,
not a WCAG conformance audit or certification.

## Preconditions

- Use the current issue branch, deployed test environment, or production build
  under evaluation and record which one was used.
- Use an authenticated account with at least one project and one pending Use
  Cases review. Do not approve, request changes, regenerate, execute, or export
  during a read-only smoke.
- Record the macOS and Chrome versions, viewport or zoom, date, commit or
  deployment, and result below.
- Turn VoiceOver on with Command-F5. Turn it off when the smoke is complete.

## Home

1. Load Home directly and confirm VoiceOver starts at the document rather than
   jumping past the global skip link.
2. Move to **Skip to main content**, activate it, and confirm focus reaches the
   Home main region and its **Home** heading is announced.
3. Traverse the global navigation. Confirm exactly one current destination is
   announced and Home, Projects, Reviews, Runs, and Reports have clear names.
4. Traverse **Continue working**, **My work**, **Projects**, and **Recent
   activity**. Confirm project names, durable status text, counts, and action
   purpose are understandable without relying on color.
5. Open and close the project chooser with the keyboard. On compact widths,
   press Escape in the chooser and confirm only the chooser closes, workspace
   controls stay open, and focus returns to the chooser trigger.

## Use Cases

1. Open a pending Use Cases review from Continue working or Review Inbox.
   Confirm the route change moves focus to the Use Cases main region.
2. Confirm the project navigation identifies Use Cases as the current
   destination and exposes each workflow state with text, not color alone.
3. Activate **Skip to review decision** and confirm focus reaches the decision
   group.
4. Traverse search, scenario groups, details, the Approve/Request changes radio
   controls, comment field, and decision buttons. Confirm their names, state,
   requirements, and order are clear.
5. Do not submit a decision. Return Home with the global navigation and confirm
   focus reaches the new main region.

## Result record

| Date | Environment/build | macOS / Chrome | Home | Use Cases | Notes |
| --- | --- | --- | --- | --- | --- |
| 2026-07-18 | `codex/issue-202-home-first-ux-gates` at `0155dc1`; local current-branch Vite/API, deterministic workspace-summary fixture for populated Home, and authenticated Firestore-backed `mytestx` Use Cases data | macOS 26.5.2 (25F84) / Chrome 150.0.7871.125 | Pass | Pass | VoiceOver was enabled and verified in System Settings for the full 1280×720 Chrome keyboard pass, then disabled. Verified first-focus skip links, Home and Use Cases route focus, named/current global and project navigation, chooser focus restoration, all four Home regions, scenario search/details, decision radio states, comment/button order, and Home return focus. No review, generation, execution, or export action was submitted. The deployed API/index rollout gap found during setup is tracked in [#214](https://github.com/lumensparkxy/agentic_test_case_generator/issues/214). This is a targeted smoke result, not WCAG certification. |

## Automated companion evidence

Run:

```bash
cd frontend
CI=1 E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e:home-first
```

`frontend/e2e/accessibility-navigation.spec.js` covers initial skip-link access,
SPA and Back/Forward focus, compact nested-disclosure focus restoration,
persistent polite status and alert semantics, stale-state isolation, and ten
Axe scans: empty Home, populated Home, project Overview, Use Cases, and
Automation at 390px and 1440px. The helper uses supported WCAG A/AA tags, has no
rule or selector exclusions, and fails on serious or critical violations.

Automated checks reduce regression risk but do not replace the VoiceOver smoke,
broader manual usability review, or formal accessibility evaluation.
