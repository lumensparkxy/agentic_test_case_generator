---
name: requirement-quality
description: Extract atomic, observable requirements with source traceability.
metadata:
  version: "1.1.0"
  stage: requirements
---

Work from the supplied sources. Separate independent behaviors while preserving conditions and exceptions. Check ambiguous actors, missing thresholds, conflicting rules and duplicate requirements. Flag missing information rather than inventing it.

Example: if a source specifies a title length of 1–100 characters, retain both bounds in the requirement. Do not infer account registration, notifications or an approval workflow from a task-creation requirement.

Review for observable outcomes, consistent domain terminology and faithful source references. Preserve the application's output schema and explicit approval policy.


## Shared goal and feedback
Produce grounded tests that expose application defects and prevent regressions.
Preserve source behavior and assertions; a passing script or high self-score is not quality proof.
Carry applicable execution prerequisites (roles, fixture identities, configuration references,
setup and cleanup) through analysis, scenarios, cases and automation. Distinguish designed
synthetic values from provisioned records. Missing evidence remains unknown or blocked.
Use downstream findings to correct relevant upstream inputs and propose scoped, versioned
lessons. Never weaken an assertion to hide an application defect. Preserve project boundaries
and resolve secrets only at execution time. Revalidate changed artifacts before approval.
