# Use Cases quality assessment

Use Cases retain three separate decisions:

- **Structural checks** inspect requirement groups, scenario categories and
  identifiers. The existing `score` and coverage ratios describe this structure
  and linked counts; they are not a percentage of behavior verified.
  Every requirement needs a nonempty group and at least one must-have scenario;
  categories must be allowed, and identifiers/requirement links valid. There is
  no mandatory Happy Path/non-happy-path pair or scenario-count quota. A single
  source behavior may be represented by one scenario in its appropriate category.
- **Semantic assessment** reviews the full title and objective for a concrete
  trigger, observable outcome, distinct behavior within the requirement, and
  grounding in supplied requirements/context. Results are advisory findings,
  not proof that a scenario or product behavior is correct.
- **Human review** remains an independent decision for the exact saved snapshot.
  Generation always creates a new unapproved snapshot. Existing revision,
  stale-input and idempotency guards still govern human decisions.

## Generation and limits

One semantic critic follows the existing analysis and coverage planner in each
Use Cases shard (at most three concurrent shards). It assesses the normalized
plan, including fallback when model output is missing, within the existing shared shard
wall-clock timeout and provider retry budget. It has no tools or repair loop.
The critic receives the shard's source requirements and supplied context, not
model-generated requirement analysis as a source of new facts. A business rule
available only in a different shard can therefore require human reconciliation.

The planner covers distinct source behaviors and applicable input partitions,
checks generated analysis against source facts, and avoids repeating the same
trigger/outcome under different categories. It must not invent states or
implementation mechanisms to fill a plan. These instructions and structural
checks do not prove source coverage or semantic correctness: the independent
critic and explicit reviewer decision remain necessary, and no model-authored
scenario is silently deleted after generation to force a passing verdict.

Known local checks flag category scaffolding, identical scenario content, and
unsupplied route/debouncing prescriptions. They do not award a semantic pass or
reject a scenario solely because its title is short or names a category. The
model reviewer handles semantic distinctions beyond these bounded patterns.

The local route check compares case-sensitive complete paths. A supplied route
template with whole-segment `{parameter}` placeholders also supports concrete
paths when fixed segments and path length match and every substituted value is
present as a complete token in source facts outside route strings. This avoids
flagging `/bookings/B-EXISTING` when both `/bookings/{bookingId}` and fixture
`B-EXISTING` are supplied. It does not infer new routes, fixture values, parameter
meaning, actor permissions or expected behavior. The independent critic still
reviews those semantics; absence of a local warning never grants approval.

`review.structural_checks` retains the structural result.
`review.semantic_assessment` records rubric version `scenario_semantics_v2`,
status (`passed`, `needs_review`, `unavailable`), assessed/total/flagged counts,
and per-scenario checks, reasons and warnings. A SHA-256 binding covers the
normalized scenario, requirement text and supplied context. Missing, ambiguous,
malformed or mismatched critic records cannot pass. Known warnings remain
visible even when the model assessment is unavailable. No semantic coverage
percentage is inferred from category counts.
Version 2 corrects route-template instances and rejects substring/case-only
route matches. Existing version 1 assessments are retained as recorded; this
change does not rewrite snapshots or human review decisions.

The legacy top-level score remains structural for compatibility; top-level
machine `approved` now requires both structural and semantic success. It does
not grant a human approval. Other generation stages retain their existing review
behavior; the two additional ReviewResult fields are optional.

If only the critic fails, valid planning output can be saved for human review
with unavailable semantic assessment. A critic-only timeout retains the timeout
diagnostic and its `semantic_review_timeout` reason. Planning timeouts, malformed
plans and fallback generation still preserve the previous saved snapshot under
the existing generation guard. There is no automatic retry/repair of a semantic
failure. Regenerate or use the human review workflow after examining findings.

Existing snapshots have no semantic assessment and display **Unavailable**.
They are not migrated or treated as semantically passed. Their existing human
decisions retain their exact-snapshot meaning.

## Verification boundaries

Regression tests cover category scaffolding (including embedded requirement
text), concise valid content, duplicates, unsupported prescriptions, strict
critic output, exact-content binding, missing assessments, critic failures,
partial results and independent human review. Browser tests cover mixed
structural/semantic results, legacy artifacts and desktop/mobile accessibility.
Offline tests cannot establish model quality. A live synthetic replay can check
provider compatibility and the rubric on those examples; it does not estimate a
general error rate or establish business acceptance.
