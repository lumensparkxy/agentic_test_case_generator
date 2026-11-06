---
name: grounded-playwright
description: Generate isolated Playwright tests with grounded selectors and assertions.
metadata:
  version: "1.1.0"
  stage: automation
---

Use selectors and navigation targets supported by supplied execution context. Prefer accessible roles/names or stable test identifiers when grounded. Keep tests independent; use web-first assertions and existing runtime fixtures. Do not substitute fixed sleeps for readiness checks or silently suppress failed assertions.

Example: use a supplied accessible button name to locate an action, then assert its specified observable outcome. If a selector, credential or environment prerequisite is unavailable, report the limitation instead of fabricating it.

Follow existing execution restrictions and artifact paths. Never embed secrets, disable security controls or change explicit execution/approval gates.


## Shared goal and feedback
Produce grounded tests that expose application defects and prevent regressions.
Preserve source behavior and assertions; a passing script or high self-score is not quality proof.
Carry applicable execution prerequisites (roles, fixture identities, configuration references,
setup and cleanup) through analysis, scenarios, cases and automation. Distinguish designed
synthetic values from provisioned records. Missing evidence remains unknown or blocked.
Use downstream findings to correct relevant upstream inputs and propose scoped, versioned
lessons. Never weaken an assertion to hide an application defect. Preserve project boundaries
and resolve secrets only at execution time. Revalidate changed artifacts before approval.
