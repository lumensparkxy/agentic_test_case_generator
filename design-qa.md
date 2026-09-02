# Collapsed Workflow Rail Design QA

- Source visual truth: `/Users/m1/Documents/Screenshot 2026-09-02 at 23.58.34.png`
- Implementation screenshot: `/tmp/issue-229-collapsed-after.png`
- Combined comparison: `/tmp/issue-229-design-qa-comparison.png`
- Browser and state: Codex in-app browser, authenticated local E2E account, project overview, desktop rail collapsed
- CSS viewport: 1440 × 900 at `deviceScaleFactor: 1`
- Source pixels: 234 × 1594 at approximately 2× density
- Implementation pixels: 1440 × 900 at 1× density
- Density normalization: source downsampled to 117 × 797; implementation cropped to the same 117 × 797 CSS-pixel rail region before horizontal comparison

## Full-view comparison evidence

The browser-rendered desktop view shows the global app bar, collapsed workflow
rail, project heading, contextual task, and workbench cards together. The rail
contains one primary icon for each of the seven destinations, its active state
remains clear, the workspace gains vertical breathing room, and no content is
hidden by the change.

## Focused comparison evidence

The normalized side-by-side rail comparison was required because the source is
a narrow 2× crop rather than a full viewport. The source shows a second circular
status icon below every stage icon. The implementation removes those repeated
tokens and preserves a single centered stage icon per destination. The active
Overview tile shrinks to the same one-icon rhythm instead of grouping two
control-like shapes.

## Required fidelity surfaces

- Fonts and typography: no typography changed; expanded labels and status copy retain the existing family, weights, line heights, truncation, and hierarchy.
- Spacing and layout rhythm: the collapsed rail keeps its 72-pixel column and existing padding, while each destination now occupies one compact icon row. The intentional reduction in rail height is the selected design change.
- Colors and visual tokens: existing active blue, success green, warning amber, pending neutral, borders, radii, and shadows remain mapped to the primary stage markers.
- Image quality and asset fidelity: the reference contains only standard UI icons. The implementation keeps the existing Lucide icon set and does not introduce raster, placeholder, custom SVG, or CSS-drawn assets.
- Copy and content: no user-facing destination or status copy was removed. Collapsed accessible names and native hover text still expose values such as “Requirements — Complete.”

## Findings

No actionable P0, P1, or P2 mismatch remains. The visible difference from the
source—the removal of every circled status token—is the requested design change.

## Comparison history

1. First post-change capture showed seven centered workflow icons, zero
   collapsed status tokens, and no overflow. The normalized focused comparison
   confirmed that the duplicate icon row was removed without altering the rail's
   visual language, so no corrective visual iteration was required.

## Interaction and responsive evidence

- Collapsed desktop: seven destinations, one visible SVG per destination, zero status tokens.
- Expanded desktop: seven status tokens restored and visible.
- Compact boundary at 900 pixels: navigation begins closed, opens from its named button, restores seven visible status tokens, and has no horizontal overflow.
- Accessible names retain destination plus status, and collapsed items expose matching hover text.
- Selecting the collapsed Requirements icon navigates to its canonical workbench and preserves the one-icon collapsed state; returning to Overview does the same.
- Browser console showed no runtime errors after authenticated navigation and interaction. The expected local warning about incomplete Firebase web configuration remains and does not affect the stored E2E session or this workflow.

## Implementation checklist

- [x] One visible icon per collapsed workflow destination
- [x] No separate collapsed status token
- [x] Expanded desktop status tokens preserved
- [x] Opened compact navigation status tokens preserved
- [x] Accessible destination and status names preserved
- [x] Hover status text preserved
- [x] Desktop and compact overflow checks passed

## Follow-up polish

No P3 follow-up is required for this scope.

final result: passed
