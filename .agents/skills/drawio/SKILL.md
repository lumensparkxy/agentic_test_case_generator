---
name: drawio
description: When explicitly requested, create or export a draw.io diagram. Prefer the existing diagram and renderer, and distinguish editable CLI exports from a raster-only browser fallback.
---

# Draw.io diagrams

Use the supplied diagram when the task is export-only. Create or revise native
`.drawio` XML only when requested. Preserve the input and honor the requested
format; PNG is the default when none is specified. Keep generated artifacts
outside tracked source unless the user requests a committed deliverable.

## Choose the renderer

Reuse an installed draw.io CLI when available. Look for `drawio` on PATH or the
platform's installed app, such as `/Applications/draw.io.app/Contents/MacOS/draw.io`
on macOS. Use the CLI for SVG/PDF and when editable diagram XML is required:

```bash
# Replace paths and choose png, svg, or pdf as requested.
drawio -x -f png -e -b 10 -o /tmp/diagram.png /path/to/input.drawio
```

The bundled [PNG helper](scripts/drawio-to-png.mjs) is a convenience for PNG
only; changing the output extension does not turn it into a SVG/PDF exporter.
From the repository root, with dependencies already installed:

```bash
node .agents/skills/drawio/scripts/drawio-to-png.mjs --renderer=cli /path/to/input.drawio /tmp/diagram.png
```

If a browser fallback is acceptable, use `--renderer=viewer`. It needs an
installed Chromium-compatible browser, `puppeteer-core`, and network access to
the remote draw.io viewer. It produces a raster screenshot **without embedded
XML**. Do not silently substitute it for a requested editable export. Avoid
remote viewer loading when the source or task requires an offline-only workflow.

Install helper dependencies only when missing and needed for the chosen path:
`npm --prefix .agents/skills/drawio/scripts ci`. Do not run installation or create
a new diagram merely because the skill was invoked.

## Verify the result

Check output existence, actual format, visual completeness, and embedded XML
when requested. The helper may log per-file failures without a failing process
exit; a successful exit or "Done" message is not evidence of a usable artifact.
Use `viewer` for the fallback even if the helper's usage error mentions `custom`.
Report a missing renderer or unsupported requested output honestly. Return the
verified artifact and preserve user-owned files and browser sessions.
