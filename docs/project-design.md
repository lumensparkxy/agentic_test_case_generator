# Shared project design

Tracked by [#239](https://github.com/lumensparkxy/agentic_test_case_generator/issues/239), based on the selected Overview, Use Cases and Test Cases designs. Builds on scenario-count correction #237.

Project destinations share `ProjectPageHeader`, the project navigation, and existing color and typography tokens through `project-design.css`. Route selection is independent of workflow status. Overview has no invented completion status; other destinations retain text and icons for their current state.

Overview prioritizes the server-provided next action, artifact counts, and actionable review/export blockers. Additional workbenches remain available in a disclosure.

Use Cases groups scenarios by requirement. Scenario objectives and metadata, machine quality findings, coverage and review history remain available through disclosures. The decision form starts without a selected option; the reviewer explicitly chooses Approve or Request changes. Existing comment requirements, version checks, retries and persistence continue to apply.

Test Cases presents a searchable selectable list alongside the current case, with steps, expected results, test data and full metadata. Search covers IDs, titles and linked requirement IDs. The first matching case is selected when the existing selection disappears. Quality findings are expandable. Template setup controls the export format independently of the review presentation. Traceability, coverage, analysis, diagnostics, refinement and export controls remain available.

On narrow screens the review columns stack, navigation becomes a disclosure, and long labels wrap. Desktop navigation can still collapse to icons. Focus, status announcements and semantic labels remain part of the interaction contract.

Validation: frontend build, lint, formatting, and the `test:e2e:home-first` suite, including the focused `shared-project-design.spec.js` checks. Visual comparison evidence and known constraints are recorded in the root `design-qa.md`.


## Application component library (epic #241)

The internal library lives in `frontend/src/components/ui/`. Import directly from controls, surfaces, collections, tabs or dialog. It uses React and native semantic HTML; features own data, filtering, API calls and mutations.

- `Button`: primary/secondary/danger/plain variants, normal/compact sizes, optional busy state. Default type is button; form submissions must explicitly use type="submit".
- `Input`, `Select`, `Textarea`, `Checkbox`, `Radio`: controlled native props and refs pass through. `Field` connects its single control to label, hint and error text; children-only mode supports existing compound fields.
- `Badge`: explicit tone and label with a library icon; domain adapters decide the meaning of status. `Surface`, `Alert`, `Disclosure`, `Progress`, `Menu` share visual treatment while retaining semantic roles and existing handlers.
- `Table` uses native table children; `TableScroll` provides a labeled keyboard-scrollable region. Density is comfortable by default, compact when specified. Features keep column content, sorting, selection and bulk-action logic.
- `List`, `ListItem`, `SelectableItem`, `ListDetail`, `CollectionToolbar`, `ResultCount`, `CollectionState` share collection composition and accessible states. List variants are plain, collection, grouped and selectable; a navigation row is a link, a selected row is a button, and grouped content uses Disclosure.
- `TabList`, `Tab`, `TabPanel` provide arrow/Home/End activation and roving tab stops. Selected values and panel IDs remain controlled by features.
- `Dialog` supports opt-in focus trapping/restoration and Escape callbacks. Existing specialized popovers retain their feature-owned keyboard behavior; modal consumers use managed focus unless they already own it.

Migration checklist: #242 foundations in progress; #243 artifacts pending; #244 workspace pending; #245 other surfaces pending; #246 consolidation and full validation pending. Visual review uses actual application pages, not a separate gallery.
