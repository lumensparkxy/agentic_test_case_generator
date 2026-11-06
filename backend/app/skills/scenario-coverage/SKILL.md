---
name: scenario-coverage
description: Plan source-grounded positive, negative, boundary and authorization scenarios.
metadata:
  version: "1.2.0"
  stage: use_cases
---

For each requirement, identify its actors, conditions, state transitions and observable outcome. Cover applicable happy paths, failures, boundaries, authorization and state transitions. Avoid multiplying near-identical scenarios or adding features unsupported by the sources.

Example: a 1–100 character field suggests 0, 1, 100 and 101-character cases. Authorization cases are appropriate only when a source defines roles or access rules. Map each scenario to its requirement and identify assumptions explicitly.

Review for missing behavior and duplicate objectives. Preserve canonical scenario identity and the application's output contract.


## Shared goal and feedback
Produce grounded tests that expose application defects and prevent regressions.
Preserve source behavior and assertions; a passing script or high self-score is not quality proof.
Carry applicable execution prerequisites (roles, fixture identities, configuration references,
setup and cleanup) through analysis, scenarios, cases and automation. Distinguish designed
synthetic values from provisioned records. Missing evidence remains unknown or blocked.
Separate supplied inputs from records that must already exist. Carry missing account roles,
record setup and unresolved business settings into the scenario without assigning invented values.
Use downstream findings to correct relevant upstream inputs and propose scoped, versioned
lessons. Never weaken an assertion to hide an application defect. Preserve project boundaries
and resolve secrets only at execution time. Revalidate changed artifacts before approval.
