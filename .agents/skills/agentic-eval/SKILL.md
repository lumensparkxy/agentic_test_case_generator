---
name: agentic-eval
description: Design or change agent evaluation rubrics, quality benchmarks, judge logic, or bounded refinement loops. Do not invoke for routine tests, code review, or every task that produces an agent output.
---

# Agent evaluation work

Identify the quality question, existing output contract, baseline fixtures, and
acceptance threshold. Reuse the repository's evaluation harness before adding a
new evaluator or model-based judge. Measure the delivered artifact and truthful
failure/fallback outcomes, not just whether a model call returned successfully.

Use deterministic checks for schema, counts, syntax, and known regressions. Add
model-based scoring or refinement only when required by the task and supported
by a rubric. Bound retries and cost; keep a reproducible baseline, and distinguish
quality gains from changes in fixtures, provider, or fallback behavior.

Read only a needed section of [optional patterns](references/patterns.md) for
reflection, evaluator-optimizer loops, code evaluation, or rubric examples.
These examples do not require extra critique/refinement passes for ordinary work.

Follow [AGENTS.md](../../../AGENTS.md) for work records and
[Testing Patterns](../../../docs/codebase/TESTING.md) for affected tests and
strict offline benchmarks. Live model experiments require scope and authorization
covering them; report when validation is offline-only.
