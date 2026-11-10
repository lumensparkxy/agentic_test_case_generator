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

- Record the exact source and review-decision manifests, scenario snapshot, suite snapshot, case versions, hydrated readback, both raw export bytes and parsed artifact, request/receipt IDs and all hashes. The export method is JSON for paired semantic comparisons. Validate other offered formats against their documented columns separately. Use the baseline's reasoned draft path where readiness alone blocks its export and log the displayed status and reason. A blocked or missing export remains a failed delivery.
- Match exported case IDs, exact step order, field values, missing/null/empty distinctions and Unicode against the hydrated saved suite. Separately check currentness, source and plan identity, review evidence, human acceptance, execution outcome, and exact historical links. Sorting object keys for comparison is permitted. Normalizing content, trimming values, or ignoring missing and additional cases is disallowed.
- Give independent QA adjudicators anonymous saved and exported artifacts with the exact authoritative source, accepted context and reviewer keys. Hide model, arm, automated scores and proposed repair. Each required obligation receives covered, unmet, or unknown with source and case/step citations. Coverage requires a coherent trigger, condition, observable business outcome, and required durable effects in the same case. A known design with named missing environment setup can still cover an obligation. A missing outcome or unverified claim does not. Show explicitly deferred obligations separately from the frozen in-scope denominator.
- Record unsupported claims by type, severity and exact artifact evidence. Synthetic input values are allowed if safe and clearly labelled. Claims that an account, record, control, mechanism or expected product behavior exists need an authoritative source or observed evidence. A claim or gap is critical if it could mask an unauthorized, duplicate, lost, corrupt, stale or incorrectly approved result or overturn a required obligation. The adjudication file premarks critical obligations. Resolve disagreements before revealing arms. Record reviewer identities and disagreements in private study evidence.
- Separate evaluation changes from generation changes. Re-run old and candidate assessors on the identical pinned adjudication examples and saved-suite pairs, and compare each to human labels. Preserve the existing quoted-filename and rendered-whitespace regression tests and score both valid and defective cases. Record false approvals, false rejections and unknown reviews separately. The app reviewer, any LLM screening judge and human business acceptance are distinct evidence.
- A mutation enters the executable denominator only when the same authored control passes healthy and fails at the specified check against the mutant. Freeze W01's eight non-duplicate Booking probes and W07's three exact-payload probes. Credit detection only when an applicable generated case passes healthy and fails at that intended assertion against the mutant. Missing eligible generated coverage, wrong failure or unverified execution receives no detection credit. W08's two retry probes remain manual-control evidence. W11's boundary outcomes and retained-case checks are a separate regression gate. A control failure invalidates that environment's measurements pending rerun.
- Capture terminal run status for every scheduled arm and retain partial suites, aborted reviews, timeouts and usage gaps. Report per-workflow and pooled results, repeated-pair deltas, counts of fully paired outputs and each exclusion with reason. Separate manual/design, browser-applicable, admitted and actually run cases. Classify every healthy-target failure before measuring a rerun. Do not drop missing arms or tune to the holdout.

### Prepare blinded case evidence

Use the offline `scripts/prepare_case_adjudication.py` after capturing the
actual app artifacts. Keep rehearsal and live intake separate. A private
`intake.json` declares `cohort`, `candidate_commit`, and `records`. Each
record includes `workflow`, `repeat`, `arm`, `cohort`, `commit`, `status`,
and `input_sha256` from the frozen generation input. The runner is
responsible for capturing that identity from the input it actually used.

Each `delivered` record has a `files` object mapping `project`, `hydrated`,
`exported`, `report`, and `run` to `{ "path": "relative.json", "sha256": "…" }`.
Use the project read immediately before export, ordered exact case API
rows, raw exported JSON bytes, the saved file report, and the runner's own
identity capture. The runner capture binds `workflow`, `arm`, `repeat`,
`input_sha256`, `suite_snapshot_id`, and `case_count` to the slot. It also
binds `cohort`, `project_id`, and `commit` for a live run. Keep the original
case response envelopes and request, approval and provider receipts in
private run evidence. W11 also uses `before_files` with the project, case,
export and report captures from before the source change. A `failed`,
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
and integer `step`. For a pre-change W11 step add `"checkpoint": "before"`. Coverage
requires at least one step citation. An explicitly deferred scope check
may cite the source decision in its reason with an empty citation list.

```sh
.venv/bin/python scripts/prepare_case_adjudication.py summarize \
  --package /tmp/tcg-v2-review/package \
  --labels /tmp/tcg-v2-review/final-labels \
  --output /tmp/tcg-v2-review/private-coverage.json
```

Coverage counts retain unknown in-scope obligations and separate the
deferred checks. Missing labels remain visible per arm. The commands
verify case integrity and label binding. Independent labels, assessor
accuracy, unsupported facts, correction effort, provider provenance and
controlled fault outcomes still need their own recorded evidence.

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
