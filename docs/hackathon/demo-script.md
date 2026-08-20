# Four-Minute Demo Script

Target runtime: 3:45–3:55. Record in English at 1440×900 with a clean browser
profile, bookmarks and notifications hidden, and no personal email, tokens,
secrets, billing data, or unrelated cloud projects visible.

## Prepare off camera

1. Deploy the final protected-main revision and confirm both Cloud Run services
   are healthy.
2. Create a production project named **Self-Test Demo** from
   [`demo-requirements.md`](demo-requirements.md).
3. Parse and approve both synthetic requirements, generate the suite, and
   pre-vet one executable case against the public application.
4. Keep three tabs ready: the live app, the architecture diagram, and the Cloud
   Run service list/log view. Never open an environment-variables or secrets
   view while recording.
5. Warm both Cloud Run services immediately before the take.

## 0:00–0:16 — Friction and promise

**Screen:** Live Home page with the custom domain visible. Open **Self-Test
Demo**.

> Test teams lose hours translating changing requirements into coverage, then
> into automation. Test Engineer Agent turns that chain into traceable evidence
> and runs the safe browser subset using Google ADK and Gemini 3.5 Flash on
> Google Cloud.

## 0:16–0:38 — Real source and durable workflow

**Screen:** Project overview, then the two source-linked requirements and their
review state.

> This is the live production app using two synthetic requirements that test
> its own public sign-in flow. ADK runs extraction, review, and refinement
> through SequentialAgent and LoopAgent workflows. The agent handles analysis;
> a human keeps authority at approval and execution boundaries. The project
> state shown here is persisted, not mocked.

## 0:38–1:18 — Coverage, traceability, and diagnostics

**Screen:** Move through **Use Cases**, **Generated Test Cases**,
**Traceability Matrix**, **Scenario Coverage**, and **Diagnostics**.

> Every generated case carries requirement and scenario IDs. This run produced
> [X] cases covering [Y of Z] requirements and [A of B] must-have scenarios.
> Diagnostics expose parser recovery, retries, and fallback use instead of
> hiding uncertainty.

Replace the bracketed values only with metrics visible in the final take. Do
not claim zero fallbacks unless the screen proves it.

## 1:18–2:16 — Safe action, live

**Screen:** In **Automation**, set the target to the public application, choose
**Preview Execution**, show all four readiness buckets, select the pre-vetted
case, and choose **Run 1 Candidate**. Keep the real result and run ID visible.

> Now it takes action. Preview first classifies every case as executable,
> manual, unsupported, or invalid. I am approving one generated case to test
> the agent's own public landing page. The backend compiles bounded plain-English
> steps into an intermediate representation and a Playwright specification,
> then runs the selected candidate in its isolated Node runtime. The real result
> is [passed or failed], with the run ID and evidence visible. Unsupported
> behavior is surfaced; it is never guessed.

If the live check fails, narrate the real failure. Fix the cause and record a
fresh take instead of splicing a different result into the run.

## 2:16–2:42 — Report evidence

**Screen:** Show the execution report, environment, counts, traceability, and
CSV/Excel/JSON export controls.

> The report ties evidence to the project revision, environment, and run.
> Approved suites export as CSV, Excel, or JSON. A failed quality gate requires
> an explicit, reasoned override.

## 2:42–3:12 — Architecture

**Screen:** Display the architecture diagram full-screen.

> React and FastAPI run as separate Cloud Run services. Google ADK orchestrates
> requirements, use-case, and test-design review loops on Gemini 3.5 Flash.
> Automation drafting uses the Google Gen AI SDK outside the ADK boundary, and
> deterministic services control what reaches Playwright. Firestore preserves
> project state, reviews, checkpoints, and run metadata; Firebase protects
> access.

## 3:12–3:36 — Google Cloud proof

**Screen:** Cloud Run service list, then backend details or logs. Show service
names, region, green status, current revision, 100% traffic, and a request from
the live run. Do not expose environment values or secrets.

> This is the actual Google Cloud deployment: frontend and backend services,
> one hundred percent traffic on the shown revisions, and Cloud Logging for the
> request we just made.

## 3:36–3:52 — Honest close

**Screen:** Return to the execution evidence.

> Browser execution uses a deliberately bounded grammar. Complex desktop,
> performance, SAP, or ambiguous actions remain manual or unsupported. Test
> Engineer Agent delivers trustworthy autonomy: less manual design, visible
> coverage, and executable evidence, while the test engineer retains release
> authority.

## Final recording checklist

- Keep the public YouTube or Vimeo video at or below four minutes.
- Use an unedited live agent action and result; do not use mocked routes or
  speed-up footage.
- Use only the synthetic fixture and public application.
- Blur or crop the account identity in every application shot.
- Show the final Cloud Run revision and real request logs without opening
  environment or secret panels.
- Leave the published video unchanged after the submission deadline until
  judging ends.
- Avoid claims of full autonomy, universal executability, perfect coverage,
  persistent ADK memory, Vertex AI, bonus models, or contest-period authorship
  of the pre-existing platform.
