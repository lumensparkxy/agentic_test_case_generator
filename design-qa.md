# Design QA — shared design system (#241 / #246)

Visual result: passed for the reviewed actual pages. Migration validation: 158 of 159 browser tests pass; the remaining legacy live-generation test is tracked in #251. The full suite is not green.

## Visual baseline and evidence

The selected Overview, Use Cases and Test Cases direction was implemented in #239 / PR #240. This migration compares against those approved real-page captures rather than replacing their layout with a new design. Baseline captures are `implemented-overview-final.png`, `implemented-use-cases-final.png` and `implemented-test-cases-final.png` under `/Users/m1/.codex/visualizations/2026/09/10/01a08b92-58ae-7fd2-94e0-74fc99835662/overview-design/`.

Final captures are in its `design-system/` subdirectory: `overview.jpg`, `use-cases.jpg`, `test-cases.jpg`, `requirements.jpg`, `home.jpg`, `use-cases-mobile.jpg`, `test-cases-mobile.jpg`, `test-cases-mobile-detail.jpg`, `home-mobile.jpg` and `settings-mobile.jpg`. Screenshots are local review evidence, not committed assets. Desktop CSS viewport: 1488 × 1056; narrow viewport: 390 × 844. Screenshot transport scales desktop output to approximately 1477 × 1048. Captures were inspected at equivalent display size. Mobile Test Cases captures include a scrolled view below its shell.

Each of the three desktop baseline/final pairs was opened in the same visual comparison input. The shared header/sidebar geometry, flat surfaces, page headings, next action, requirement groups and list/detail composition remain consistent. Shared badges now use semantic text colors and icons; radios have a clearer selected treatment; controls use common dimensions and focus appearance. Real long titles, all case metadata, quality findings and the explicit review decision remain intact. The actual project still shows 8 requirements, 28 scenarios and 25 cases, quality 65/100 and blocked export.

Requirements uses native editable table cells within a bounded, labeled scroll container. Home uses the same controls and status treatments while retaining its workspace composition. Narrow Home and Use Cases wrap long content; Settings retains its scrollable dialog; Test Cases stacks its collection and detail. No separate gallery was created.

## Findings resolved

- Consolidated duplicate control, badge, table and collection appearance into tokens and `ui.css`; removed unused table/card styles, old workflow tabs/stepper and duplicate Escape hook.
- Fixed active-sidebar white-text rules leaking into shared status labels. All accessibility scenarios subsequently passed.
- Restored the shared 44px icon-button treatment for Settings and dialog close controls after removing old global button appearance.
- Updated stale test selectors for real tab semantics, visible case counts, Overview navigation and machine-quality wording. Approval and export assertions remain covered by dedicated gates and the lifecycle scenario.
- The long-lived development tab briefly retained old component identities during HMR and showed unassociated workflow labels. Fresh-browser checks verify both Approval threshold controls and integration fields have accessible names. No production-build failure was observed.

## Validation

- Each of the five stages ran build, lint and formatting checks. Foundations: 6 focused browser tests; artifact collections: 40; workspace: 67 unique scenarios after contrast correction; remaining surfaces: 60.
- Final `npm run lint`, `npm run format:check`, `npm run build`: passed. Existing JavaScript chunk-size warning remains (585.43 kB).
- Final `npm run test:e2e -- --workers=4 --retries=0 --timeout=45000`: **158 passed, 1 failed**. Covers populated, empty, filtered-empty, loading, error, disabled and selected actual-page fixture states; keyboard/focus, responsive reflow, accessibility, identity, stale responses, approvals, export and workflow actions.
- Subsequent focused `shared-project-design.spec.js` run: **8 passed**, including an added assertion for both labeled workflow threshold fields.
- `git diff --check`: passed.
- No backend API/schema changes, new UI framework, dependencies or live review/generation/export mutations.

## Remaining limitation

[#251](https://github.com/lumensparkxy/agentic_test_case_generator/issues/251) tracks the unchanged live-generation test in `frontend/e2e/workflow.spec.js:18`: it opens Home and waits for a file input that now lives in a project Requirements workbench. The obsolete helper also exists before this migration. It times out before a generation/export request. Repair requires updating that integration scenario to the current project workflow while preserving its live quality assertions. It was not skipped or weakened to make the suite appear green.

The migration is implemented in five stacked PRs and awaits merge. Completion of the full-suite acceptance gate remains dependent on #251.
