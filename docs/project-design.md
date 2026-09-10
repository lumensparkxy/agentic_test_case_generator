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

- `Button`: primary/secondary/danger/plain variants (use `IconButton` for labeled icon-only actions), normal/compact sizes, optional busy state. Default type is button; form submissions must explicitly use type="submit".
- `Input`, `Select`, `Textarea`, `Checkbox`, `Radio`, `SearchField`: controlled native props and refs pass through. `Field` connects its single control to label, hint and error text; children-only mode supports existing compound fields.
- `Badge`: explicit tone and label with a library icon; domain adapters decide the meaning of status. `Surface`, `Alert`, `Disclosure`, `Progress`, `Menu` share visual treatment while retaining semantic roles and existing handlers.
- `Table` uses native table children; `TableScroll` provides a labeled keyboard-scrollable region. Density is comfortable by default, compact when specified. Features keep column content, sorting, selection and bulk-action logic.
- `List`, `ListItem`, `SelectableItem`, `ListDetail`, `CollectionToolbar`, `ResultCount`, `CollectionState` share collection composition and accessible states. List variants are plain, collection, grouped and selectable; a navigation row is a link, a selected row is a button, and grouped content uses Disclosure.
- `TabList`, `Tab`, `TabPanel` provide arrow/Home/End activation and roving tab stops. Selected values and panel IDs remain controlled by features.
- `Dialog` supports opt-in focus trapping/restoration and Escape callbacks. Existing specialized popovers retain their feature-owned keyboard behavior; modal consumers use managed focus unless they already own it.

Migration checklist: #242 foundations (PR #247), #243 artifacts (PR #248), #244 workspace (PR #249), #245 other surfaces (PR #250), and #246 consolidation are implemented on stacked review branches. Full validation: 158 browser tests passed; the unchanged legacy live-generation scenario failed and is tracked in #251. Build, lint and formatting pass. See `design-qa.md` for evidence and the remaining acceptance limitation. Visual review uses actual application pages, not a separate gallery.


## Adoption rules

Shared components own controls, table cells, status appearance, focus and density. Feature CSS owns placement, column widths, responsive composition and domain-specific content. Put new shared variants in `ui.css`; do not restore removed global input/button styles or create page-specific copies. ESLint rejects raw interactive controls and tables outside the UI library, and rejects dependencies from the UI library into feature code. Native semantic children such as `thead`, `tr`, `th` and `td` remain valid within Table.

The default row density is comfortable (12px by 16px table cell padding); compact tables use 8px by 12px. Normal controls have a 44px minimum height; compact controls use 36px. Badge supports subtle, inline and compact presentation with semantic text colors tested on tinted backgrounds. Table scroll containers must have a descriptive accessible name. Dialog focus is managed for modal workflows; the project chooser remains a nonmodal popover with its existing focus behavior.

## Screen migration checklist

| Surface | Shared patterns | Story |
| --- | --- | --- |
| Home, Projects and recovery screens | SearchField, Badge, List, CollectionState, Button, Link | #244 |
| Reviews, Runs and Reports indexes | CollectionToolbar, List, Badge, filters and states | #244 |
| Overview and project navigation | Shared header, Link, Button, Badge and Disclosure | #245 |
| Requirements and source imports | Table/TableScroll, labeled fields, selection controls, actions | #243 / #245 |
| Context and template setup | Field, Input, Select, Textarea, Button and Surface | #242 / #245 |
| Use Cases | List, Disclosure, Radio, Textarea, Badge, Alert and actions | #243 |
| Test Cases and impact analysis | ListDetail, SelectableItem, Table, TabList/Tab/TabPanel, states | #243 / #245 |
| Traceability, coverage and analysis | Table, List, Disclosure, Badge and states | #243 |
| Automation, execution and report evidence | Table, Checkbox, Input, Select, Alert, actions | #243 / #245 |
| Export and integration forms | Field, Input, Select, Textarea, Checkbox, alerts and actions | #245 |
| Settings, authentication and confirmation dialogs | Dialog, TabList, labeled controls, shared focus management | #245 |
| Account/project menus, diagnostics and usage | Menu, Dialog, Disclosure, List and controls | #245 |

Business-specific content remains composed by its feature. Backend contracts, auth, persistence, export format, sorting and filter rules are not part of this visual library. Removed unused legacy table/card styles and obsolete stepper/tab components; there is no separate component gallery or added dependency.

## Sequential project navigation (#253)

Back/Next follows `PROJECT_NAV_ITEMS`, the same destination order as the sidebar: Requirements → Context → Use Cases → Test Cases → Automation → Reports. It does not increment legacy panel IDs. Use Cases provides navigation in both populated and missing-artifact states. Visiting a destination is separate from approving an artifact or running a workflow; existing mutation and export gates remain authoritative.

Test Cases route entry, including browser history and reload, opens the normal generation/review workbench. Template Setup is an explicit local subview. Its Back to Test Cases action and the header Generate and review action return to that workbench without losing in-session template edits. Template Setup is not a sequential workflow step.

The existing first-generation operation still creates the initial Use Cases artifact from the Test Cases workbench. When no Use Cases snapshot exists, the page retains its explanation and appropriate prerequisite link; navigation itself does not generate or approve an artifact.
