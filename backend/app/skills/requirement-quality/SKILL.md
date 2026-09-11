---
name: requirement-quality
description: Extract atomic, observable requirements with source traceability.
metadata:
  version: "1.0.0"
  stage: requirements
---

Work from the supplied sources. Separate independent behaviors while preserving conditions and exceptions. Check ambiguous actors, missing thresholds, conflicting rules and duplicate requirements. Flag missing information rather than inventing it.

Example: if a source specifies a title length of 1–100 characters, retain both bounds in the requirement. Do not infer account registration, notifications or an approval workflow from a task-creation requirement.

Review for observable outcomes, consistent domain terminology and faithful source references. Preserve the application's output schema and explicit approval policy.
