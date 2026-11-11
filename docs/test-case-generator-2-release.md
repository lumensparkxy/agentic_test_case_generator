# Test Case Generator 2.0 release verification

The implementation lives on `codex/version_2_0`. The frozen baseline is
`2f6458d3d1bb68197ba2b5572261870277c8cadb`. Use the
[frozen evaluation protocol](test-case-generator-2-evaluation.md) for the
fixed thresholds. The application verified for this release record is
`5f7c712d38d5d2a8394804d676114d200431781e`. Both required CI checks passed
on that commit. The stack is held for final merge approval and the evaluation
scope is offline only.
[PR #589](https://github.com/lumensparkxy/agentic_test_case_generator/pull/589) ·
[CI run](https://github.com/lumensparkxy/agentic_test_case_generator/actions/runs/42928440143)

## Desktop workspaces

Set `VITE_PROJECT_WORKSPACES_V2=true` when building the frontend to enable
Inputs, Review and Deliver. The default is false. Local approval continuation
also requires `VITE_APPROVAL_CONTINUATION_ENABLED=true` and the server settings
under [Local rollout and rollback](#local-rollout-and-rollback).

| Section | Route suffix | Compatible alias |
| --- | --- | --- |
| Inputs · Sources | `/inputs` | `/requirements` |
| Inputs · Context | `/inputs/context` | `/context` |
| Review · Scope | `/review` | `/requirements` |
| Review · Scenarios | `/review/scenarios` | `/use-cases` |
| Review · Tests | `/review/tests` | `/test-cases` |
| Deliver · Export | `/deliver` | `/reports` |
| Deliver · Browser tests | `/deliver/browser-tests` | `/automation` |

Routes follow `/projects/:projectId`. Review opens the current scope or scenario
decision, then Tests after the exact scenario plan is approved. Scope and
scenario approvals remain explicit decisions. When enabled, background work
continues from each saved approval.

Review starts with the app menu collapsed. **Show app menu** reveals global
navigation and focuses Home. Escape closes the menu and restores focus to its
toggle. **Hide app menu** returns the space to the artifacts. The choice carries
across Review sections during the session and resets on reload. Suite quality
and supporting evidence expand on demand. Assessment and recovery controls
share a row when space permits and stack at higher zoom.

The selected test, filters and drafts are scoped to the user, project and saved
suite. Case, run and report links retain their original evidence. **Project
status** opens the project overview.

## Acceptance evidence

| Gate | Evidence | Open requirement |
| --- | --- | --- |
| User effort | The matched Chromium replay at `5f7c712` took seven deliberate actions against eleven, a 36.4% reduction against the 30% target. Both arms used identical saved inputs, made two explicit approvals, inspected TC-202, and changed workbenches four times. | Practitioner active time and waiting. |
| Practitioner completion and review effort | The private kit pairs the frozen baseline with `5f7c712`. The fresh headless candidate matched the archived baseline starting state with zero writes or approvals. The updated archive manifest passed. Zero participant sessions have been collected. | At least four of five unassisted completions, 20% fewer median corrections per adjudicated obligation, and non-increasing active review time. [#354](https://github.com/lumensparkxy/agentic_test_case_generator/issues/354) |
| Business coverage and unsupported facts | The offline intake and scorer bind obligation and fact judgments to exact saved and exported cases and retain unknowns. The held integration calibration preserved exact cases and removed fabricated dependencies from saved analysis. [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534) | Independently adjudicated coverage of at least 95%, zero critical misses or unsupported facts, and non-regression on the generated cohort. [#495](https://github.com/lumensparkxy/agentic_test_case_generator/issues/495) |
| Healthy reliability and defect sensitivity | W01 preserved eleven authored cases per arm, passed every eligible healthy case, and detected all eight seeded variants in each arm. At `61b7d66` both exact authored W07 cases passed healthy and all three seeded variants failed at their intended first assertion. [PR #520](https://github.com/lumensparkxy/agentic_test_case_generator/pull/520) · [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534) | The 95% healthy reliability and 90% intended fault detection thresholds apply to generated applicable cases across the frozen targets. |
| Evaluator correctness | The structural assessor kept five valid authored controls and rejected both defective whitespace controls. The separate retry comparison removed two false unmet findings on identical saved Manual cases. [PR #514](https://github.com/lumensparkxy/agentic_test_case_generator/pull/514) · [PR #530](https://github.com/lumensparkxy/agentic_test_case_generator/pull/530) | Independent verdicts and paired live assessor outputs. [#513](https://github.com/lumensparkxy/agentic_test_case_generator/issues/513) |
| Exact artifact integrity | The offline delivery replay sealed 24 authored repeat-one packets across 12 workflows and kept 48 unrun slots. The refusal replay retained one partial slot. The W11 source change preserved seven unaffected versions, replaced six, and refused fresh stale export. Current delivery code reproduced all 13 rows and their case versions at each of the four archived W11 checkpoints. [PR #518](https://github.com/lumensparkxy/agentic_test_case_generator/pull/518) | The live artifact cohort has zero of 36 pairs under the frozen protocol. [#517](https://github.com/lumensparkxy/agentic_test_case_generator/issues/517) |
| Durable decisions and recovery | Isolated Firestore verified exact approvals, response loss, cancellation, release and stale fences. Eight forced worker stops exercised lease expiry, known-unstarted replay, interrupted status and exact saved artifact recovery. The rendered W01 dialog restored focus on the saved decision before the delayed acknowledgement arrived. | Hosted worker identity, dispatch timeouts and shared producer admission in an authorized environment. |
| Runtime cost and latency | The offline ledger exercised bounded mock reservation and settlement. | Paired provider cost and latency within the 20% median ceiling, with call and token coverage. |
| Rollback | The same local project and Chromium session passed through enabled, compatible and restored bundles at `61b7d66`, with all twelve public evidence digests unchanged. The server restart at that pin preserved exact replay while refusing fresh admission. [PR #534](https://github.com/lumensparkxy/agentic_test_case_generator/pull/534) | Hosted rollout and operator verification. |

The offline engineering comparison measured seven versus eleven deliberate
actions with both approvals and the same saved and exported test cases. The
matched desktop layout check placed Requirements and Scenarios 179 CSS pixels
higher, and the selected test and its first expected result 278 pixels higher,
at 1440 × 900. The user approved the compact layout after trying a local
synthetic preview. The five-person practitioner study retains its separate
acceptance criteria.
[Layout verification](https://github.com/lumensparkxy/agentic_test_case_generator/pull/589) ·
[Study preflight](https://github.com/lumensparkxy/agentic_test_case_generator/pull/590)

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
