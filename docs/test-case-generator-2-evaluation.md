# Test Case Generator 2.0 evaluation protocol

Protocol `tcg-2.0-paired-v1` was fixed before 2.0 candidate measurement. The baseline is `2f6458d3d1bb68197ba2b5572261870277c8cadb`, tree `a566f1cf94219e7e924db8fc54e516eb1d81f3a8`. Baseline and candidate use the same sources, test intent, target implementation, browser and independent scoring and eligibility rules. Record any changes in actual app admission. Retain `gemini-3.8-flash` and the baseline context and reviewer limits of 256,000 characters and 512,000 bytes. Record the effective settings, guidance manifest and provider model on each stage. Keep held guidance stages disabled. Freeze any lower experiment limits identically for both arms before authorization, record them, and reject drift.

## Corpus and trial order

Reproduce the freeze using the baseline checkout's working virtual environment and Python modules. The [hash manifest](test-case-generator-2-corpus-manifest.json) pins each frozen input. The replay helper and generated synthetic inputs are retained with the P0 evidence outside Git. Set `TCG_V2_EVIDENCE` to that directory before replay.

```sh
.venv/bin/python "$TCG_V2_EVIDENCE/p0/evaluation/freeze_corpus.py" "$PWD" /tmp/tcg-v2-eval/frozen
cmp /tmp/tcg-v2-eval/frozen/manifest.json "$TCG_V2_EVIDENCE/p0/evaluation/frozen/manifest.json"
```

| Workflow | Source and purpose | Split |
| --- | --- | --- |
| W01 | Room Booking 1–8, no-record rejection, availability and cancellation | Calibration |
| W02 | Existing authentication/profile/search fixture with lockout and unprovided account state | Calibration |
| W03 | Existing expense workflow with role, state and configured threshold rules | Calibration |
| W04 | Existing conflicting-threshold fixture with archived 250 EUR contradicting 173 EUR | Calibration |
| W05 | Existing missing Supervisor access and ambiguous threshold fixtures combined. R2 is explicitly deferred | Calibration |
| W06 | Existing long-context over-budget expense fixture. Preserve late facts without treating archive decoys as product behavior | Calibration |
| W07 | Existing controlled payload editor. Preserve multiline whitespace, Unicode and malformed input | Held out |
| W08 | Room Booking repeat submission and response loss. Compare original ID and persisted count with manual prerequisites | Calibration |
| W09 | Newly authored invoice attachment handoff across source clauses. Verify literal Unicode filename and denied mutation | Held out |
| W10 | Existing receipt fixture's external audit requirement in isolation. Keep the useful design manual | Held out |
| W11 | Existing Room Booking reimport from 8 to 6. Retain unchanged cases, reject stale delivery, renew affected approvals | Calibration |
| W12 | Newly authored 42-requirement archive workflow. Exercise multiple roles and stages, shared state and large reviews | Held out |

Use W01 and W05 for the two-workflow smoke, one matched pair per workflow. Full comparison is 36 matched pairs across 12 workflows with three repeats each. Read the exact fixture and arm sequence from `pair-order.json`, randomized with seed 20261109. Each arm gets an independent disposable project, account scope and target state. Smoke observations do not replace any planned full comparison. Keep W07, W09, W10 and W12 out of candidate tuning. Reused held-out fixtures are publicly inspectable engineering holdouts. Reserve their candidate outcomes until the candidate is frozen.

The runner admits only requirements, source documents, context and template as product input. Resolve healthy-target aliases locally and give only the healthy observed interface evidence to the app. Author control bodies, fault definitions, adjudication keys, pairing and arm metadata remain outside request payloads. Do not expose raw fixture HTML scripts or broken variant URLs as context. Keep the reviewer sidecar and held-out inputs outside the generation working directory while tuning. Use the hash-only manifest to verify the selected files before a run.

## Artifact evidence and scoring

Use the isolated Firestore emulator, loopback frontend/API, and synthetic targets supported by `scripts/e2e_workbench_local.py`. W11 uses the existing `room-booking-change` fixture and compares both source versions. The runner must replay actual approval and persistence routes for all workflows. Existing `evaluate_guidance.py` and `evaluate_browser_handoff.py` establish direct-agent screening and controlled execution. Extend the workbench path or add a thin adapter for the remaining corpus. A direct-agent result cannot satisfy saved/exported acceptance.

### Rehearse project inputs before the live comparison

`scripts/rehearse_paired_inputs.py` opens only the hash-pinned generation
manifests. It uploads actual Markdown to the pinned app and supplies the
frozen author rows at the extraction seam. Project creation, source binding,
saved comparison, application, approval, Context, and W11 requirement
history use the actual application routes and Firestore. Named candidate
approvals queue and cancel the exact continuation. No worker is started.

Use a dedicated anonymous loopback emulator for each pinned app. The
baseline uses `demo-tcg-workbench` and the candidate uses
`demo-tcg-v2-continuation`. Supply the pinned app's `backend` on
`PYTHONPATH`, disable dotenv loading and held guidance, use local billing
shadow mode, and keep provider credentials out of the process. The
rehearsal launcher in the P6 evidence supplies these settings and restricts
network access. The CLI takes the frozen directory, arm, full commit and
new output path. Its output is private input rehearsal evidence.

The rehearsal leaves synthetic target placeholders intact and exercises
source and scope only. The live runner resolves them to the matching
healthy target, uses the frozen template where supplied, and captures the
actual scenario, suite, export and execution evidence separately. For
W02 and W03 pass the exact frozen template to baseline generation and as
`template` on the candidate's named scenario approval. Preserve its field
order and record the template that reached the generator in each arm.

### Rehearse saved delivery before the live comparison

`scripts/rehearse_paired_delivery.py` uses an isolated app pin and the same
hash-checked source intake for the eleven workflows with one source
checkpoint. Keep the authored case controls outside Git and record their
SHA-256 separately from the frozen corpus. Pass the digest with
`--controls-sha256`. Use separate loopback Firestore processes for the two
app pins. The archived launcher serves the pinned healthy Booking and
payload editor fixtures, blocks external networking, verifies the app
archive, and keeps the generation inputs separate from reviewer keys.

The runner uses actual approvals and persistence. It exercises the
baseline generation routes and the exact named candidate continuations,
records the template that reached the generator, and compares saved case
versions with raw JSON and the saved export report. These authored cases
are deliberately delivered as reasoned drafts. The W05 refusal control
omits the draft reason and keeps its saved project and cases with HTTP 422.
A refused export is a partial slot. Keep the separately recorded W11
source-change replay with both export checkpoints.

The runner writes repeat-one private records for each arm. Combine them
with the same-pin W11 records in the intake format below after generation
exits. Do not use the rehearsal artifacts as live pairs, practitioner
labels, or evidence of fault detection.

### Capture and score delivered artifacts

- Record the exact source and review-decision manifests, scenario snapshot, suite snapshot, case versions, hydrated readback, both raw export bytes and parsed artifact, request/receipt IDs and all hashes. The export method is JSON for paired semantic comparisons. Validate other offered formats against their documented columns separately. Use the baseline's reasoned draft path where readiness alone blocks its export and log the displayed status and reason. A blocked or missing export remains a failed delivery.
- Match exported case IDs, exact step order, field values, missing/null/empty distinctions and Unicode against the hydrated saved suite. Separately check currentness, source and plan identity, review evidence, human acceptance, execution outcome, and exact historical links. Sorting object keys for comparison is permitted. Normalizing content, trimming values, or ignoring missing and additional cases is disallowed.
- Give independent QA adjudicators anonymous saved and exported artifacts with the exact authoritative source, accepted context and reviewer keys. Hide model, arm, automated scores and proposed repair. Each required obligation receives covered, unmet, or unknown with source and case/step citations. Coverage requires a coherent trigger, condition, observable business outcome, and required durable effects in the same case. A known design with named missing environment setup can still cover an obligation. A missing outcome or unverified claim does not. Show explicitly deferred obligations separately from the frozen in-scope denominator.
- Record unsupported claims by type, severity and exact artifact evidence. Synthetic input values are allowed if safe and clearly labelled. Claims that an account, record, control, mechanism or expected product behavior exists need an authoritative source or observed evidence. A claim or gap is critical if it could mask an unauthorized, duplicate, lost, corrupt, stale or incorrectly approved result or overturn a required obligation. The adjudication file premarks critical obligations. Resolve disagreements before revealing arms. Record reviewer identities and disagreements in private study evidence.
- Separate evaluation changes from generation changes. Re-run old and candidate assessors on the identical pinned adjudication examples and saved-suite pairs, and compare each to human labels. Preserve the existing quoted-filename and rendered-whitespace regression tests and score both valid and defective cases. Record false approvals, false rejections and unknown reviews separately. The app reviewer, any LLM screening judge and human business acceptance are distinct evidence.
- A mutation enters the executable denominator only when the same authored control passes healthy and fails at the specified check against the mutant. Freeze W01's eight non-duplicate Booking probes and W07's three exact-payload probes. Credit detection only when an applicable generated case passes healthy and fails at that intended assertion against the mutant. Missing eligible generated coverage, wrong failure or unverified execution receives no detection credit. W08's two retry probes remain manual-control evidence. W11's boundary outcomes and retained-case checks are a separate regression gate. A control failure invalidates that environment's measurements pending rerun.
- Capture terminal run status for every scheduled arm and retain partial suites, aborted reviews, timeouts and usage gaps. Report per-workflow and pooled results, repeated-pair deltas, counts of fully paired outputs and each exclusion with reason. Separate manual/design, browser-applicable, admitted and actually run cases. Classify every healthy-target failure before measuring a rerun. Do not drop missing arms or tune to the holdout.

For Booking `reject-but-save`, keep the selected case's raw Playwright
report in the local run artifact directory. The workbench verifies the
terminal failure after a successful starting count, one invalid submit,
the exact rejection, and reopening the same ready bookings page. The
reported count must increase by one for the same actor. An unavailable
report or an earlier failure leaves this fault uncredited.

### Prepare blinded case evidence

Use the offline `scripts/prepare_case_adjudication.py` after capturing the
actual app artifacts. Keep rehearsal and live intake separate. A private
`intake.json` declares `cohort`, `candidate_commit`, and `records`. Each
record includes `workflow`, `repeat`, `arm`, `cohort`, `commit`, `status`,
and `input_sha256` from the frozen generation input. The runner is
responsible for capturing that identity from the input it actually used.
Upload the frozen `source_document` when supplied. Otherwise use the
canonical Markdown from the input rehearsal, with each exact requirement
ID as a heading and its exact text below. The intake verifies saved
requirements against the recorded import, document hash, verbatim quote
and cited line range. W11 checks the original and revised documents
separately. A row carried forward from the original import must match the
saved original row and cite lines that are identical in both documents.

Each `delivered` record has a `files` object mapping `project`, `hydrated`,
`exported`, `report`, and `run` to `{ "path": "relative.json", "sha256": "…" }`.
Use the project read immediately before export, ordered exact case API
rows, raw exported JSON bytes, the saved file report, and the runner's own
identity capture. The runner capture binds `workflow`, `arm`, `repeat`,
`input_sha256`, `suite_snapshot_id`, and `case_count` to the slot. It also
binds `cohort`, `project_id`, and `commit` for a live run. Keep the original
case response envelopes and request, approval and provider receipts in
private run evidence. W11 also uses `before_files` with the project, case,
export and report captures from before the source change. A reviewed suite
links to the preceding Test Cases snapshot in the captured project timeline
and records the current scenario plan in its source metadata. A `failed`,
`partial`, `blocked`, or `interrupted` record includes a `reason` and any
available hashed files. Partial runs stay in the private ledger as incomplete
deliveries, with no reviewer packet or inferred coverage score.

```sh
.venv/bin/python scripts/prepare_case_adjudication.py prepare \
  --frozen "$TCG_V2_EVIDENCE/p0/evaluation/frozen" \
  --intake /tmp/tcg-v2-review/intake.json \
  --output /tmp/tcg-v2-review/package
```

The private ledger retains all 72 scheduled slots, including missing and
invalid deliveries. The `reviewer/` directory contains opaque packet IDs,
the frozen source and reviewer keys, and the exact case content. The saved
view also carries saved execution prerequisites. Keep the raw captures and
private ledger with the facilitator. Give reviewers only the packet files,
and resolve reviewer disagreements before opening the arm mapping.

Record one final label file per sample. Use its `sample_id` and
`packet_sha256`, and include `judgments` keyed by every frozen obligation.
Each judgment has `status` (`covered`, `unmet`, or `unknown`), the frozen
`source_id`, an evidence-based `reason`, and `citations` with exact `case_id`
and integer `step`. For a pre-change W11 step add `"checkpoint": "before"`.
For each covered business obligation, choose one complete saved case at
one checkpoint and cite one or more of its steps. The W11 `revision`
obligation can cite multiple cases and checkpoints to compare the original
and revised evidence. Unmet and unknown judgments may cite any relevant
exact steps. An explicitly deferred scope check may cite the source
decision in its reason with an empty citation list.

Add `fact_review` to the same label for unsupported-fact adjudication.
Mark `complete` after checking all distinct product facts in the packet.
Explain the reviewed scope in `reason`, including a reason for an empty
inventory. List each fact once per checkpoint, using one exact example
when several cases repeat it. The validator rejects repeated kind, quote,
and reason within a checkpoint. Reviewers consolidate paraphrases.
Record `kind` as `rule`, `account`, `record`,
`locator`, `outcome`, or `other`. Use verdict `supported`, `unsupported`,
or `unknown`, an explicit boolean `critical`, and the source or observed
evidence in the judgment reason. Clearly labelled synthetic test inputs
stay outside the product-fact inventory.

For each fact cite the saved case ID, field, and verbatim quote. A step
citation also carries the integer step number and uses `action`,
`expected`, or `test_data`. Case-level citations use `title`,
`description`, `preconditions`, `test_data`, `expected_result`, or
`execution_prerequisites`. Use `"checkpoint": "before"` for a fact in
the original W11 suite. This is an example claim inside `fact_review.claims`.

```json
{
  "kind": "outcome",
  "verdict": "supported",
  "critical": true,
  "reason": "The current Room Booking requirement specifies Confirmed status.",
  "citation": {
    "case_id": "TC-001",
    "step": 4,
    "field": "expected",
    "quote": "Confirmed"
  }
}
```

```sh
.venv/bin/python scripts/prepare_case_adjudication.py summarize \
  --package /tmp/tcg-v2-review/package \
  --labels /tmp/tcg-v2-review/final-labels \
  --output /tmp/tcg-v2-review/private-quality.json
```

The private summary retains all 36 pairs and each arm's delivery and label
status. It verifies delivered capture identities against the ledger cohort,
commit and scheduled slot before scoring. It compares labeled pairs on the
same obligations and reports
coverage per repeat, per workflow and pooled across matched pairs. Unknown
in-scope obligations stay in the denominator. Deferred obligations stay
separate. The pair records show obligations covered by only one arm.

The business coverage calculation can pass only for a complete live
cohort. It uses the unrounded candidate ratio for the 95% threshold,
requires zero missed critical obligations and compares the pooled candidate
and baseline counts for regression. Partial and rehearsal reports retain
their separate states. The calculation relies on verified live provenance
and independent final labels. Assessor accuracy, correction effort and
controlled fault outcomes require separate evidence.

The separate `unsupported_facts` result reports counts for each arm and
compares pooled unsupported claims divided by supported plus unsupported
facts across complete pairs. Unknown judgments stay visible in their
pair. Missing, incomplete, or unknown fact reviews keep the live gate
incomplete, as do empty pooled denominators. The live comparison requires
all 36 matched pairs and uses unrounded rates. Earlier coverage-only
labels remain readable and leave fact review pending.

### Compare assessors separately

Prepare a separate private manifest before replay. List the pinned
adjudication examples and exact saved-suite inputs from both generation
arms. Each entry has an opaque `id`, `layer` (`structural` or `design`),
a relative `file`, and its `input_sha256`. Keep the exact inputs with the
manifest, including the accepted source, context, plan and case bytes
actually supplied to the reviewer. Record `baseline_commit`,
`candidate_commit`, `cohort` (`independent` or `rehearsal`) and the
`examples` list. Record the SHA-256 of the manifest in the private study
record before running either assessor. Feed both pinned assessors those
same inputs. For each `design` example, record `review_inputs.baseline`
and `review_inputs.candidate` before assessment. Each entry has the
relative `file` containing the exact canonical bytes returned by that
pin's `review_input` and their `input_sha256`. The scorer verifies those
files and matches the raw result's own `input_hash` to the expected pin's
projection. Save the complete raw output from each layer separately.
Structural review relies on the capture runner's invocation record for
input identity.

Each arm capture has `cohort`, `manifest_sha256`, `arm` (`baseline` or
`candidate`), `commit` and `rows`. Each row has `id`, `input_sha256`
and `status`. A `completed` row includes its raw reviewer `output`. A
`failed`, `interrupted` or `blocked` row includes its `reason`. Keep
unscheduled or missing results visible through the precommitted manifest.
If an arm produced no results, record an empty `rows` list. The scorer
reads `approved` for structural review and `design_status` for independent
assessment. A model verdict of `unknown` or any unverified review groups
remain unknown even if deterministic checks report a gap. Execution
readiness and overall approval are separate.

Give adjudicators the opaque IDs, input digests and exact source and suite
evidence. Keep both assessor outputs and pins private until disagreements
are resolved and the verdicts sealed. The private label file carries
`cohort`, `manifest_sha256` and `rows` with `id`, `input_sha256`,
`verdict` (`valid`, `defective` or `unknown`), a `critical` boolean
and the adjudicator's `reason`. Reserve critical labels for defective
designs whose failure could mask a critical outcome. Retain identities
and disagreements separately in private evidence.

Run the comparison from the repository root.

```sh
python -m scripts.score_paired_assessor \
  --manifest assessor-manifest.json --manifest-sha256 "$FROZEN_ASSESSOR_SHA" \
  --baseline baseline-assessments.json --candidate candidate-assessments.json \
  --labels independent-labels.json --output assessor-comparison.json
```

Omit `--labels` while human evidence is pending. The report counts false
approvals, critical false approvals, false rejections and unknown verdicts
by arm and layer. The paired human thresholds require both layers, a known
verdict from each assessor and the adjudicator for every input, and at
least one adjudicated valid design and one critical defective design per
layer. The `rehearsal` cohort always stays separate from this gate. Keep the
original quoted-filename and rendered-whitespace regression evidence with
the report. The scorer verifies file and capture identities. Keep operator
provenance and adjudicator independence in the study record.

## Frozen acceptance gates

| Dimension | Fixed measurement and threshold |
| --- | --- |
| User effort | At least 30% fewer deliberate actions on the same normal path from inputs ready to inspected and exportable. Count clicks or keyboard activations, one text edit per committed field, and workspace changes. Report interactions, workspace transitions, active time and waiting separately. Preserve both explicit upstream approval actions. |
| Practitioner task completion | At least four of five QA practitioners complete the same core tasks without assistance. Counterbalance task order, use identical starter evidence, and retain failed attempts. Issue #354 owns the study. |
| Review effort | At least 20% fewer median corrections per adjudicated in-scope obligation with non-increasing median active review time, preserved coverage and no weaker assertions. If the baseline needs zero corrections, preserve zero and report time without inventing a percentage. |
| Business coverage | At least 95% of independently adjudicated in-scope obligations covered. Zero missed critical obligations and no baseline regression. Unknown in-scope obligations remain in the denominator. |
| Unsupported facts | Zero critical unsupported product facts and no increase in the overall rate of unsupported claims per fact adjudicated. |
| Defect sensitivity | At least 90% of frozen executable faults detected at the intended assertion. No critical-fault regression. Retain uncredited, manual and excluded probes separately. |
| Healthy reliability | At least 95% of generated applicable cases pass the controlled healthy target. No baseline regression or assertion suppression. Record blocked compilation and prerequisites separately without relabeling applicable cases as manual to improve the result. |
| Evaluator correctness | Zero critical false approvals. No increase in false rejections of adjudicated valid designs. Preserve filename and whitespace regressions. Unknown remains visible. |
| Artifact integrity | Zero silent drops, placeholder substitutions, source or history mismatches, lost literal values, or stale approvals. |
| Recovery | Zero duplicate committed artifacts or charges in controlled duplicate requests, source changes, lost responses, restarts and account-switch cases. Unknown outcomes stay recoverable and explicit. |
| Runtime cost | Median end-to-end latency and median known provider cost each at most 20% above baseline, with no unexplained increase. Report call counts, observed token/cost coverage and unknown costs. Any exception needs a documented quality gain and separate authorization. |

Passing a fixed threshold preserves an already-good baseline only when the paired result also preserves it. Claim an improvement only where a paired measure supports it. Changes to thresholds or fixtures require a documented protocol revision before candidate results are inspected.

## Baseline results and boundaries

| Command from repository root | Result |
| --- | --- |
| `.venv/bin/python scripts/evaluate_requirements.py --offline --strict` | 2/2 offline fallback fixtures passed, both scoring 100 |
| `.venv/bin/python scripts/evaluate_generation.py --offline --strict` | 2/2 safe fallback checks passed with zero delivered cases. No model-quality evidence |
| `.venv/bin/python scripts/evaluate_orchestrator.py --offline --strict` | 3/3 deterministic fixtures passed, with all expected resume and preservation outcomes |
| `.venv/bin/python scripts/run_backend_p1.py` | 186/186 passed in a loopback-capable process. The default sandbox blocked the response-loss test's socket bind |
| `.venv/bin/python scripts/evaluate_browser_handoff.py --port 58947 --output /tmp/tcg-v2-eval/browser-controls.json` | Authored controls passed all six synthetic fixtures, nine healthy executable cases and 15 intended fault probes. One external audit case remained manual |
| `.venv/bin/python scripts/evaluate_room_booking.py --output /tmp/tcg-v2-eval/booking-controls` | 12 healthy control groups passed, revised capacity passed five checks, and 10/10 intended probes detected across eight seeded mutations |
| Corpus replay | Two reproductions of the frozen input files were byte-identical. All 12 requirement/context payloads validated against baseline contracts |

Keep the exact evidence files outside Git. The available budget for this work is **0 paid API calls** pending explicit authorization. An authorized pilot should pin a hard total USD/token/request budget, per-stage ceilings, provider account/project and run-level timeout before dispatch. Use one active trial at a time, reserve enough budget for both arms of each pair, and stop on budget exhaustion, provider/model drift or unverifiable spending. Existing limits apply inside each stage. A failed or unknown provider charge is not assumed to be zero. Budget proposals can use known stage telemetry and applicable provider pricing before a future run. Practitioners, independent adjudication and any genuine production-like environment also remain external gates. No baseline or candidate live trial ran as part of this freeze.
