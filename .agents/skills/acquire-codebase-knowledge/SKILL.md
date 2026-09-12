---
name: acquire-codebase-knowledge
description: Map or document a codebase when explicitly requested, including a named architecture area. Focused requests inspect only that area; routine feature work and narrow fixes do not require repository-wide discovery.
license: MIT
metadata:
  version: "1.4"
---

# Acquire codebase knowledge

Choose scope from the user's request before scanning or writing. An explanation
request returns an explanation; create or edit documentation only when requested.
Treat existing docs as navigation aids and verify claims against relevant source.

## Focused discovery

For a named area such as architecture, testing, or one workflow:

1. Read the relevant existing document and locate the corresponding entrypoints,
   configuration, callers, and tests using targeted searches.
2. Follow dependencies only as needed to explain that area. Do not run a full
   scan or read every intent/architecture document as a prerequisite.
3. When documentation is requested, update only the requested document/sections.
   Leave other documents intact; do not create placeholder versions of them.
4. Verify changed claims and links against source. State unresolved facts or
   material intent questions without requiring answers to unrelated questions.

## Full mapping

Only an explicit full-codebase mapping request uses the seven-document workflow:
`STACK.md`, `STRUCTURE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`, `INTEGRATIONS.md`,
`TESTING.md`, and `CONCERNS.md` under `docs/codebase/`.

Use the existing repository scan (`python scripts/scan_codebase.py` from the
root). For another repository without that command, the bundled
[scripts/scan.py](scripts/scan.py) supports `--help` and `--output`. Exclude
ignored/generated files, environments, dependencies, and credentials. Read the
relevant intent documents and compare stated design with observed code.

Use [inquiry checkpoints](references/inquiry-checkpoints.md) for the documents
being produced. Read [stack detection](references/stack-detection.md) only when
manifests leave the technology ambiguous. Templates are starting points:

- [Stack](assets/templates/STACK.md) and [structure](assets/templates/STRUCTURE.md).
- [Architecture](assets/templates/ARCHITECTURE.md) and [conventions](assets/templates/CONVENTIONS.md).
- [Integrations](assets/templates/INTEGRATIONS.md), [testing](assets/templates/TESTING.md),
  and [concerns](assets/templates/CONCERNS.md).

Keep verified useful existing material. Include source evidence for non-trivial
claims and clearly mark unknowns. Validate the documents within the requested
scope; do not impose a seven-document completion gate on focused work. Follow
[AGENTS.md](../../../AGENTS.md) for work records and delivery.
