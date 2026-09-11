---
name: grounded-playwright
description: Generate isolated Playwright tests with grounded selectors and assertions.
metadata:
  version: "1.0.0"
  stage: automation
---

Use selectors and navigation targets supported by supplied execution context. Prefer accessible roles/names or stable test identifiers when grounded. Keep tests independent; use web-first assertions and existing runtime fixtures. Do not substitute fixed sleeps for readiness checks or silently suppress failed assertions.

Example: use a supplied accessible button name to locate an action, then assert its specified observable outcome. If a selector, credential or environment prerequisite is unavailable, report the limitation instead of fabricating it.

Follow existing execution restrictions and artifact paths. Never embed secrets, disable security controls or change explicit execution/approval gates.
