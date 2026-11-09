# Test Case Generator 2.0 implementation record

The baseline is commit `2f6458d3d1bb68197ba2b5572261870277c8cadb`, tree
`a566f1cf94219e7e924db8fc54e516eb1d81f3a8`. The pre-existing local
`codex/free-hand` checkout has the same tree. The 2.0 integration branch is
`codex/version_2_0`. Phase diffs use stacked pull requests over an immutable
baseline branch. Final approval is required before a merge or targeting main.

[P0 (#449)](https://github.com/lumensparkxy/agentic_test_case_generator/issues/449)
records the migration decisions and comparison method. The
[2.0 coordination issue (#448)](https://github.com/lumensparkxy/agentic_test_case_generator/issues/448)
tracks phase delivery. Prior release evidence and the live acceptance owners
retain their scope.

## Workspace and route contract

| Workspace | Main decision or action | Existing seams and aliases |
| --- | --- | --- |
| Inputs | Review scope | Requirement import, compare/apply, and Context inputs. Requirements and Context links stay resolvable. The legacy Requirements screen contains both import and scope review during migration. |
| Review | Approve scope, approve scenarios, inspect the suite, or resolve a finding | Scope workbench, scenario review, test reader, traceability, saved-suite review and targeted repair. Keep Use Cases and Test Cases URLs, including their case and snapshot parameters. |
| Deliver | Export the saved suite or choose eligible browser tests to run | Existing Reports export/evidence and Automation preview/run. Keep historical report and run links bound to their exact evidence. |

Project identity and revision stay visible. Home, Projects, Reviews, Runs, and
Reports remain cross-project destinations. Project overview remains reachable
as a status/recovery link. Keep the existing route handlers as evidence
destinations and project them into the three workspaces. Map workspace entries
and subsections without redirects that discard query parameters, hashes, or
history state. Explicit case and snapshot links take precedence over the
current suite or cached reader selection. Keep review filters, scroll and
drafts in their existing user/project/snapshot scopes. Resolve counts and
decisions against one hydrated project revision. Opening a workspace can
perform reads.

P1 introduces the shell with the frontend build flag
`VITE_PROJECT_WORKSPACES_V2=true`, off by default. P2 brings source collection and supporting context together in Inputs and
moves scope decisions to Review. The legacy routes continue to read the same artifacts when the flag
is off. Each new workspace route resolves to an existing workbench when the
flag is off. Keep server admission checks active on both paths.

| Workspace section | Workspace route suffix | Evidence alias |
| --- | --- | --- |
| Sources | `/inputs` | `/requirements` with the combined intake/review view |
| Context | `/inputs/context` | `/context` |
| Scope | `/review` | `/requirements` with the combined intake/review view |
| Scenarios | `/review/scenarios` | `/use-cases` |
| Tests | `/review/tests` | `/test-cases` |
| Export | `/deliver` | `/reports` |
| Browser tests | `/deliver/browser-tests` | `/automation` |

These suffixes follow `/projects/:projectId`. The project root still opens
the overview via Project status. Roll back by rebuilding this compatible
version with the flag off. Older builds released before the aliases were
added cannot resolve the new suffixes.

### Input and scope state

In the enabled build, new projects open Inputs. A pending comparison keeps
source replacements and retirements explicit. Local comparison choices are
scoped to the pending import and base revision. Apply still checks the
project revision and asks for retirement confirmation. Context and intent
saves wait until the active comparison is finished or cancelled.

Supporting context uses the existing Analyze Context action. The 2.0 form
keeps local drafts under the current user, project, and context snapshot.
It shows older values for comparison when a newer context snapshot or a
material source change supersedes them. Only saved evidence feeds the
normal project generation request. Context with a stale source must be
analyzed again before starting generation. Compatible saved context stays
current across approval-only requirements edits.

Testing intent uses the existing revision-checked project PATCH. The
optional `testing_intent` record stores a testing objective and risk
emphasis separately from factual context and approval metadata. The current
generation prompts are unchanged. Review Scope uses the saved requirements
and existing clarification workflow. Deferred and excluded items are shown
separately from eligible scope. Open questions still need a decision.

## Continuation and recovery decisions

[Orchestrator runs](../backend/app/services/orchestrator_run_service.py) persist
run records, events and checkpoints. The
[API container](../backend/Dockerfile) runs Uvicorn and the repository ships no
durable queue consumer. Existing long-running specialist requests and
[project sync](../backend/app/services/project_sync.py) have different claim
and receipt paths. Reuse the claim and fencing pattern from
[impact application](../backend/app/services/impact_application_state.py)
and the receipt states in
[execution requests](../backend/app/services/execution_request_service.py).
Transactional publication guards alone cannot prevent a second provider call
after a retry or process loss.

P3 will add a deterministic continuation over the existing specialists.
Prototype a durable transactional intent and worker claim against Firestore
with a local worker and fault injection. Keep the scheduling transport
separate from the persisted intent. For a Cloud Run release, use authenticated
queue delivery to a bounded worker entry point and an outbox reconciler once
queue identity, timeouts, and runtime permissions can be verified in an
authorized environment. A browser request, an in-process task, or a Firestore
record alone does not satisfy restart recovery.

| Event | Authoritative transition and guard |
| --- | --- |
| User approves current requirements or scenarios with continuation | Validate actor, decision, source, and base revision. Persist the decision, receipt, intent, and outbox entry atomically. Only the admitted intent can start paid work. |
| Approval saved, dispatch fails | Preserve approval and retry dispatch for the original intent. Inspect current evidence before starting another attempt. |
| Duplicate delivery, two tabs, project sync | Reuse the intent identity and claim a shared project lease transactionally before provider work. Fence writes with the active claim token and current source revision. |
| Stage completes | Read the committed receipt and artifact identity. Advance with the revision produced by that stage. Pause for the next human decision or mark complete. |
| Process stops or response is lost | Reconcile saved receipts first. Resume known unstarted work or a known checkpoint. An ambiguous paid attempt remains interrupted with usage unknown until deliberate recovery. |
| Cancellation or source changes | Prevent new stages and late publication. Preserve existing snapshots and previous evidence. An issued provider call can finish and incur cost. |
| Account or project changes | Reject late UI updates outside the active identity and workspace. Existing server work follows its recorded authorization. |
| Browser run | Preview and run stay deliberate. No continuation action can start a browser run. |

Bind intent, idempotency key, stage, source snapshot identities, versioned
settings/guidance, scope, deadline, and budget to the actor and project.
Inventory nested specialist retries before combining stages. Charge and publish
under the same source/claim guard where supported. Treat unknown usage as
unknown and distinguish one committed artifact from one external provider call.

### Persisted approval foundation

The internal approval path in #455 uses the existing requirement and
scenario review transactions. A named, eligible decision can create an
outbox entry and private, versioned run in the same commit as its receipt.
An ineligible next stage leaves a saved decision and an explicit blocker
reason. Exact replay uses that receipt. The public approval-only routes
retain their current behavior.

The claim uses one per-project Firestore lease and a monotonic fence. Known
transaction contention leaves work due for a later poll. The dispatch
reservation records a possibly paid attempt before an invocation, so an
expired uncertain attempt cannot be reclaimed automatically. The result
guard verifies the exact actor, operation, request, revision and source
lineage before the artifact transaction advances the run.

### Scenario preparation in the local emulator

The Review action **Approve scope and prepare scenarios** records the
approval and next stage through its named API. The worker picks up the
persisted intent independently of the browser. The Review status reads
the owner-scoped run and exact approval receipt. It recovers a lost
response using the same request identity and offers a link when the
matching saved scenario plan is current. Scenario approval stays explicit.

The local runtime requires `TCG_APPROVAL_SCENARIOS_ENABLED=true`, the
`demo-tcg-v2-continuation` Firebase project and a loopback Firestore
emulator. The build requires `VITE_PROJECT_WORKSPACES_V2=true` and
`VITE_APPROVAL_CONTINUATION_ENABLED=true`. The worker checks the
configured project and the actual emulator client before polling. Use
`scripts/run_approval_scenario_worker.py --once` for one bounded scan or
omit `--once` for polling. Use the repository virtual environment and
an isolated synthetic project. A live provider requires an explicitly
authorized budget and environment.

Each local scenario run reserves at most seven provider requests, disables
SDK retry multiplication and uses the approved sources and a fixed
settings fingerprint. Observed provider cost remains unknown when the
provider does not return usage. The emulator validation uses a synthetic
provider. Shared admission with legacy and Project Sync work remains a
release gate before enabling a hosted worker.

### Exact-plan Test Cases worker

The Test Cases worker uses the saved requirements, current Context or
explicit Context absence, exact scenario plan and latest matching human
approval. It rejects an unbound plan before generation. The existing
project-bound HTTP entry point uses the same generation and publication
path, retaining its legacy admission.

The worker shares a 24-request reservation across the writers and the
independent critic. The publication transaction reads the active worker
fence before staging billing, case versions, review progress, suite and run
advancement. The exact receipt remains recoverable after a newer revision.
An incomplete replacement preserves the existing suite. A recoverable
assessment outage retains valid saved cases for saved-suite review. A saved
run result proves publication of that exact suite. Design and human
acceptance are separate evidence.

### Named scenario approval and suite recovery

**Approve scenarios and generate tests** applies to the currently displayed
scenario plan from a completed scope preparation run. The existing review
dialog keeps the decision explicit. Requesting changes, editing individual
scenarios and approving legacy plans use their existing review behavior.

The second approval binds the exact plan, prior run, approved scope and
sources before attaching one child. It commits the human decision, receipt,
child intent and outbox with the budget allocation. The two stages share a
31-request ceiling, with up to seven reserved for preparation and 24 for test
generation. A missing or spent budget yields a recoverable receipt for the
saved approval. Exact replay returns the recorded outcome without consuming
another reservation, including after a change to server settings.

Review shows the stage and exposes readback and cancellation. After a lost
response it reads the exact owner, project, stage and request. A retry of an
unconfirmed decision reuses the original request identity and comment.
The link to Review Tests appears when the exact saved suite is current.
An assessment outage remains a separate saved-suite review action. The
worker stays limited to the local emulator and invokes no browser execution.

### Shared draft admission and assessment recovery

The local approval worker and saved-suite assessment share the same
project lease and increasing fence. The assessment uses its exact project
revision, suite and upstream sources. It reserves at most 24 provider
requests before dispatch and has a four-minute deadline. The exact
assessment receipt and immutable review snapshot commit with release of the
lease. Compatible partial assessment checkpoints can be recovered.
Old continuation leases remain readable.

If an attempt expires before dispatch, new work can claim a higher fence.
After possible dispatch, Review shows the exact unresolved assessment.
The owner can cancel a running assessment or acknowledge the interrupted
attempt. The release names its operation and fence. Requests already sent
may finish. Further provider calls, checkpoint writes and result publication
from the old claim lose authority. Rechecking status never starts work.

Until shared admission covers the remaining producers, the opted-in local
demo defers paid draft refresh from Project Sync and direct scenario, suite,
refinement, impact and generated-report entry points. Project Sync can
still save an explicit correction. Deterministic impact analysis, execution
preview and deliberate browser runs keep their existing admission. The
shared lease covers competing project draft providers. Source intake uses
its existing extraction policy. Flag-off and standalone flows retain their
existing paths.

## Quality and delivery policy

Use the existing [saved suite review](../backend/app/services/test_case_suite_review.py),
[export evidence](../backend/app/services/export_evidence.py), and
[execution readiness](../backend/app/services/execution_readiness.py) seams.
The substantive assessment already records design and execution dimensions.
P4 must derive new fields on the server and migrate consumers together.
Maintain the conservative legacy approval during the additive transition.
Audit orchestrator actions, export, execution, refinement, billing and clients
before changing any writer. Preserve the structural result before combined
assessment. Base export scope on the generation's approved subset and record
exclusions separately. Derive execution outcomes from matching saved runs.

| Dimension | Source and effect |
| --- | --- |
| Source freshness | Compare snapshot source IDs/revisions and existing stale state. Historical evidence remains readable. |
| Requirement and scenario decisions | Require approved scope and the human decision on the exact current plan for the standard path. |
| Design assessment | Complete requires valid delivered cases and a complete assessment for the exact suite. Structural failures, missing cases and placeholders still block design completion. Absent legacy evidence is unknown. |
| Human suite acceptance | Use an exact-snapshot human decision only where recorded. Missing means unavailable. |
| Execution readiness | Derive per target from preview, prerequisites and server admission. A design can be complete while browser execution is blocked, manual, or unsupported. |
| Execution result | Use the actual run bound to the saved suite and target. Record no run or unknown where applicable. |

A current, complete manual design with valid upstream decisions can qualify
for design export under the new policy, independently of execution setup.
Incomplete or unverifiable designs use the existing reasoned draft path.
Stale current exports and placeholder pseudo-tests remain blocked. Execution
keeps its existing server gates. Runtime project corrections remain scoped to
project data and dependent drafts.

## Baseline evidence

[Desktop workspace sketches](test-case-generator-2-workspaces.md) show the
intended layout and behavior across the phases.

| Check | Result at the baseline |
| --- | --- |
| Strict offline requirements | 2/2 fixtures pass. |
| Strict offline generation | 2/2 safe-fallback fixtures pass, with zero generated cases. |
| Strict offline orchestrator | 3/3 lifecycle fixtures pass. |
| Critical backend selection | 186/186 pass. |
| Focused recovery and policy tests | 153/153 pass across orchestrator, sync, repair, decisions, generation recovery, execution admission and export. |
| Desktop Chromium | 16/16 mocked tests pass across create/import/review, 1366/1440/1920 reader recovery, selected repair, saved evidence, preview and export. |
| Frontend build | Passes with the existing Vite chunk-size warning. |
| Controlled browser faults | Authored controls pass on healthy targets and detect the seeded faults at their intended assertions. This validates the controls for later candidate evaluation. |

Run the corresponding offline commands in
[Testing Patterns](codebase/TESTING.md). The mocked desktop baseline used
a disposable JWT and loopback Vite. It did not use Firebase, call a model,
or run a live project. Product walkthrough screenshots and the command log
remain outside Git. Paired candidate measurements must use the same
starting state, fixture, end state, and counting method.

### Paired desktop starting point

The 1440 × 900 mocked Chromium journey starts after both pasted requirements
and context are saved, with scope awaiting approval. It ends with the selected
saved case inspected and JSON export enabled. Count one deliberate action for
each click, keyboard activation, or committed text edit.

The baseline took **11 actions**, four workbench transitions and two explicit
approvals. The sequence was Requirements, bulk review, approve non-rejected,
Use Cases, generate plan, open scenario decision, approve plan, Test Cases,
start generation, inspect case, Reports. The 30% gate allows at most **7
actions** for this paired fixture. The archived interaction log records a
2.646-second core span including scripted waits and screenshots under mocked
responses. Measure live waiting and practitioner time separately.

Scenario approval recorded snapshot `snapshot-use-cases-v3` at revision 4.
The JSON download matched the saved mocked suite including the literal test
data `  QA–01  `. Selected repair preserved the other case. The execution
preview classified both cases as manual and left Run disabled. The browser
recorded zero page errors, unmocked API calls, or run requests.

To replay the walkthrough, copy `p0/desktop/baseline-count.mjs` from the P0
evidence into `frontend/e2e` in an isolated checkout at the pinned baseline.
Run loopback Vite with `VITE_API_BASE=http://127.0.0.1:43998` on port 43999.
From `frontend/`, run:

```sh
JWT_SECRET_KEY=tcg-2-mocked-browser-fixture-signing-secret \
E2E_BASE_URL=http://127.0.0.1:43999 node e2e/baseline-count.mjs
```

At P3d, the paired mocked Chromium replay took **8 actions** and four
workbench transitions, a **27.3% reduction** in actions. The source inputs,
saved plan and test cases matched the frozen fixture. The reviewer made both
approvals, selected the non-default case TC-202 and reached enabled JSON
export. A separate export inspection matched the two saved cases. The
recorded core span was 5.902 seconds including scripted polling and
screenshots. These are mocked timings. The action count remains above the
seven-action threshold. Direct selection of a saved case from the completed
generation status is a P5 candidate for the remaining action.

[The frozen evaluation protocol](test-case-generator-2-evaluation.md) defines
the corpus, measurement rules and thresholds for the paired comparison.

## Continuation state

P0 pins the baseline and records the prospective gates (commit `2327ab9`,
[PR #450](https://github.com/lumensparkxy/agentic_test_case_generator/pull/450)).
P1 adds opt-in navigation and compatible aliases (commit `1d5977b`,
[PR #452](https://github.com/lumensparkxy/agentic_test_case_generator/pull/452)).
P2 adds unified intake and scoped decisions (commit `750fa78`,
[PR #454](https://github.com/lumensparkxy/agentic_test_case_generator/pull/454)).
P3a adds the internal transactional approval, intent outbox and fenced
stage claim (commit `ca554da`,
[PR #456](https://github.com/lumensparkxy/agentic_test_case_generator/pull/456)).
P3b connects scenario preparation and Review status (commit `2bf1cef`,
[PR #458](https://github.com/lumensparkxy/agentic_test_case_generator/pull/458)).
P3c adds the exact-plan Test Cases worker (head `86c194b`,
[PR #460](https://github.com/lumensparkxy/agentic_test_case_generator/pull/460)).
P3d connects the second named approval, budget and Review recovery (commit
`2864d34`, [PR #462](https://github.com/lumensparkxy/agentic_test_case_generator/pull/462)).
P3e shares provider admission with saved-suite assessment under
[#463](https://github.com/lumensparkxy/agentic_test_case_generator/issues/463).
Next is enrolling Project Sync and the remaining foreground draft producers
before any hosted activation.
The production queue identity and timeout policy remain
unverified. Live provider pairs require a bounded budget and approved
environment. Independent practitioner validation stays with
[#354](https://github.com/lumensparkxy/agentic_test_case_generator/issues/354).
[#251](https://github.com/lumensparkxy/agentic_test_case_generator/issues/251)
retains live journey and source-change acceptance,
[#280](https://github.com/lumensparkxy/agentic_test_case_generator/issues/280)
and [#274](https://github.com/lumensparkxy/agentic_test_case_generator/issues/274)
retain the held guidance decisions, and
[#318](https://github.com/lumensparkxy/agentic_test_case_generator/issues/318)
retains the embedded-browser diagnosis. Repository defaults use Gemini 3.8
Flash with guidance skills and memory off. The baseline does not establish
deployed settings.
