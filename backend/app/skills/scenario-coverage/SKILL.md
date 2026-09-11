---
name: scenario-coverage
description: Plan source-grounded positive, negative, boundary and authorization scenarios.
metadata:
  version: "1.0.0"
  stage: use_cases
---

For each requirement, identify its actors, conditions, state transitions and observable outcome. Cover applicable happy paths, failures, boundaries, authorization and state transitions. Avoid multiplying near-identical scenarios or adding features unsupported by the sources.

Example: a 1–100 character field suggests 0, 1, 100 and 101-character cases. Authorization cases are appropriate only when a source defines roles or access rules. Map each scenario to its requirement and identify assumptions explicitly.

Review for missing behavior and duplicate objectives. Preserve canonical scenario identity and the application's output contract.
