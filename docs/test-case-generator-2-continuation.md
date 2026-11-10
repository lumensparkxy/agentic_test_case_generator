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

The opted-in demo still defers paid work from the unenrolled foreground
scenario, suite, refinement, impact and generated-report producers.
Deterministic impact analysis, execution preview and deliberate browser
runs keep their existing admission. Source intake uses its existing
extraction policy. Legacy and standalone paths keep their existing
behavior outside the demo.

### Bounded Project Sync refresh

An accepted correction saves revised sources and marks the prior drafts
stale. The initial acceptance can then start bounded scoped refresh. A busy
project keeps the accepted correction and previous artifacts while it waits
for a deliberate resume. Replaying the acceptance reads the saved change
without starting more provider work.

The two draft stages take the same project fence in turn. They reserve at
most seven and 24 provider requests respectively and bind the exact
corrected sources, scoped change, prior checkpoint, settings and revision.
The Test Cases stage verifies the committed scenario draft before starting.
A pre-dispatch restart keeps the canonical stage request. An uncertain
dispatch requires exact owner acknowledgement followed by a separate
retry. Status checks perform reads. Source changes revoke publication
authority, and the original artifact history stays accessible.

The Test Cases draft, preserved and updated version references, usage
event, application billing and completion receipt commit together. The
exact committed result remains replayable after acknowledgement loss.
Refreshed drafts remain provisional pending the existing reviews. Optional
POM generation stays deferred and the existing report is preserved.

A frontend rollback can hide the new actions while retaining server
fences. Drain or explicitly resolve local draft attempts before changing
the backend demo flag. Enrolled Sync records stay protected from the
legacy refresh loop. Hosted activation still needs a verified queue,
uniform provider admission and an authorized deployment.

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

### Exact suite design evidence

Project Detail exposes `suite_quality` for its saved Test Cases snapshot.
It reports source freshness, independent design status, the exact human
scenario decision, any human suite acceptance, and declared setup
separately. The scoped required, deferred and excluded requirement IDs
remain visible. Declared setup comes from the saved cases. Target
readiness and run results use their existing evidence and admission.

Before legacy combined assessment, the server records whether the exact
delivered cases passed structural review. It checks that the independent
assessor saw those ordered cases and the same applied source inputs. The
binding preserves literal data and expected outcomes. A saved proof binds the immutable suite and case versions to the canonical
approved scope, current exact scenario approval, applied Context and
independent assessment. A saved reassessment can add new evidence while
preserving the case content and versions. Sparse or malformed legacy
evidence stays unknown. Drafts and source changes cannot inherit current
proof from a different suite.

The proof and projection leave legacy approval, repair and execution
admission in force. An independently complete design can show declared
setup as blocked.

### Explicit reviewed-design export

`TCG_DESIGN_DELIVERY_ENABLED=true` enables the server capability for a new
file export request. The capability is off by default and projected in
Project Detail. An explicit `delivery_intent: reviewed_design` request
binds CSV, Excel or JSON output to the exact current suite and revision.
It requires verified source freshness, complete independent design, the
exact human-approved scenario plan and concrete saved cases.

The file and report classify the delivery as a reviewed design. The
legacy combined approval remains separate. The evidence carries exact
case versions, source mappings, approved scope and deferred or excluded
requirements. Historical cases stay in the file while the coverage
denominator uses the active design. Declared setup, human acceptance and
actual run outcomes remain separate. Target readiness remains unknown
in the file until a matching target check can supply authoritative
evidence. Preview and Run continue to use their existing admission.

The existing reasoned draft export remains available. Legacy file and
audit layouts keep their original receipt fingerprints. An exact
committed design report can be recovered while new design delivery is
disabled, subject to the existing current-evidence checks.

### Selected repair in Review

The local shared-admission demo can offer **Add missing check** in the
current saved case reader. The server binds the offer to the current
suite, case version, approved scenario and exact cited source obligation.
It accepts one additional read-only observation with an independently
verified expected result. The preceding steps and data, sibling case
rows and sibling version identities stay exact. Setup blockers and
recorded application defects retain their separate paths.

The owner can check or release the exact attempt while a request is
running or interrupted. The same request ID recovers a committed
result. Read-only before and after links use the original suite and
case versions. Repair reserves at most eight provider requests with a
180-second deadline. Publication, the usage event, billing and the
receipt use the same source and lease fence. The combined suite remains
pending until the user invokes **Review saved suite**. Neither action
starts browser execution.

### Scoped scenario refresh after source revision

Review routes a verified stale predecessor to **Review refresh scope**
after the revised requirements and current Context are ready. The scope
dialog marks changed and added groups, lists removed groups, and lets
the reviewer include other groups affected by Context. The existing
approved plan and suite remain readable throughout the attempt.

The loopback demo binds the exact predecessor plan, human review, suite,
and current sources to the common provider fence. The confirmed selection
can refresh only the approved groups in scope. Removal-only refresh keeps
all remaining groups. The provider cap is seven requests with a
240-second deadline. A saved response remains recoverable by its request
ID. A running or interrupted attempt can be released by its owner.

The new scenario plan remains pending for a fresh explicit human review.
The current test suite and its prior execution and delivery evidence stay
historical and stale. Scoped Test Cases application is a separate action.

### Scoped application of directly affected tests

Review uses deterministic impact analysis after the exact refreshed plan
receives its ordinary approval. The loopback demo asks the reviewer to
select every verified direct update, up to six existing cases. Optional
semantic neighbors remain visible for review. A partial direct selection
cannot publish a current suite with older affected assertions.

The application uses one shared lease and up to 24 physical provider
requests across its targeted writers and independent checks, with a
300-second deadline. The receipt binds the revision, sources and selection.
The owner can release a running or interrupted attempt. A new explicit
attempt with the same compatible scope can reuse validated completed
checkpoints. Publication writes one selected successor per changed case,
the unchanged raw rows, usage and billing atomically.

The saved result links the previous and revised cases by exact version.
The separate **Review saved suite** action can assess the combined suite
against its recorded approved sources. File delivery and browser execution
retain their own gates and deliberate actions.

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
screenshots. The eight-action path remained above the seven-action threshold.

At P5a, the paired replay took **7 actions** and four workbench transitions,
a **36.4% reduction** from P0. Choosing TC-202 directly from the completed
result opened the editable reader and focused the case. Both approvals
remained explicit. The four frozen content hashes matched. The mock suite
envelope includes its project ID to reflect the existing server contract.
The separate JSON download matched both saved cases, including literal
test data. The scripted core span was 5.916 seconds and the seven action
blocks took 0.764 seconds. These timings include fixture and automation
work. Practitioner time and live waiting require separate evidence.

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
P3e shares provider admission with saved-suite assessment (commit
`4a76f0c`, [PR #464](https://github.com/lumensparkxy/agentic_test_case_generator/pull/464)).
P3f enrolls bounded Project Sync refresh (commit `dbeda56`,
[PR #466](https://github.com/lumensparkxy/agentic_test_case_generator/pull/466)).

The current local process replay for
[#457](https://github.com/lumensparkxy/agentic_test_case_generator/issues/457)
and [#459](https://github.com/lumensparkxy/agentic_test_case_generator/issues/459)
ran both stages through the worker CLI at `bb500a6` with a synthetic
provider transport. Separate processes stopped after claim, at first
provider dispatch, before publication and after publication for each
stage. After natural lease expiry, fresh workers verified fenced resume,
explicit interruption or exact published receipt as appropriate.
The [release record](test-case-generator-2-release.md) records the local
outcome and the remaining hosted gate.

P4a binds independent design evidence to the exact saved suite (commit
`1be782c`, [PR #468](https://github.com/lumensparkxy/agentic_test_case_generator/pull/468)).
P4b adds explicit reviewed-design file delivery (commit `2ee40a4`,
[PR #470](https://github.com/lumensparkxy/agentic_test_case_generator/pull/470)).
P4c exposes exact suite quality, reviewed-design delivery eligibility and
saved report classification in Review, Deliver and project status (commit
`fb6a06d`, [PR #472](https://github.com/lumensparkxy/agentic_test_case_generator/pull/472)).
The browser fixture exports an exact manual design while browser setup
is blocked. Unknown, stale and legacy evidence retains its existing
export gate. Exact historical case and run links remain available. P7
still owns opening the exact historical file report from its index row.
P5a opens priority cases from the exact completed suite (commit `2df24f2`,
[PR #474](https://github.com/lumensparkxy/agentic_test_case_generator/pull/474)).
The desktop browser verifies a 200-case suite, saved filters, keyboard
focus, Back and Forward, stale/replaced suites, and existing case links.
P5b adds the bounded one-case repair and exact saved comparison (commit
`d63d5a4`, [PR #476](https://github.com/lumensparkxy/agentic_test_case_generator/pull/476)).
The loopback fixture proves one selected successor and charge, exact
historical readback, lost acknowledgement recovery, source and cancellation
fences, and explicit whole-suite reassessment. P5c1 adds bounded scoped
scenario refresh (commit `d3b555a`,
[PR #478](https://github.com/lumensparkxy/agentic_test_case_generator/pull/478)).
The 8→6 synthetic replay refreshes two groups, retains seven exactly,
and preserves the 14 prior cases. It keeps the new plan's human approval
explicit. P5c2 adds bounded impact application (commit `b9eef81`,
[PR #480](https://github.com/lumensparkxy/agentic_test_case_generator/pull/480)).
The loopback Booking fixture replaces six direct cases, preserves eight
exact rows and versions, and reuses a completed checkpoint after release.
It verifies billing rollback, the physical request cap and exact receipt
recovery. The separate assessment confirms exact current source lineage
and leaves reviewed-design export unavailable because the fixture Context
does not supply the response-loss test mechanism needed by TC-013.
P5d binds saved-suite assessment to an exact Context-only Project Sync
draft (commit `acc6dfd`, [PR #482](https://github.com/lumensparkxy/agentic_test_case_generator/pull/482)).
After separate approval of the matching plan, Review can assess the exact
saved cases with the accepted project decision. Reassessment retains
unchanged rows and versions, including explicit null values. Material
requirement corrections and unverified drafts cannot use this admission.
A saved assessment remains readable after the demo flag is off. P5e
rebinds verified material Sync drafts after approval of the exact corrected
requirements (commit `4af2f8f`, [PR #484](https://github.com/lumensparkxy/agentic_test_case_generator/pull/484)).
The reviewer confirms the deterministic rebind before approving the new
scenario snapshot. The original drafts and versions remain readable.
Rebind publishes both successors and one receipt in the same transaction
and uses no provider. A separate saved review uses the accepted decision
and verifies both the rebind and original stage receipts. P5f groups exact
saved findings by requirement and behavior (head `117ede9`,
[PR #486](https://github.com/lumensparkxy/agentic_test_case_generator/pull/486)).
Separate source quotes and case steps stay visible. Verified citations
open and focus the exact saved step. Unverified evidence, structural
blockers, setup and classified project findings retain distinct paths.

Unenrolled paid draft producers remain a release gate.
P6 error analysis found prior Booking gaps covered by existing regression
checks and no verified new prompt change. The credential-free W05/W01
rehearsal completed four arms at baseline `2f6458d` and candidate `b9eef81`.
Both pairs matched exact saved case content and JSON projection. W05
retained the deferred requirement and unavailable Supervisor setup.
Candidate worker reservations stayed at 7 and 24 requests per arm. A
total of 12 SDK mock requests used authored specialist responses. This
measures the route, persistence and export seams. A second rehearsal
ran the actual native W05 and W01 planners, writers and critics through
the SDK. Both pairs matched the exact authored case projection and source
bindings. A dry SQLite broker recorded 26 synthetic requests across four
arms. It exercised pair reservation and restart handling without provider
egress or credentials. Live model outcomes and independent adjudication
remain open. Controlled local Deliver rehearsal at `acc6dfd` also ran
one selected authored case against healthy and seeded-fault targets
through the rendered app and actual Chromium, inspected its exact saved
case link, and kept Run disabled for zero candidates. That proof is
separate from model and practitioner gates.

P7a opens exact saved file reports (head `54f50cd`,
[PR #488](https://github.com/lumensparkxy/agentic_test_case_generator/pull/488)).
Deliver, Reports and Home link to the selected immutable file report.
Both `/deliver?report=ID` and `/reports?report=ID` resolve the same evidence.
The owner can inspect recorded decisions and original case versions after
a newer export, a source edit, archive or a client rollback. The saved
record retains audit evidence. Exact current file replay remains an export
operation with its existing policy. Older sparse reports retain their
recorded header. P7b exposes current declared browser prerequisites and
exact failed-run findings from Deliver (commit `d3fd7f8`,
[PR #490](https://github.com/lumensparkxy/agentic_test_case_generator/pull/490)).
A deliberate finding read keeps the recorded run, source suite and case
assertions visible. Current file and execution gates stay separate.
Rendered Chromium against the real local API opened the one finding from
the exact failed run without changing its classification.

The current frozen mocked desktop replay still takes seven deliberate
actions versus eleven. It adds a mock response for the new read-only
repair status and preserves all four content hashes, both approvals and
the inspected export. A fresh W01 rendered run with synthetic model
responses also verified all eleven downloaded cases against exact case
reads. It exposed a modal race when the named approval receipt and
suite finished before the original POST response. The saved decision
was correct. P7c reconciles that exact saved approval (commit `e94ce21`,
[PR #492](https://github.com/lumensparkxy/agentic_test_case_generator/pull/492)).
In the deliberately delayed actual W01 run, the dialog closed and
restored focus before the HTTP acknowledgement. It used seven expected
browser POSTs, six native SDK mock requests, both explicit approvals,
and eleven exact saved and exported cases. A later decision draft stayed
open when the old response arrived in the separate mocked test.

P8 records the [local release and rollback handoff](test-case-generator-2-release.md)
under [#493](https://github.com/lumensparkxy/agentic_test_case_generator/issues/493).
The final application pin `e94ce21` used the same disposable project and
Chromium session across enabled, flag-off and restored builds. Both aliases
preserved exact report, run, case and failed-run Context evidence. Twelve
canonical evidence digests matched, with no API writes from the browser.
All project records matched. The only database changes were two timestamps
from existing entitlement reads. The release record includes admission,
drain and rollback boundaries.

P6 blind review preparation under
[#495](https://github.com/lumensparkxy/agentic_test_case_generator/issues/495)
adds an offline intake and reviewer packet compiler. It checks ordered exact
saved and exported cases, per-case versions, source and approval lineage,
and the saved file report. A private ledger retains all 72 scheduled slots.
The W01/W05 native rehearsal produced four arm-neutral packets and retained
68 unrun slots. The label importer binds judgments and citations to those
exact packets and keeps unknown obligations in scope. The live cohort and
independent judgments remain empty.

The intake also checks saved file source digests and exact cited lines
against the frozen documents before creating reviewer packets. It keeps
original and revised W11 evidence separate, including when only one
requirement changed. A mismatched capture stays in the private ledger
without a reviewer packet.

The paired business coverage summary keeps all 36 pair identities,
calculates deltas only for two labeled arms and distinguishes rehearsals
and incomplete cohorts from the full-cohort calculation. Unknown
in-scope obligations count against coverage. One-arm coverage and critical
misses stay visible per repeat, with workflow and pooled totals.

P6 corpus input rehearsal under
[#497](https://github.com/lumensparkxy/agentic_test_case_generator/issues/497)
exercised all twelve frozen workflows on the baseline and candidate
`dd6e55e`. Thirteen source and Context checkpoints matched across the two
pins. The candidate recovered the exact cancelled continuation after
each explicit approval. W11 recovered the original 8-attendee requirement,
preserved the unaffected requirement decisions, and saved the revised
6-attendee source and Context. Provider attempts were zero. The native
W01/W05 smoke remains the separate scenario-through-export rehearsal.

P6 structural review under
[#499](https://github.com/lumensparkxy/agentic_test_case_generator/issues/499)
requires exact rendered whitespace evidence for structured step-data cases.
An exact scoped result after input must match a current prior field value.
The frozen baseline approved two authored defective controls, one without a
rendered check and one expecting a different literal. The fixed review
rejected both and kept the valid exact control. Chromium confirmed that
only the valid control passed healthy and detected the seeded whitespace
collapse at the intended assertion. The authored comparison sits outside
the frozen live corpus and keeps independent adjudication open.

P6 Booking fault attribution under
[#501](https://github.com/lumensparkxy/agentic_test_case_generator/issues/501)
binds rejected-write count evidence to the selected case's terminal report
and its post-reopen check. The old matcher could also credit an identical
count failure during setup. The archived 8- and 6-attendee suites each
passed eleven eligible cases on their matching local target. The revised
suite matched all fourteen exported case versions. Eight unaffected rows
kept their exact versions. The archive contains no successful original
export. The historical fault replay is separate from the frozen paired live
cohort. Earlier assertion failures leave downstream checks unobserved.

The next externally gated actions are an authorized W05/W01 live smoke,
independent practitioner validation, and verification of a hosted worker
identity and timeout policy. Live provider pairs require a bounded budget
and approved environment. Independent practitioner validation stays with
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
