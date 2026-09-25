# Unfinished generation and targeted repair

A test case must contain product actions and observable expected results. Internal
instructions such as “review the implemented behavior” or “execute the scenario”
are unfinished generation work, even when they have a test-case ID.

Generation responses expose `generation_tasks` separately from `test_cases`.
Each task identifies its requirement IDs, scenario references, reason, and an
optional source test-case ID. Counts and coverage use delivered cases. Missing
scenarios and known placeholder content prevent approval. Deterministic coverage
augmentation no longer fills a model output with generic cases. Model-backed
retry remains available; fallback output cannot approve itself.

Existing snapshots are immutable. Current project and workspace views withhold
known legacy placeholder content and expose repair tasks. Detection uses content,
not an ID prefix: a real test named `TC-FB-001` remains a real test. Exports reject
placeholder content even with a draft override. This is a conservative guard for
known filler patterns and missing steps, not a guarantee of semantic test quality.

## Final delivery contract

Generated case-level and step-level `test_data` remains a string or null in the
public API and exports. At the model-output boundary, JSON objects and lists are
converted to deterministic compact JSON text (sorted string keys, preserved
Unicode and array order). Decoding that text recovers the original data; existing
strings and null are unchanged. Structured conversion is limited to 16,000 output
characters, 2,048 value/container nodes and nesting depth 12, with the root at
depth zero. Non-finite numbers, non-JSON values, non-string object keys and
oversized structures are rejected, never truncated or converted with Python repr.

Every case rejected at final contract validation becomes an explicit
`generation_tasks` entry with its available case, requirement and scenario
references and a field-level reason. Diagnostics include no rejected test-data
values. Valid siblings remain delivered. Counts, coverage and provenance totals
are recomputed from the final deliverable set; even a redundant rejected case
blocks suite approval until resolved. Existing project replacement guards reject
incomplete updates before writing artifact versions or snapshots. Persistence
identifiers are excluded from model serialization and ignored in model output;
the existing versioning service matches refined cases to the previous suite by
case ID and assigns the artifact metadata.

Generation and refinement prompts require a description and the canonical
case/step fields even when a display template selects fewer columns. The public
schema still accepts legacy optional descriptions; this change does not migrate
saved artifacts or enable held guidance stages. Matched live quality evidence
and the separate rollout decision remain necessary under #280/#274.

## Repair a suite

1. Review and approve current requirements and the affected Use Cases.
2. In Test Cases, choose **Plan targeted repair**.
3. Select the Add/Update recommendations to accept, then apply them.
4. Review the resulting changed tests. Repair does not grant suite approval.

Analysis does not call the model. Apply generates only the selected requirement
and scenario slices, using saved context and the existing bounded generation
pipeline. At most 40 generation recommendations can be selected per request;
duplicate updates/additions are consolidated. Each distinct recommendation is
generated in sequence, with bounded parallel shards inside that generation run.

The apply API requires `base_project_revision`. Ownership, active status, input
snapshot freshness, requirement approval, and snapshot-bound scenario reviews
are checked before model generation. Rejected, incomplete, fallback, or foreign
coverage cannot be committed. The original suite and history remain intact on
failure. Changed artifact versions and the new project snapshot commit in one
revision-guarded transaction. Unchanged test identities and versions are retained.
Generation evidence, guidance references, usage, and billing consumption follow
the targeted run. Repaired tests are saved for review, without a fabricated pass
score. Old invalid snapshot content is retained as historical evidence.

## Validation boundary

The offline generation benchmark checks safe fallback handling: withholding
placeholders, reporting uncovered scenarios, blocking approval, and truthful
counts. Its model-backed quality thresholds remain unchanged. Offline and mocked
browser checks do not establish live model quality or production repair success.
The separate structured-data/guidance rollout work in issue #280 remains held.

## Substantive business coverage

Generation and refinement assess the exact hydrated, delivered cases independently
of the generation score. `substantive_assessment` records the input hash, rubric,
source obligations, exact source quotations and step citations, and required or
optional prerequisites. The Improve tests view exposes this evidence after reload.
Requirement/scenario reference ratios remain separate from behavioral coverage.

`assessed_complete` means a model assessed the test design against the supplied
requirements, context and actual run guidance. It does not establish execution,
human acceptance or exhaustive correctness. Citation validation binds a judgment
to its evidence; it cannot prove that a model enumerated every semantic obligation.
Measured retry false positives also have deterministic guards: duplicate-submission
requirements with refresh need explicit durable count and same-ID checks in the
triggering case, and unprovided idempotency/debounce or response-loss mechanisms
are prerequisites. These guards can reject a model pass and still run when the
model review is unavailable; `model_status` keeps that distinction inspectable.
They are conservative checks of explicit wording, not general semantic proof.
Current requirements outrank conflicting context/memory; curated methods are not
product facts. Valid boundaries and each stated invalid example must be assessed
separately. A trigger and its durable result must belong to the same coherent case.

The final review has one request, no HTTP retries, a 60-second HTTP timeout,
160,000-byte input limit and 24,000-token output limit. Missing credentials,
empty output, oversized inputs, malformed evidence or provider failure preserve
cases but leave coverage `unknown`, unapproved and explicitly needing review.
This adds a bounded model call to generation/refinement; it does not enable held
guidance. The review never upgrades another failed quality or completeness gate.

Actionable unmet obligations and required assumptions feed `generation_tasks`
and the existing impact/targeted-repair workflow. Findings for the same case and
requirement are grouped into one recommendation. Only accepted findings are
retired; unselected findings and unchanged case identities remain. A failed
replacement preserves the baseline. Successful repair leaves the combined suite
unapproved and its assessment unknown until reviewed again. No second repair
engine, automatic human approval or new execution path is introduced.

Source route placeholders such as `/bookings/{bookingId}` remain literal text.
Source/feedback blocks are inserted after ADK expands its own workflow state,
so product braces cannot request internal state or artifacts.
