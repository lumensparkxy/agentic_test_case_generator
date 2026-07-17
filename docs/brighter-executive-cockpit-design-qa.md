# Brighter Executive Cockpit Design QA

## Source Visual

Issue #123 validates the Brighter Executive Cockpit direction selected for the
second-pass workflow shell redesign.

Selected visual target:
`/Users/m1/.codex/generated_images/019ee59f-60ff-7e70-b185-7c4f0d8ae4fd/ig_0d54b7e1e357bd41016a36b333d6808191931560c229495c00.png`

## Result

Final result: passed.

## Visual Contract

- Top app bar remains the global product and project control surface, including
  project selection, refresh, and creation.
- Left workflow navigation owns Upload, Context, Template, Generate,
  Automation, and Export, uses line icons for destination recognition, and can
  collapse to a compact icon rail.
- Center workspace owns the active workflow and keeps the vivid command banner
  for recommended Generate/orchestrator actions. The page uses the full
  available desktop width so dense workflow tables benefit from wider screens.
- Right information rail owns operational status, stage progress, blockers,
  agent timeline, last run, project evidence, and report evidence; it can
  collapse to a compact status handle with a directional icon control.
- The visual system uses brighter Material 3-inspired tokens without adding a
  Material UI dependency.

## Intentional Deviations

- Material 3 is treated as design guidance for color, state, elevation, shape,
  and information architecture; implementation stays in the existing React/CSS
  stack.
- The center workspace keeps existing accessible action names so workflow E2E
  coverage and keyboard/user expectations remain stable.
- Navigation and rail collapse controls are icon-only visually, but keep
  explicit accessible labels for collapse/expand state.
- Project evidence and latest report evidence stay in compact rail sections
  rather than modal-only surfaces so audit context remains visible after reload.
- The selected mock's bright command treatment is applied to the orchestrator
  action surface, while dense review, automation, and export workbenches keep
  their existing task-focused layouts.

## Checked Surfaces

- Left workflow navigation destinations and active state.
- Workflow destination icons and left/right directional collapse affordances.
- Projects menu selection, refresh, and inline creation.
- Generate command banner, primary/secondary orchestrator actions, and blocker
  messaging.
- Right rail status overview, stage progress, blockers, agent timeline, last
  run, project evidence, and latest report evidence.
- Independent collapse/expand controls for the left workflow navigation and
  right project information rail, including persisted browser preferences.
- Responsive shell placement at 1280px and 1440px (project rail below the
  center), the 1728px three-column transition, 1920px wide-desktop columns, and
  the existing 390px mobile stack.
- Core upload, context, generation, automation, export, orchestrator lifecycle,
  and report-evidence workflows.
- Laptop, desktop, wide-desktop, and mobile CSS smoke surfaces for center width,
  clipped text, and overflow.

## Automated Coverage Map

| Surface | Coverage |
|---------|----------|
| Left workflow navigation | `frontend/e2e/workflow-navigation.spec.js` |
| Right rail status, blockers, timeline, evidence, and last run | `frontend/e2e/orchestrator-cockpit.spec.js` |
| Reload, impact update, execution, review, and report lifecycle | `frontend/e2e/orchestrator-lifecycle.spec.js` |
| Named automation execution history | `frontend/e2e/multi-environment-execution.spec.js` |
| Latest report evidence after reload | `frontend/e2e/report-evidence.spec.js` |
| Responsive shell width, placement, collapse gain, and overflow smoke | `frontend/e2e/frontend-css-smoke.spec.js` |
| Authenticated parse, generate, and export path | `frontend/e2e/workflow.spec.js` |

## Manual Visual QA

Manual screenshots from issue #122 were inspected after the brighter token pass:

- `/tmp/issue-122-desktop.png`
- `/tmp/issue-122-mobile.png`

Observed result: brighter command banner, selected left navigation state, right
rail readability, and mobile stacking showed no clipped text or incoherent
overlap.

Issue #186 manual visual checks passed at 1280px expanded, 1440px expanded,
1920px expanded and fully collapsed, and 390px mobile. The measured center
widths were 964px, 795px, 1,146px, and 1,630px respectively, with no browser
console warnings or errors. Screenshots remain local test evidence and are not
committed.
