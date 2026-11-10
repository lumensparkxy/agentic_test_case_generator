# Test Case Generator 2.0 release verification

The implementation lives on `codex/version_2_0`. The frozen baseline is
`2f6458d3d1bb68197ba2b5572261870277c8cadb`. The phase history is in the
[implementation record](test-case-generator-2-continuation.md). Use the
[frozen evaluation protocol](test-case-generator-2-evaluation.md) for the
fixed thresholds. The most recent local rollback rehearsal used
`6de863c4757a7c1560fafd5e0c58dcd8cf5064a0`. The stack remains open for
final merge approval. The current evaluation scope is offline only.

## Acceptance evidence

| Gate | Evidence | Open requirement |
| --- | --- | --- |
| User effort | The paired, mocked Chromium journey still takes seven deliberate actions against eleven, a 36.4% reduction. Both explicit approvals and all four workbench transitions remain. The four fixture hashes match the frozen run. | Practitioner active time and waiting. |
| Practitioner completion and review effort | The task and recording sheet are prepared against the frozen rubric. | At least four of five unassisted completions under [#354](https://github.com/lumensparkxy/agentic_test_case_generator/issues/354), and the paired correction and active review time measures. |
| Business coverage and unsupported facts | The offline intake and scorer bind obligation and fact judgments to exact saved and exported cases and retain unknowns. The two-workflow native SDK rehearsal exercised four arms with authored responses. | Independent labels for critical misses, unsupported facts and the paired rates on the generated cohort. |
| Healthy reliability and defect sensitivity | The W01 authored suite saved and exported eleven exact cases per arm. All ten baseline and eleven candidate cases admitted to healthy Chromium passed. Both arms ran the eight frozen variants and retained the first failed assertion for each selected case. The candidate also verified the previously blocked empty field, and the reject-but-save probe checked the terminal count after reopening. [PR #520](https://github.com/lumensparkxy/agentic_test_case_generator/pull/520) | Generated applicable cases, healthy results and credited intended assertions across the frozen targets. |
| Evaluator correctness | On identical authored inputs the candidate structural assessor rejected two defective whitespace designs the baseline accepted and kept all five valid controls, including four quoted filenames. Separate authored responses exercised the design and browser-readiness verdicts. [PR #514](https://github.com/lumensparkxy/agentic_test_case_generator/pull/514) | Independent verdicts and paired live assessor outputs. |
| Exact artifact integrity | The offline delivery replay sealed all 24 repeat-one packets across 12 workflows and kept 48 unrun slots. The refusal replay kept the W05 saved suite as one partial slot. In both W11 arms, thirteen exact cases were exported at each source checkpoint, seven unaffected versions stayed exact, six were replaced, and a fresh stale export returned HTTP 409. [PR #518](https://github.com/lumensparkxy/agentic_test_case_generator/pull/518) | The full live 36-pair artifact cohort. |
| Durable decisions and recovery | Isolated Firestore verified exact approvals, response loss, cancellation, release and stale fences. Eight forced worker stops exercised lease expiry, known-unstarted replay, interrupted status and exact saved artifact recovery. The rendered W01 dialog restored focus on the saved decision before the delayed acknowledgement arrived. | Hosted worker identity, dispatch timeouts and shared producer admission in an authorized environment. |
| Runtime cost and latency | The offline ledger exercised bounded mock reservation and settlement. | Paired provider cost and latency, including call and token coverage. |
| Rollback | The same local project and Chromium session passed through enabled, compatible and restored bundles with all twelve public evidence digests unchanged. The separate server restart preserved exact replay while refusing fresh admission. | Hosted rollout and operator verification. |

The current desktop replay checked the exact default template in the
scenario approval request, the same two exported cases including literal
whitespace, and the direct saved-case link. The archived mock initially
expected the earlier request shape. The replay assertion now includes the
template field. Source, Context, plan and suite fixtures are unchanged.

The authored W11 browser replay exercised the original and revised booking
limits. The frozen baseline passed all ten original and nine revised cases
admitted to Chromium. The candidate at `e21f06e` passed eleven at each
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

The rendered rollback at `6de863c` used one disposable Firestore project
at revision 16 and one Chromium session at 1440×900. The seed saved a historical
reasoned-draft JSON report, a successor report with a distinct case version,
and a healthy and seeded-fault Directory run. Both aliases resolved in all
three bundle states. The browser followed six historical report-to-case
round trips, twelve run-to-case round trips, six direct case links and six
exact failed-run Context links. The latter focused Quality feedback and
showed one linked finding, excluding unrelated preview and scenario notes.

The browser made 534 GETs with zero explicit API mutations, unexpected
requests or page errors. The storage audit compared all 67 Firestore
documents. Sixty-five matched exactly. Existing entitlement reads refreshed
only `updated_at` on the owner billing account and user profile. All project
evidence matched. The harness blocked 36 optional font stylesheet requests
and all provider traffic. The phase PR records the manifest, input and file
digests, browser captures, and reproduction command. The earlier rehearsal
at `6ad1efa` is recorded in
[#510](https://github.com/lumensparkxy/agentic_test_case_generator/pull/510).

In a separate W01 server rehearsal at the same pin, Chromium exported the
11 exact reviewed cases as JSON. The local worker finished and the outbox
was empty before the API restarted with reviewed-design delivery disabled.
The second process replayed the original request with HTTP 200 and
byte-identical JSON. A fresh request against the current revision returned
HTTP 409 with `design_delivery_disabled`. The exact saved report and
original case still matched. All 59 Firestore document contents and update
times were identical across the restart and readback. The worker used
six authored SDK responses in the loopback emulator.

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
  reviewer classifies it with the saved assertion visible. Current setup
  and older run evidence retain distinct routes.
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
