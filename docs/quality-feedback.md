# Shared test-quality goal and feedback

Every generation/review role and direct automation/critic call consumes
`application-quality-v1` from `backend/app/agents/quality_goal.py`. Its outcome is
source-grounded tests that expose application defects and prevent regressions,
with observable assertions and explicit execution prerequisites. Counts, model
self-scores and passing scripts alone are not quality evidence. Curated stage
skills reinforce the same method; project facts remain scoped approved knowledge.

## Product workflow

In **Test Cases → Improve tests**, business coverage and execution setup appear
separately. For a named missing prerequisite, open its link to review a finding
prefilled from the current saved case. Check the environment and record the
finding before applying a correction in **Context → Quality feedback**. Links
to stale or mismatched cases require returning to the current Test Cases view.

In **Context → Quality feedback**, select the evidence snapshot and record the
observed problem, expected behavior, affected case IDs, environment and missing
prerequisites. Execution previews and failed/invalid/skipped results also create
findings in the same transaction as their saved evidence. An execution failure is
initially **unknown**, not automatically an application defect. Classify it before
verification; add missing prerequisites when reviewing the finding.

For missing data, access or environment findings, configure non-secret references
and choose **Apply prerequisite correction**. This atomically creates new Context
and Test Cases snapshots. The old evidence remains immutable; corrected cases are
drafts, upstream scenarios and downstream execution/reports require review.
Business assertions are preserved. Regenerate/review affected artifacts using the
normal workflow before execution. The new suite requires a fresh substantive
assessment, and the previous assessment stays with its original snapshot.
Changed source suites reject stale corrections.
Three correction attempts are the maximum per finding.

A configured reference is not a verified resource. **Verify against latest
execution** requires a real saved execution snapshot, passing results for all
affected cases, the current non-stale suite and the same environment. Application
defect verification also requires unchanged source actions/assertions. Missing
inputs and previews cannot close findings. Source/script/ambiguous-behavior
findings require their normal review/edit workflow; prerequisite backfill cannot
rewrite expected product behavior.

**Propose lesson** creates an inactive, versioned project-knowledge proposal for
the selected stages; approval remains a separate Context action. Knowledge flags
and rollout holds still apply. Learn a reusable method, not passwords or
unverified product facts. General methodology needing a code/skill change follows
the issue-linked PR workflow; agents do not silently rewrite their system prompts.

## Prerequisite contract

Context and test cases support `execution_prerequisites`, each with `name`,
`kind`, `description`, optional `requirement_ids`, `reference`, and `status`
(`missing` or `configured`). Kinds cover data, credentials, roles, environment,
setup and cleanup. Applicable context contracts survive case hydration/refinement,
even when a model omits them. Requirement links scope future generation.

For the deterministic browser runtime, provision a dedicated environment value
such as `PETF_TEST_SUPERVISOR_LOGIN` and store only
`env:PETF_TEST_SUPERVISOR_LOGIN` as its reference. The server must also grant that reference to the authenticated owner and project
through `PETF_TEST_REFERENCE_ACCESS`, for example:

```json
[{"owner_user_id":"owner-id","project_id":"project-id","variables":["PETF_TEST_SUPERVISOR_LOGIN"]}]
```

Missing/malformed configuration, another owner/project, or a literal marker
without a matching grant stays blocked. Artifact text cannot grant access to
server environment values. Runtime checks presence and reference authorization,
not whether the account has the right permissions. Actual login/assertions establish
that evidence. The reference may be stale or wrong and the test should fail
truthfully. Role/setup/cleanup references likewise identify explicit prerequisites;
setting an environment value does not itself perform setup or cleanup.

Input actions may contain `{{supervisor_login}}` (a prerequisite name) or the
literal `[[env:PETF_TEST_SUPERVISOR_LOGIN]]` marker. The compiler retains the marker;
the generated Playwright fill reads `process.env` at execution time. Only the
`PETF_TEST_*` namespace is supported. Values are never substituted into saved
specs, IR or generated source. Fixture/context/setup/secret references are accepted
as contracts but remain blocked by this runtime until supplied through a supported
test environment binding. Generated Python automation retains its existing
interface-evidence admission; this change does not add a secret manager to it.

## API

Authenticated owner-only endpoints:

- `GET /projects/{id}/quality-findings`
- `POST /projects/{id}/quality-findings` — capture evidence and prerequisite gaps
- `POST /projects/{id}/quality-findings/{finding_id}` — `classify`, `apply`,
  `propose_lesson`, or `verify`

Writes require `X-Request-ID` and `base_project_revision`. Exact duplicate captures
reuse a finding; action retries reuse the last completed request, while stale
project revisions fail with conflict. Feedback, new snapshots and invalidation
commit together. Knowledge proposals use the existing separate idempotent approval
lifecycle. A failed knowledge-followup update can safely be retried without making
a second proposal. Feedback is project-owned and is never automatically shared
across projects.

## Evaluation before changing models

For browser cases, quote an observed URL when writing navigation or URL
expectations. A bare URL at the end of a sentence can have its prose punctuation
removed. Use `exact heading "Sign in" is visible` for an exact accessible name,
or `exact text "No results" is visible` for exact displayed text. Unqualified
visibility assertions retain their existing partial-match behavior.

For a private app, enter its **Application link** and add declarations on their
own lines in **Context notes** for any observed page or native dropdown that
the artifact collector cannot inspect.

```text
Browser page "https://booking.example.test/bookings/B-EXISTING"
Browser dropdown "Room" on "https://booking.example.test/" options ["Atlas", "Birch"]
```

Use an absolute URL on the same origin as the application link. The application
link itself is already a known page. Declare an additional page before relying
on its dropdowns. Use the exact accessible field name and a JSON list of exact,
selectable option labels. For a field-value assertion, supply the DOM value
separately using `field "Room" DOM value is "atlas"`. Conflicting declarations
retain the case for manual review. These declarations record supplied evidence
and do not cause the server to fetch the pages.

Place supplied non-secret inputs directly in steps or test data. When a case
depends on an existing user, record or setting, cite its fixture or provide a
supported setup recipe. Record unresolved dependencies as missing. Input
actions use runtime bindings only when the corresponding reference was supplied
and the runner has access to it.

The existing smoke corpus remains available. The versioned quality corpus adds
missing actor/data prerequisites, ambiguity, contradictory context, evidence past
the former truncation limits and evidence-complete automation. Expected obligations
are reviewer-only data, not injected into generation prompts. Unit regressions add
partial-run, stale review, missing usage, source-change and runtime reference cases.

```bash
.venv/bin/python scripts/evaluate_guidance.py \
  --corpus scripts/quality_fixtures/corpus.json --output /tmp/quality-contracts.json
# Run model-backed trials only under an explicitly scoped experiment:
.venv/bin/python scripts/evaluate_guidance.py --live --model MODEL_ID \
  --corpus scripts/quality_fixtures/corpus.json --repeats 3 \
  --output /tmp/quality-live.json
```

`--model` is request-scoped and propagates to worker threads. Per-call telemetry
records resolved model, prompt hash and non-secret generation settings; report
provenance includes code/SDK/goal versions. A provider-model mismatch cannot count
as a successful comparison. Unknown failed-call usage stays unknown, and partial
or timed-out outputs remain incomplete even when all requirement IDs appear.
Streaming requests are not measured by this collector; use the non-streaming
workflow or extend capture before measuring streaming. SDK-internal retry costs
may remain unknown. No report changes rollout flags.

Adjudication rehashes the actual output and verifies `run_hash` (inputs, output,
guidance, usage/configuration and report provenance). Reviews must supply both
`output_hash` and `run_hash`. For quality-corpus samples also supply
`rubric_version: application-quality-v1` and an `obligations` map keyed by every
fixture obligation ID; each judgment has `status: covered|unmet|unknown` and an
evidence-based `reason`. Coverage is computed from those judgments. Supply the
independent reviewer and adjudicated unsupported-assumption/repeated-correction
counts. Old reports lacking these bindings must not be reused as new evidence.

Preserve the audited baseline commit and compare a 2×2 matrix: current model vs
Argon, each with baseline vs improved generation/prompts. Use this corrected
measurement harness for **all four cells**; run the old generation in its isolated
checkout rather than trusting the old measurement bugs. Hold fixtures, input
versions, budgets and guidance arm constant; record model-specific settings.
Rotate ordering, repeat runs and blind artifact reviewers to model/configuration.
Report behavior coverage, unsupported assumptions, executable prerequisites,
duplicate behavior, reviewer rework, failure rate, latency and known/unknown cost.
Promote only after paired improvement without increased unsupported assumptions.
A detected application defect must retain its assertion, not be “fixed” into a
passing test.

### Expanded-context model comparison

Issue #360 compares Flash and Argon on five synthetic fixtures with three
paired repeats. Its trial arms use explicit limits. Use the repository virtual environment with the Google
Gen AI SDK version recorded in the report. The long-output trial uses SDK
continuation on non-streaming Argon case-writing calls.

```bash
.venv/bin/python scripts/evaluate_argon.py --output /tmp/argon-quality
# Live requests use the configured paid Gemini account. Pick a new directory.
.venv/bin/python scripts/evaluate_argon.py --live --output /tmp/argon-quality
.venv/bin/python scripts/review_argon.py /tmp/argon-quality --live
```

Each sample gets a 600-second process deadline including substantive review.
The experiment varies the context selection limit and the Argon writer's
output cap separately. All arms use the same 512,000-byte substantive review
limit. The expanded context limit is 128,000 characters, and the long-output
arm permits up to 256,000 tokens including reasoning. Reports record prompt
evidence IDs, provider finish reasons, continuation requests and token usage.
An output ceiling is not evidence that the model used it.

The review command sends only anonymized synthetic artifacts and the answer
key to a fixed Gemini 3.7 Flash reviewer. The command checks the delivered
schema and links, verifies cited step references, and compares source accuracy,
readability, contextual evidence, traceability and unsupported assumptions.
Run and artifact hashes bind the scored outputs. Model-based scoring is
separate from human approval or test execution. Treat invalid or failed
reviews as unscored and retain partial or absent deliveries in the report.

### Conflict-only Argon pilot

The app defaults to Gemini 3.8 Flash and selects up to 256,000 characters of
additional context for Use Cases and Test Cases. Substantive assessment accepts
up to 512,000 bytes of review input and reports larger packages as unreviewed.

Issue #362 runs three matched Flash and Argon repeats on synthetic conflicts in
authorization, receipt thresholds, and withdrawal states. This opt-in mode uses
the app's context and review limits and the standard case-writing output cap.
The archived threshold appears after 128,000 characters in its fixture.

```bash
.venv/bin/python scripts/evaluate_argon.py --conflict-pilot --output /tmp/argon-conflicts
.venv/bin/python scripts/evaluate_argon.py --conflict-pilot --live --output /tmp/argon-conflicts
.venv/bin/python scripts/review_argon.py /tmp/argon-conflicts --live
```

Substantive review reports business design coverage separately from declared
execution blockers. A design can cover every source obligation while its cases
still need provisioned accounts, data, or an environment. Missing prerequisites
remain blockers for the combined assessment and do not become requests to
regenerate an otherwise covered design. The reviewer receives a structured
response schema and can retry a malformed or incoherent response once within
its existing deadline. A covered obligation may cite a standalone action with
an empty expected result when a later cited step in the same case supplies the
observable result. Source binding and exact step validation still apply.
