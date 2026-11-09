# Use Cases quality assessment

Use Cases retain three separate decisions:

- **Structural checks** inspect requirement groups, scenario categories and
  identifiers. The existing `score` and coverage ratios describe this structure
  and linked counts; they are not a percentage of behavior verified.
  Every requirement needs a nonempty group and at least one must-have scenario;
  categories must be allowed, and identifiers/requirement links valid. There is
  no mandatory Happy Path/non-happy-path pair or scenario-count quota. A single
  source behavior may be represented by one scenario in its appropriate category.
- **Semantic assessment** reviews each full title and objective for a concrete
  trigger, observable outcome, distinct behavior across the plan, and
  grounding in supplied requirements and Context. It also checks the plan for
  evidence at explicit inclusive input boundaries. Results are advisory.
- **Human review** remains an independent decision for the exact saved snapshot.
  Generation always creates a new unapproved snapshot. Existing revision,
  stale-input and idempotency guards still govern human decisions.

## Generation and limits

One semantic critic assesses the delivered normalized plan. A single full
shard runs it after planning. Multiple shards and scoped refresh run the critic
once on the combined plan. Planning uses at most three concurrent shards.
The critic receives the source requirements and supplied Context and works
within the existing timeout and retry budget.

The planner covers distinct source behaviors and input partitions and checks
generated analysis against source facts. For an explicitly inclusive numeric
input range, it plans separate must-have scenarios at the exact accepted
endpoints alongside the stated invalid partitions. If a companion requirement
defines the same input's successful outcome, it can own the positive scenarios.

The coverage check recognizes direct affirmative numeric interval clauses such
as “must be between 1 and 8 inclusive” and “accepts from -2.5 to 3.5
(inclusive).” It uses the saved requirement text. The critic cites the exact
input and acceptance outcome from a required scenario that passes its individual
review. An error that quotes the range or a generic successful input cannot
earn endpoint coverage. Missing verified inputs appear above the scenario table
with the owning requirement ID and exact interval. The human decision remains
independent.

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
`review.semantic_assessment` records rubric version `scenario_semantics_v4`,
status (`passed`, `needs_review`, `unavailable`), assessed/total/flagged counts,
per-scenario checks, reasons, warnings and verified boundary evidence, and `requirement_findings` for
unverified boundary inputs. A SHA-256 binding covers the complete normalized
plan, source requirements and supplied Context. Missing, ambiguous,
malformed or mismatched critic records cannot pass. Known warnings remain
visible even when the model assessment is unavailable. No semantic coverage
percentage is inferred from category counts.
Earlier assessments remain as recorded with their original rubric version.

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
