# Shared project design

Tracked by [#239](https://github.com/lumensparkxy/agentic_test_case_generator/issues/239), based on the selected Overview, Use Cases and Test Cases designs. Builds on scenario-count correction #237.

Project destinations share `ProjectPageHeader`, the project navigation, and existing color and typography tokens through `project-design.css`. Route selection is independent of workflow status. Overview has no invented completion status; other destinations retain text and icons for their current state.

Overview prioritizes the server-provided next action, artifact counts, and actionable review/export blockers. Additional workbenches remain available in a disclosure.

Use Cases groups scenarios by requirement. Scenario objectives and metadata, machine quality findings, coverage and review history remain available through disclosures. The decision form starts without a selected option; the reviewer explicitly chooses Approve or Request changes. Existing comment requirements, version checks, retries and persistence continue to apply.

Test Cases presents a searchable selectable list alongside the current case, with steps, expected results, test data and full metadata. Search covers IDs, titles and linked requirement IDs. The first matching case is selected when the existing selection disappears. Quality findings are expandable. Template setup controls the export format independently of the review presentation. Traceability, coverage, analysis, diagnostics, refinement and export controls remain available.

On narrow screens the review columns stack, navigation becomes a disclosure, and long labels wrap. Desktop navigation can still collapse to icons. Focus, status announcements and semantic labels remain part of the interaction contract.

Validation: frontend build, lint, formatting, and the `test:e2e:home-first` suite, including the focused `shared-project-design.spec.js` checks. Visual comparison evidence and known constraints are recorded in the root `design-qa.md`.
