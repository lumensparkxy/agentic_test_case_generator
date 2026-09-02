# Compact Global App Bar Design QA

- Source visual truth: `/Users/m1/Documents/Screenshot 2026-09-02 at 22.54.55.png`
- Implementation screenshots: `/tmp/issue-227-app-bar-reference-size.png`, `/tmp/issue-227-app-bar-desktop.png`, `/tmp/issue-227-account-menu.png`, `/tmp/issue-227-health-menu.png`, `/tmp/issue-227-app-bar-mobile.png`
- Combined comparison: `/tmp/issue-227-before-after.png`
- Reference viewport: 3704 × 648 CSS pixels
- Source pixels: 3704 × 648; implementation pixels: 3704 × 648
- Density normalization: implementation captured at `deviceScaleFactor: 1`; equal pixel and CSS dimensions were compared directly
- State: authenticated `multidocs` project overview at revision 7; menus closed for the full-view comparison

## Full-view comparison evidence

The combined equal-size comparison shows the former control stack forcing a roughly 320-pixel header with an unused center region. The implementation holds global navigation and all persistent controls in one 72-pixel row, then starts the two-column project workspace 16 pixels below it. Project name and revision, text-and-color health state, Settings, and account access remain visible without adding filler content.

## Focused comparison evidence

Focused 1440 × 900 captures were required because menu typography and controls are too small to judge in the ultrawide full-view comparison. The closed app bar, account menu, health popover, and expanded 390 × 844 workspace-controls panel were inspected. The account menu contains readable profile metadata plus Settings and Sign Out; the health popover preserves usage details; mobile controls retain full-width labels and do not overflow.

## Required fidelity surfaces

- Fonts and typography: existing application family, weights, line heights, truncation, and hierarchy are preserved. Compact labels remain legible and long project names ellipsize within the selector.
- Spacing and layout rhythm: desktop header measured 72 pixels at 1440 pixels wide; primary content starts exactly 16 pixels below it. Control heights are consistently 44 pixels.
- Colors and visual tokens: existing surface, border, shadow, primary tonal, muted text, and semantic green health tokens are reused.
- Image quality and asset fidelity: the existing user image remains supported; the no-image fixture uses the existing Lucide icon system rather than a custom-drawn asset.
- Copy and content: global destination labels, selected project and revision, health copy, Settings, account identity, and Sign Out are preserved. Email is available on demand instead of occupying the persistent header.

## Findings

No actionable P0, P1, or P2 mismatch remains. The implementation intentionally replaces the source screenshot's oversized stack according to the selected compact-app-bar plan. The fixture account name differs from the source only inside the open-menu evidence and does not affect layout or behavior.

## Comparison history

1. Initial implementation measured 82.375 pixels because link and project-trigger heights were content-box sized. Their height and padding exceeded the intended app-bar rhythm.
2. Navigation and control sizing was corrected to border-box geometry. Revised automated measurements pass at 72 pixels across 901, 1280, 1440, and 1920-pixel desktop widths.
3. The health disclosure initially relied on native `summary` semantics that were not exposed consistently as a named button in browser automation. It was replaced with an explicit button and popover while preserving Escape and focus restoration.
4. Revised desktop, account, health, and mobile captures show no clipping, overlap, framework overlay, document overflow, or relevant console warning/error.

## Implementation checklist

- [x] Compact single-row desktop app bar
- [x] Selected project and revision remain visible
- [x] Anchored health details popover
- [x] Icon-sized Settings control with accessible name
- [x] Account menu with profile, Settings, and Sign Out
- [x] Escape, focus restoration, and arrow-key account navigation
- [x] Existing compact workspace-controls disclosure preserved
- [x] Desktop and mobile visual checks completed

## Follow-up polish

No P3 follow-up is required for this scope.

final result: passed
