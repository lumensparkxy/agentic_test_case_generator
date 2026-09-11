---
name: execution-ready-tests
description: Write concrete traceable test steps, data and expected outcomes.
metadata:
  version: "1.0.0"
  stage: test_cases
---

Implement the supplied scenarios with explicit actors, preconditions and realistic test data. Each action must have an observable expected result. Reuse requirement and scenario identifiers; separate setup from behavior under test. Include applicable boundaries and negative conditions without inventing UI or API details.

Example: for an expired reset link, state that the token is already expired, open that link, and assert the specified expiration response. Do not use "verify it works" or fabricate an error string the sources do not provide.

Review for executable steps, independent cases, unsupported assumptions and requirement coverage. Keep human review gates and output schemas unchanged.
