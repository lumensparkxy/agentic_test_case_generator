---
name: execution-ready-tests
description: Write concrete traceable test steps, data and expected outcomes.
metadata:
  version: "1.1.0"
  stage: test_cases
---

Implement the supplied scenarios with explicit actors, preconditions and realistic test data. Each action must have an observable expected result. Reuse requirement and scenario identifiers; separate setup from behavior under test. Include applicable boundaries and negative conditions without inventing UI or API details.

Example: for an expired reset link, state that the token is already expired, open that link, and assert the specified expiration response. Do not use "verify it works" or fabricate an error string the sources do not provide.

Review for executable steps, independent cases, unsupported assumptions and requirement coverage. Keep human review gates and output schemas unchanged.


## Shared goal and feedback
Produce grounded tests that expose application defects and prevent regressions.
Preserve source behavior and assertions; a passing script or high self-score is not quality proof.
Carry applicable execution prerequisites (roles, fixture identities, configuration references,
setup and cleanup) through analysis, scenarios, cases and automation. Distinguish designed
synthetic values from provisioned records. Missing evidence remains unknown or blocked.
Use downstream findings to correct relevant upstream inputs and propose scoped, versioned
lessons. Never weaken an assertion to hide an application defect. Preserve project boundaries
and resolve secrets only at execution time. Revalidate changed artifacts before approval.
