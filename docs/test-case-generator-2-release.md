# Test Case Generator 2.0 release verification

The implementation lives on `codex/version_2_0`. The frozen baseline is
`2f6458d3d1bb68197ba2b5572261870277c8cadb`. The phase history is in the
[implementation record](test-case-generator-2-continuation.md). Use the
[frozen evaluation protocol](test-case-generator-2-evaluation.md) for the
fixed thresholds. The application verified for this release record is
`091c45ad6b3b8c4041308fb1c4a0b048be32cadf`. Both required CI checks passed
on that commit. The stack is held for final merge approval and the evaluation
scope is offline only.
[PR #587](https://github.com/lumensparkxy/agentic_test_case_generator/pull/587) ·
[CI run](https://github.com/lumensparkxy/agentic_test_case_generator/actions/runs/42922773574)

## Acceptance evidence

| Gate | Evidence | Open requirement |
| --- | --- | --- |
| User effort | The matched Chromium replay at `091c45a` took seven deliberate actions against eleven, a 36.4% reduction against the 30% target. Both arms used identical saved inputs, made two explicit approvals, inspected TC-202, and changed workbenches four times. | Practitioner active time and waiting. |
| Practitioner completion and review effort | The private kit pairs the frozen baseline with `091c45a`. Headless runtime preflight loaded identical saved inputs in both builds and recorded zero writes or approvals. The package and observer checks passed. Zero participant sessions have been collected. | At least four of five unassisted completions, 20% fewer median corrections per adjudicated obligation, and non-increasing active review time. [#354](https://github.com/lumensparkxy/agentic_test_case_generator/issues/354) |
| Business coverage and unsupported facts | The offline intake and scorer bind obligation and fact judgments to exact saved and exported cases and retain unknowns. The held integration calibration preserved exact cases and removed fabricated dependencies from saved analysis. [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534) | Independently adjudicated coverage of at least 95%, zero critical misses or unsupported facts, and non-regression on the generated cohort. [#495](https://github.com/lumensparkxy/agentic_test_case_generator/issues/495) |
| Healthy reliability and defect sensitivity | W01 preserved eleven authored cases per arm, passed every eligible healthy case, and detected all eight seeded variants in each arm. At `61b7d66` both exact authored W07 cases passed healthy and all three seeded variants failed at their intended first assertion. [PR #520](https://github.com/lumensparkxy/agentic_test_case_generator/pull/520) · [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534) | The 95% healthy reliability and 90% intended fault detection thresholds apply to generated applicable cases across the frozen targets. |
| Evaluator correctness | The structural assessor kept five valid authored controls and rejected both defective whitespace controls. The separate retry comparison removed two false unmet findings on identical saved Manual cases. [PR #514](https://github.com/lumensparkxy/agentic_test_case_generator/pull/514) · [PR #530](https://github.com/lumensparkxy/agentic_test_case_generator/pull/530) | Independent verdicts and paired live assessor outputs. [#513](https://github.com/lumensparkxy/agentic_test_case_generator/issues/513) |
| Exact artifact integrity | The offline delivery replay sealed 24 authored repeat-one packets across 12 workflows and kept 48 unrun slots. The refusal replay retained one partial slot. The W11 source change preserved seven unaffected versions, replaced six, and refused fresh stale export. Current delivery code reproduced all 13 rows and their case versions at each of the four archived W11 checkpoints. [PR #518](https://github.com/lumensparkxy/agentic_test_case_generator/pull/518) | The live artifact cohort has zero of 36 pairs under the frozen protocol. [#517](https://github.com/lumensparkxy/agentic_test_case_generator/issues/517) |
| Durable decisions and recovery | Isolated Firestore verified exact approvals, response loss, cancellation, release and stale fences. Eight forced worker stops exercised lease expiry, known-unstarted replay, interrupted status and exact saved artifact recovery. The rendered W01 dialog restored focus on the saved decision before the delayed acknowledgement arrived. | Hosted worker identity, dispatch timeouts and shared producer admission in an authorized environment. |
| Runtime cost and latency | The offline ledger exercised bounded mock reservation and settlement. | Paired provider cost and latency within the 20% median ceiling, with call and token coverage. |
| Rollback | The same local project and Chromium session passed through enabled, compatible and restored bundles at `61b7d66`, with all twelve public evidence digests unchanged. The server restart at that pin preserved exact replay while refusing fresh admission. [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534) | Hosted rollout and operator verification. |

The matched desktop replay used mocked Chromium at the frozen baseline and
`091c45a`. Both arms started at revision 2 from byte-identical saved input
snapshots with both decisions pending. All four frozen fixture hashes matched.
Both readers displayed the same TC-202 and its saved assertion, and a direct
case lookup resolved to each arm's exact suite. The two exported case rows
matched, including literal whitespace in TC-101. Each export also matched
its earlier archived file byte for byte. The fixture used the legacy approved
export policy. This measures deliberate actions in one engineering pair.
Participant time and waiting remain unmeasured.

The headless facilitator preflight opened Context in the frozen baseline and
Inputs Context in the candidate at 1440 × 900. Both ran Chromium 151.0.7922.34
against loopback mocks with identical saved Requirements and Context snapshot
IDs, input hashes and four fixture hashes. Both decisions were pending. The
runner observed 1.2 seconds of idle time with zero writes or approvals and then
closed the browser. The earlier headed facilitator dry checks used `61b7d66`.
The current evidence verifies the study's starting state.

At `db54730`, Review starts with the app-wide menu collapsed. In the matched
layout fixture, the first requirement row, scenario group, selected test and
first expected result each start 76 CSS pixels higher at 1366, 1440 and 1920
pixels wide. The 1440-pixel keyboard check preserved the selected case and
search filter in a 200-case suite. The 390-pixel check verified the menu and
project controls without horizontal overflow.
[PR #583](https://github.com/lumensparkxy/agentic_test_case_generator/pull/583)

Deliver blocks fresh files for stale suite sources. An exact human request for
changes or a recorded proof that needs reassessment, shows design gaps, or
cannot be verified requires a reasoned draft where sources permit delivery.
Paired policy checks preserved ordinary approval for a legacy suite without
recorded proof and reviewed-design eligibility for the verified control.
Historical reports retained their recorded classification and case versions.
[Human decision](https://github.com/lumensparkxy/agentic_test_case_generator/pull/580) ·
[Source freshness](https://github.com/lumensparkxy/agentic_test_case_generator/pull/581) ·
[Recorded design proof](https://github.com/lumensparkxy/agentic_test_case_generator/pull/582)

Browser tests blocks Run when the saved suite has stale sources. The 2.0 Run
action also sends the saved Preview's request ID. The API verifies the suite,
revision, target and compiled candidates before launching the selected cases.
In the controlled `091c45a` loopback replay, the real Preview and Run routes
used an in-memory project store and Chromium. The selected Checkout case
passed healthy and failed at its original heading assertion on the seeded
fault. Both runs recorded the original suite and requested only `/checkout`.
[Stale source Run gate](https://github.com/lumensparkxy/agentic_test_case_generator/pull/586) ·
[Saved Preview binding](https://github.com/lumensparkxy/agentic_test_case_generator/pull/587)

The read-only W11 delivery replay at `db54730` hydrated all 13 exact saved
cases per checkpoint and reproduced their archived JSON rows and version
evidence. Both baseline checkpoints qualified for ordinary approved files
and both candidate checkpoints qualified for reviewed-design files.

The authored W11 browser replay exercised the original and revised booking
limits. The frozen baseline passed all ten original and nine revised cases
admitted to Chromium. The candidate at `a95c27b` passed eleven at each
checkpoint, including the exact saved lower-bound check after reopening.
Seven unaffected case versions stayed exact. The revised case rejected seven
attendees and found the same saved count after reopening. Against the old
limit it failed at the intended inline assertion. Both arms refused a fresh
Run on stale source. [#522](https://github.com/lumensparkxy/agentic_test_case_generator/issues/522)

The worker recovery replay pinned `bb500a685fcb1be0ae4e47833d1a1238d896e801`
and used loopback Firestore with a synthetic SDK transport. Fresh workers
resumed two jobs stopped before dispatch, marked four jobs with uncertain
dispatch interrupted, and recovered two jobs stopped after publication.
Eight repeat polls preserved exact evidence with zero additional provider
attempts. Each published artifact had one usage event. Each published Test
Cases suite had one local shadow billing receipt and an exact case readback.
The phase PR records the reproduction command and evidence hashes.

The rendered rollback at `61b7d66` used one disposable Firestore project
at revision 16 and one Chromium session at 1440×900. The seed saved a historical
reasoned-draft JSON report, a successor report with a distinct case version,
and a healthy and seeded-fault Directory run. Both aliases resolved in all
three bundle states. The browser followed six historical report-to-case
round trips, twelve run-to-case round trips, six direct case links and six
exact failed-run Context links. The latter focused Quality feedback and
showed one linked finding, excluding unrelated preview and scenario notes.

The browser made 534 GETs with zero API mutations, automatic runs, unexpected
requests or page errors. The storage audit compared all 67 Firestore
documents. Sixty-five matched exactly. Existing entitlement reads refreshed
only `updated_at` on the owner billing account and user profile. All project
evidence matched. The harness blocked 36 optional font stylesheet requests
and all provider traffic. The extra orchestrator status read compared with
the previous rehearsal kept the same evidence. The phase PR records the
manifest, input and file digests, browser captures, and reproduction command.

In a separate W01 server rehearsal at the same pin, Chromium exported the
11 exact reviewed cases as JSON. The local worker finished and the outbox
was empty before the API restarted with reviewed-design delivery disabled.
The second process replayed the original request with HTTP 200 and
byte-identical JSON. A fresh request against the current revision returned
HTTP 409 with `design_delivery_disabled`. The exact saved report and
original case still matched. All 59 Firestore document contents and update
times were identical across the restart and readback. The worker used
six authored SDK responses in the loopback emulator. The client and server
lanes use separate isolated projects.

The full backend suite passed 1,075 tests on the rollback pin `61b7d66`,
including the opt-in loopback Chromium target-binding test. The clean archive
contained no local `.env` and used read-only Git metadata for the provenance
checks. Both required CI jobs passed separately.
[Rollback CI](https://github.com/lumensparkxy/agentic_test_case_generator/actions/runs/42789788414)

The frozen W07 pair and the earlier reasoned-draft capture remain as
recorded. The separate current-pin replay kept both approved case contents
and exact literal values while the structural review moved from 89 to 95 at
the unchanged threshold of 90. It exported reviewed-design JSON and admitted
both cases in Preview. A distinct execution capture then ran both exact
saved cases healthy and the three authored faults with matching compiled
assertions. These results support controlled artifact and execution behavior.
Live generation, independent artifact judgments and practitioner acceptance
remain open. [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534)

## Local rollout and rollback

1. Record the application commit, frontend asset digests, Firestore demo
   project, current revision, exact source/plan/suite IDs, approval IDs,
   case versions, file report IDs, run record IDs and known in-flight
   request identities. Keep evidence and synthetic data outside Git.
2. Build the enabled client with `VITE_PROJECT_WORKSPACES_V2=true`. Set
   `VITE_APPROVAL_CONTINUATION_ENABLED=true` only for the authorized demo
   workflow. Reviewed-design file admission is separately controlled by
   `TCG_DESIGN_DELIVERY_ENABLED`. Runtime continuation and its shared
   draft producers require `TCG_APPROVAL_SCENARIOS_ENABLED=true`,
   `FIREBASE_PROJECT_ID=demo-tcg-v2-continuation`, and a matching
   loopback `FIRESTORE_EMULATOR_HOST`. The local worker is
   [`scripts/run_approval_scenario_worker.py`](../scripts/run_approval_scenario_worker.py).
   An authorized hosted rollout needs a verified worker and admission contract.
3. Stop accepting new scoped work. Let admitted jobs settle, or reconcile
   and release their claims through the original receipt and fence. Read
   saved continuation status and use its cancel action or the scoped draft's
   fenced release where available. Preserve ambiguous provider usage as
   interrupted evidence. Resolve claims before any server flag transition.
   Preview and browser execution keep their separate authorization and
   request evidence.
4. Serve the compatible client built with workspace flags off while
   retaining the additive backend, schemas, exact readers and server
   admission. Reopen the same project and test both aliases for the
   case, run and file report. Verify scope decisions, statuses, draft
   evidence, original case assertions, and stable project version IDs.
5. Restore the enabled client and re-read the same artifact IDs. Compare
   digests and completed/pending receipts. Monitor new faults, duplicate
   publication or charge, unknown provider outcomes and mismatched
   source/version IDs. Hold rollout if any of those checks fail. Keep a
   dated rollback record with observed results and evidence.
6. Verify disabled reviewed-design admission as a separate server check
   after the demo workers are terminal. Historical evidence remains
   readable and an exact current file request can replay its receipt.
   A fresh reviewed-design intent follows the active server policy.

## Support and release boundaries

- For an unconfirmed approval or draft attempt, use the existing status
  read with its original request ID. Inspect the exact saved result before
  an explicit release or retry. Project switches keep their own state.
  For scope and scenario approvals, Check status reads the project before
  looking up the exact attempt. It clears the local attempt only when the
  project revision advanced and both the receipt and run are absent.
- If Review confirms the stage failed before any provider request, use the
  one-time stage retry from the saved approval. The original scope, settings,
  and request budget apply. An interrupted provider outcome still requires
  explicit acknowledgement and release. Provider usage remains unconfirmed
  after release when possible dispatch was recorded.
- An independently assessed suite may have blocked browser setup. Review
  the named prerequisite or publish a permitted file. The browser uses
  the current target preview and an explicit selection.
- An actual failed browser outcome records its exact source finding. A
  reviewer classifies it with the saved assertion visible. Use **Back to
  saved run** on verified findings to reopen that run's evidence in Deliver.
  Current setup and older run evidence retain distinct routes.
- Historical file reports retain saved audit evidence and links to original
  case versions. The file bytes are returned at export. Exact current
  receipt replay stays governed by source freshness and original identity.
- [#251](https://github.com/lumensparkxy/agentic_test_case_generator/issues/251)
  owns the live fresh and 8→6 changed-source journey.
  [#274](https://github.com/lumensparkxy/agentic_test_case_generator/issues/274)
  and [#280](https://github.com/lumensparkxy/agentic_test_case_generator/issues/280)
  own held guidance and its matched Test Cases evaluation.
- Keep separate decisions for merge, deployment, and paid evaluation.
  The current evaluation has zero authorized paid requests. The dry paired
  ledger exercised mock reservation and settlement. A live adapter still
  needs the approved billing project, spend envelope, and verified transport.
