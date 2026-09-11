# Shared project design

Tracked by [#239](https://github.com/lumensparkxy/agentic_test_case_generator/issues/239), based on the selected Overview, Use Cases and Test Cases designs. Builds on scenario-count correction #237.

Project destinations share `ProjectPageHeader`, the project navigation, and existing color and typography tokens through `project-design.css`. Route selection is independent of workflow status. Overview has no invented completion status; other destinations retain text and icons for their current state.

Overview prioritizes the server-provided next action, artifact counts, and actionable review/export blockers. Additional workbenches remain available in a disclosure.

Use Cases shows one scenario per table row, with source requirement, ID, title/objective, review status and multi-select quality flags. Titles and objectives are shown directly without a row Details disclosure; machine quality, coverage and review history remain in the summary disclosure. A compact sticky bar opens a shared confirmation dialog for Approve all or Request changes. The chosen action is selected in the dialog; opening or cancelling never submits a decision. The dialog explicitly covers every scenario in the saved version, including search-hidden scenarios. Comments are optional for approval and required for changes. Success closes the dialog, updates the bar and retains review details/history. Existing comment requirements, version checks, retries and persistence continue to apply.

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

## Compact typography and resizable panes (#255)

The shared body token is 14px, secondary text remains 13px, page titles use 28px (26px narrow), and section titles use 20px. Keep 44px normal control targets; reducing reading size must not reduce hit areas. Workspace headings and panel descriptions consume this shared scale rather than independent oversized values.

`ResizablePanes` in `components/ui/resizable-panes.jsx` takes exactly two content children. It owns presentation state only: `defaultSize` is the first pane percentage, `minFirst`/`minSecond` are minimum pixel widths, `label` names the accessible separator, and `storageKey` optionally persists a layout preference under `tcg.panes.*`. Nest splits for additional panes. It observes available container width and stacks the children without a separator when the minimum widths cannot fit. Browser storage failures do not disable interaction. Mount with a new React key when changing the identity of a stored layout.

Test Cases uses it through `ListDetail` (38% initial list width, 260/360px minima). Use Cases now uses full-width scenarios and a sticky review bar (#259), since its decision covers the entire snapshot. Drag the divider, use Left/Right arrows (Shift for larger steps), Home/End for limits, or Enter/double-click to reset. It preserves child identity while resizing, so selection, filters and review state remain feature-owned. Fixed navigation and ordinary card grids are not converted to resizable content panes.

Validation: 152 tests in `test:e2e:home-first` pass, including pointer drag, keyboard reset/limits, width persistence across reload, compact typography and responsive/accessibility regression checks. Live desktop Test Cases and Use Cases were inspected with actual artifacts.

## Test case row and step presentation (#257)

Case list summaries use a single inline flow for ID and title only. Priority and linked requirements remain in the selected detail pane, and linked-requirement search remains supported. Do not force these fields onto separate lines or truncate titles: wrapping follows the resized pane width. Case selection remains a button with its existing accessible name/state.

Steps use the shared compact Table inside a named TableScroll region. Columns are Step, Action and Expected result, with column headers and sequential row headers. Step-specific test data stays in the Action cell. All steps are always visible; there is no Show all/fewer toggle. The missing expected-result fallback remains available. The table wraps long cell content; at very narrow widths its 320px minimum scrolls inside the named container without widening the document.

Validation: 50 focused shared-design, accessibility and responsive tests pass; build/lint/format pass. Actual local Test Cases was inspected with long case names and step content.

## Whole-snapshot review actions (#259)

Use Cases composes the shared Button, Radio, Textarea, Alert and focus-managed Dialog. Keep decisions separate from collection navigation and filters. The bottom bar identifies the complete scenario/group count; the dialog identifies the version and scope. Review details and the quality/history disclosure preserve recorded comments and reviewer provenance. A successful decision dismisses the dialog and restores focus to its trigger. Escape/Cancel dismiss without mutation; dismissal is disabled while saving or reloading. Conflicts and saved-but-refresh-failed responses remain in the dialog with their existing recovery actions. Feature CSS owns sticky placement and dialog dimensions; control appearance remains shared.

## Scenario review table (#261)

`ScenarioReviewTable` composes the shared Table/TableScroll, Select, Checkbox, Disclosure, Button and Alert components. The table wraps long content and scrolls inside a labeled, focusable container on narrow screens. Status and quality-flag filters are separate from the editable row controls; multiple selected flag filters match any selected flag and combine with the status and text filters.

Row choices are **Needs review**, **Approved**, and **Changes requested**. Reviewer flags are Ambiguous, Duplicate, Missing coverage, Not testable, and Incorrect requirement mapping; they are separate from automated machine findings. Row edits are drafts until **Save reviews**. Counts report persisted approvals; unsaved reviews are labeled. Save/discard is required before bulk actions, so hidden drafts cannot be overwritten accidentally. Saving rows does not approve the whole version. Confirmed **Approve all** explicitly marks every current scenario approved, including filtered-out or changes-requested rows, then records the whole-version approval. Confirmed Request changes marks every scenario accordingly and requires a comment.

Every save is bound to the project revision and artifact snapshot. Failed saves retain drafts; unchanged retries reuse request identity. Conflicts require an explicit reload/discard. Saving edits to an approved version clears its whole-version decision. Regeneration carries forward a row only if its source group and scenario content match exactly; changed/new scenarios reset to Needs review. New snapshots always require a fresh whole-version decision. Existing whole-version approvals remain visible as approved rows until edited or regenerated.

See [scenario review persistence](scenario-review-persistence.md) for the API and audit contract.

## Direct dropdowns and resizable columns (#263)

Use Cases uses shared `MultiSelect` and `ResizableTable` components. Quality flags have a uniform visible label and selected count; the accessible label retains scenario context. The checkbox popup uses the native popover top layer to avoid table-scroll clipping, and supports Escape/light dismissal and a Done action. Opening it does not expand the row. Titles/objectives no longer include nested Details.

Drag an internal divider between column headers; the final column has no outer resize handle. Adjacent columns exchange width while the table width stays fixed. Keyboard Left/Right adjusts by 10px (Shift: 40px), Home/End applies the neighboring columns’ limits, and Enter/double-click restores that column's default width. Width preferences persist locally (the current grouped-table key is documented below); unavailable storage does not block resizing. Minimum widths preserve usable controls, and wider tables scroll within their labeled container. Layout changes never submit or clear row reviews.

## Requirement-grouped tables (#265)

Use Cases displays each requirement ID and full text inline, wrapping within the group width without a per-group count (#284), in a non-collapsible `.ui-table-group-heading`, followed by a named four-column table. Columns are Use Case ID, Title / objective, Review status and Quality flags. Search, filters, save/discard controls and overall review actions remain page-level; groups without matching rows disappear without discarding draft reviews.

`ResizableTable` supports controlled `widths` (pixel array or `null` for default proportions) and `onWidthsChange(nextWidths)`. A page owning multiple tables should call `useTableColumnWidths(columns, storageKey)` once and pass the returned state/setter to every table. Standalone tables may continue using `storageKey` for internal persistence. Pass `aria-labelledby` to name each table from its requirement heading. Group headings wrap outside each labeled horizontal scroll container.

Use Cases stores the synchronized layout under `tcg.columns.use-cases-grouped-v1`; the previous five-column preference remains unused. Dragging or keyboard-resizing any group's header updates every visible table and groups restored by clearing filters. Review draft identity and persistence remain owned by the page.

The fixed outer-edge behavior (#267) preserves both neighboring minimum widths during pointer and keyboard resizing. Quality flags can be resized only from its left divider; the table border is not an interactive separator.

## Approved knowledge and generation provenance

`components/knowledge/KnowledgePanel` uses the shared collection toolbar, table, fields, badges and focus-managed dialogs on Context and Settings → Personal knowledge. The project receives controlled scope and an authenticated request callback; API lifecycle and selection remain in `useKnowledge`. Keep project/personal scope in component identity so pending requests and dialogs cannot carry across owners or projects. The compact toolbar wraps on mobile; only the named table region scrolls horizontally.

An active entry with a proposed edit displays both versions. Approve knowledge is separate from artifact review. Personal promotion creates an inactive proposal; selection requires approval and an explicit per-project action. Never style these decisions as the artifact's approval/export gate. Use labeled badges rather than color alone. Loading, failure/retry, empty and filtered-empty states use the shared collection state pattern.

The read-only skill catalog uses shared badges to show Skills on/off and Memory on/off per stage from the backend feature map. An absent value says status unavailable; do not infer that an available skill package is enabled for generation.

`GuidanceUsed` is the compact, expandable provenance view on Requirements, Use Cases, Test Cases and Automation. Its key includes owner/project and manifest identity; historical wording resolves by recorded revision. Missing history says Guidance not recorded. Missing/deleted content says unavailable. Newer guidance is informational and never changes artifact approval. Deterministic execution preview shows the source test-case manifest separately.

The shared recovery dialog pauses a generation that cannot load memory and offers Retry loading guidance, Run without remembered guidance, and Cancel generation. Only an explicit bypass changes that input; failed suggestion retries never repeat generation or artifact review.

Use Cases opts into `ResizableTable`'s `responsiveMinWidth={800}` (#284). On container resize, stored widths share the available space above each column's minimum width; groups remain aligned and the table fits the container down to 800px, then scrolls within it. Browser resizing does not overwrite the saved preference. Other table consumers retain their existing sizing behavior.
