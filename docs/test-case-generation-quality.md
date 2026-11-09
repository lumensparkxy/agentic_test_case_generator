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

## Browser handoff

Supply the application URL and the exact interface evidence available for the
scenario. Context analysis reads explicit form labels in linked HTML, including
`<label for="…">`, simple labels that wrap an input, and `aria-label`. Field IDs,
names and placeholders stay as hints when a label is unavailable. Supply observed
names or result text from rendered states in context notes, such as
`field "Reference"`, `button "Save request"`, and `"Request saved"`.

Generation and refinement use one browser action and at most one observable
check per step. Complete browser actions may leave their expected result
empty when the case has a separate observable outcome. Use
`Click exact button "Save request"` and
`Click exact link "View receipt"` for whole accessible names. A case for an
initially blank field checks the blank value before submitting.

For a grounded native single-choice dropdown, use
`Select exact option "Closed" in field "Order status"`. Linked HTML supplies
the selectable labels. A separate field-value check needs an explicit
declaration such as `field "Order status" DOM value is "closed_internal"`
in the supplied context. The browser verifies one available exact option
before selecting it. Cases with unsupported or unavailable choices stay
manual.

For a grounded native checkbox, use `Check exact checkbox "Email receipt"`
or `Uncheck exact checkbox "Email receipt"`. Use `exact checkbox "Email
receipt" is checked` or `exact checkbox "Email receipt" is not checked` for
the state expectation. These expectations describe definite states. Keep any
save or persistence outcome as its own check. State-setting requires an
available checkbox with the supplied label.

For an out-of-view state, use an expectation such as
`exact link "Open receipt" is not visible`. The browser waits for zero visible
exact matches at that step. Hidden and absent matches both satisfy the check.
Record DOM absence, authorization, and timing as distinct requirements.

New browser-intended cases go through the deterministic preview and compiler
without launching the app. Verified cases are marked **To Be Automated**.
Cases with missing evidence, prerequisites, or unsupported steps keep their
actions, expectations, and traceability and appear as **Manual**. **Improve
tests** shows the reasons recorded for the latest generation or refinement.
External outcomes, such as checks in a separate audit store, remain manual.

Business approval and execution are separate. Review the delivered cases, then
preview eligible cases against the chosen environment before running them.

## Repair a suite

1. Review and approve current requirements and the affected Use Cases.
2. In Test Cases, choose **Plan targeted repair**.
3. Select the Add/Update recommendations to accept, then apply them.
4. Review the resulting changed tests. Repair does not grant suite approval.

Analysis does not call the model. Apply generates only the selected requirement
and scenario slices, using saved context and the existing bounded generation
pipeline. At most 40 generation recommendations can be selected per request;
duplicate updates/additions are consolidated. Multiple independent replacements
run with up to three workers, capped by `PARALLEL_TEST_CASE_MAX_WORKERS`.
`PARALLEL_TEST_CASE_GENERATION_ENABLED=false` keeps replacements sequential.
In a parallel repair batch each replacement runs its inner shards one at a time,
so nested fan-out stays within the batch limit. A single replacement keeps
the ordinary shard configuration. Results are assembled in the saved
recommendation order.

The apply API requires `base_project_revision`. Ownership, active status, input
snapshot freshness, requirement approval, and snapshot-bound scenario reviews
are checked before model generation. Rejected, incomplete, fallback, or foreign
coverage cannot be committed. The original suite and history remain intact on
failure. A failed worker stops further submissions and allows already-running
work to finish before releasing the application lease. Changed artifact versions
and the new project snapshot commit in one revision-guarded transaction.
Unchanged test identities and versions are retained. Generation evidence binds
each result to its recommendation, source case, scenario scope and project
revision. The snapshot records the worker count and batch duration.
Usage and billing consumption follow the committed targeted run. Repaired tests
are saved for review, without a fabricated pass
score. Old invalid snapshot content is retained as historical evidence.

**Retry** on a failed or interrupted apply reuses replacements that already
passed individual review. The saved result must match the same analysis,
selection, source revision, generation input, model settings and current
guidance. Missing or invalid results go through generation and review again.
The completed draft records how many replacements were reused and retains their
original generation evidence. It still needs combined-suite review.

If a provider outage was the sole blocker after complete generation, use
**Review saved suite** in Test Cases. Review retries the saved cases with the
same approved Requirements, Use Cases and generation Context. It writes a
review successor with the original case versions and generation evidence.
Changed inputs require impact analysis. A repeated outage leaves the saved
suite in place, while verified gaps are saved for repair.

Recovery keeps one private Firestore record per project, capped at 512 KiB of
checkpoint data. A record is eligible for reuse for 24 hours after its latest
checkpoint. A successful apply deletes it in the draft transaction. A subsequent
apply discards a record from an expired or different operation. Results outside
the storage bound can still be applied and may need to be generated again after
an interruption.

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

When the source says a rejection must create no record, assert the error and the
fresh saved state in the triggering case. A browser Context declaration can bind
the exact error, submit control, collection and loaded indicator on a supplied
page. Review checks the compiled steps for the same count after the collection
loads before submission and after reopening. A missing check stays in review
with a repair finding for the affected case.

```text
Browser rejection "Invalid input" on "https://app.example/" after button "Save" preserves css "#records tr" when exact heading "My records" is visible
```

The independent review allows up to two requests within a 180-second budget,
with at most 120 seconds per request and no SDK retries. Each prompt, including
review instructions and any correction, must fit within 512,000 UTF-8 bytes.
The output limit is 24,000 tokens. The prompt keeps every case and its behavioral
evidence, omitting duplicate Context requirements, redundant guidance metadata,
null fields and generation bookkeeping. Citations and regression checks use the
full input and its hash.

Whole-suite reviews of 41 or more cases can use up to eight groups with three
concurrent workers and a shared 240-second budget. Each group can make two
requests of up to 90 seconds and 192,000 UTF-8 bytes. Cases that link multiple
requirements keep them in the same group. Reviewers see the full source and
shared Context, assess their assigned requirements and cases, and return
evidence bound to that group's input. The combined assessment keeps the full
suite hash and requires every group to verify its coverage before approval.
Grouped prompts can share identical step bodies while preserving each case's
step numbers and full evidence for citation checks.
Requirements derived from the same verified source version and original ID
can cite related cases across groups. Exact duplicate reference cases share
one representative, while each original case is grounded in its own group.
A large linked scope that exceeds the ordinary group limits can use a source
obligation inventory followed by bounded case batches. The inventory gets up to
60 seconds inside the same 240-second budget. Each batch assesses the exact
indexed obligations for its linked scope and grounds its assigned cases. Cases
linked to the same verified source can supply proof in separate batches. The
combined assessment uses a coherent case for each covered obligation. The
reviewer reports an indexed coverage gap after all its assigned batches finish
without verified proof. If batching cannot start, the bounded whole-suite
reviewer still applies. If the indexed content exceeds the batch limits,
review reports an input limit. Selected-repair review keeps its existing
scope and limits.

Completed groups retain verified findings. If any group remains unverified,
approval stays blocked. A saved-suite review with verified gaps can record the
findings on a new unapproved snapshot. If the service is unavailable and no
group confirmed a gap, the original snapshot stays current for retry.
The Test Cases workbench shows group status separately from test repair
findings and opens the exact cases assigned to a group. A failed review
attempt can show its groups while the current suite is open. Saved group
evidence remains available after reload.

Retrying an interrupted **Review saved suite** reuses completed groups from
the same project, saved suite, revision, reviewer model and effective prompts.
The service revalidates each group's source and step citations before combining
the results. Missing or invalid groups go to the reviewer again. Progress stays
in one private Firestore record per project, capped at 768 KiB and eligible for
24 hours. The next grouped review replaces expired or incompatible progress.
Publishing the reviewed snapshot removes its progress in the same transaction.
Groups beyond the storage cap can still complete in the current attempt.
For a batched review, retry also revalidates and reuses the source inventory.
Generation against an approved Use Cases snapshot can save that same private
progress with an eligible outage-only draft. The draft and progress commit
together. The first saved review can reuse it when the current inputs, reviewer
and effective guidance match the original review.

Project generation against approved Use Cases commits the Test Cases snapshot,
case versions, completed usage, applicable billing and review progress together.
The commit checks the owning project and the active revision. If delivery is
interrupted, the workbench retries the exact payload and request ID once. A
matching receipt for the current suite returns its saved cases and versions.
An ID used for different inputs or an earlier suite returns a conflict.

Missing credentials, empty output, oversized inputs, malformed evidence or
provider failure preserve cases but leave coverage `unknown`, unapproved and
explicitly needing review. Saved-suite review reports an input limit separately
from a provider outage.
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
