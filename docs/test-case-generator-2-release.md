# Test Case Generator 2.0 release verification

The implementation lives on `codex/version_2_0`. The frozen baseline is
`2f6458d3d1bb68197ba2b5572261870277c8cadb`. The phase history is in the
[implementation record](test-case-generator-2-continuation.md). Use the
[frozen evaluation protocol](test-case-generator-2-evaluation.md) to
score live results. The verified application commit is
`e94ce215da16630c53edfa90d170542d4bf8eb65`. The current release record is
local and unmerged.

## Acceptance evidence

| Gate | Evidence | Open requirement |
| --- | --- | --- |
| Desktop effort | The frozen paired Chromium journey took seven deliberate actions against eleven, a 36.4% reduction. Both explicit approvals and four workbench transitions remained. | Human active time, completion and correction effort. |
| Exact artifact integrity | Local Firestore and rendered checks exercised saved cases and versions, source changes, scoped repair, CSV/XLSX/JSON delivery, report and run history, and literal data. A fresh W01 rendered journey matched all eleven downloaded cases to their exact saved versions. The frozen W11 rehearsal admitted four exact 13-case exports from both pins and both source checkpoints into blinded intake. | The full live 36-pair artifact cohort. |
| Durable decisions and recovery | Isolated Firestore verified exact approval, response loss, cancellation, release and stale fences. Eight forced worker stops verified natural lease expiry, replay of known unstarted work, explicit interrupted status and exact saved artifact recovery for both stages. The material 8→6 local replay preserved exact unaffected cases and reapproved the revised plan. The rendered W01 dialog restored focus on the exact saved decision before the delayed acknowledgement arrived. | Hosted worker identity, dispatch timeouts and shared producer admission in an authorized environment. |
| Grounded design and quality | The two-workflow native SDK rehearsal completed four arms with authored responses and exact JSON comparison. Local repair retained prior assertions and appended a checked observation. | Authorized live pairs, independent adjudication, generated-case healthy and fault gates, and runtime cost and latency. |
| Browser execution | Four authored controls passed on healthy targets and all five seeded faults failed at the intended saved assertion through the actual app/runtime. The rendered Deliver path separately verified deliberate selection, failed evidence, the exact original case, and zero executable candidates. | Applicable generated cases on the frozen targets. |
| Rollback | A same-project Chromium session exercised enabled, compatible legacy and restored bundles, with exact case, run, Context finding and historical report links. All twelve canonical public evidence digests matched. | Hosted rollout and operator verification. |
| Practitioner completion | Study task and recording sheet are prepared against the frozen rubric. | At least four of five unassisted completions under [#354](https://github.com/lumensparkxy/agentic_test_case_generator/issues/354). |

The worker recovery replay pinned `bb500a685fcb1be0ae4e47833d1a1238d896e801`
and used loopback Firestore with a synthetic SDK transport. Fresh workers
resumed two jobs stopped before dispatch, marked four jobs with uncertain
dispatch interrupted, and recovered two jobs stopped after publication.
Eight repeat polls preserved exact evidence with zero additional provider
attempts. Each published artifact had one usage event. Each published Test
Cases suite had one local shadow billing receipt and an exact case readback.
The phase PR records the reproduction command and evidence hashes.

The rendered rollback used one disposable Firestore project at revision 16
and one Chromium session at 1440×900. The seed saved a historical
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
digests, browser captures, and reproduction command.

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
