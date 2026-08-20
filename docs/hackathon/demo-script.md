# Four-Minute Demo Script

Target runtime: 3:45–3:55. Record in English in one continuous application
take where possible. Use a small, pre-benchmarked fixture so the real workflow
finishes reliably.

## 0:00–0:20 — Friction and promise

**Screen:** Test Engineer Agent Home, then the two- or three-requirement input.

**Narration:**

> A small requirement change can trigger hours of QA coordination: interpreting
> intent, finding risk, planning coverage, updating tests, running them, and
> proving what happened. Test Engineer Agent turns that chain into traceable
> evidence while the test engineer keeps release control.

## 0:20–0:55 — Real input and ADK extraction

**Screen:** Upload the fixture and run requirement parsing. Keep the app action
and visible progress in the same take.

**Narration:**

> The source can be Markdown, Word, Excel, Jira, or Azure DevOps. Google ADK
> orchestrates an extractor and a bounded reviewer-refiner loop. Structured
> outputs retain the source excerpt and quality diagnostics instead of returning
> an ungrounded list.

## 0:55–1:20 — Human authority

**Screen:** Inspect one source-linked requirement and approve the set.

**Narration:**

> Machine review and human approval are deliberately different states. The
> agent may recommend, but it cannot approve release evidence on behalf of the
> test engineer.

## 1:20–2:05 — Analysis, coverage, and critique

**Screen:** Generate the small suite. While it runs, briefly show the
architecture diagram; return to the completed Analysis and Coverage views.

**Narration:**

> ADK analysis, coverage-planning, generation, validation, and refinement agents
> share structured session state. The output is not just test-case prose: every
> case links back to requirements and planned scenarios, and missing coverage or
> malformed model output stays visible in diagnostics.

## 2:05–2:45 — Safe action

**Screen:** Open Automation, preview execution, and show the four candidate
buckets. Run one or two supported candidates.

**Narration:**

> Before action, deterministic services classify every case as executable,
> manual, unsupported, or invalid. Supported cases pass through a structured
> intermediate representation and Playwright compiler. The agent never invents
> executable certainty for an unsupported instruction.

## 2:45–3:15 — Evidence and honest failure handling

**Screen:** Show the run result, traceability/report evidence, and one deliberate
failure or unsupported case if available.

**Narration:**

> A pass becomes durable run evidence. A failure or unsupported case becomes a
> review signal; it does not silently rewrite an approved requirement. Firestore
> preserves snapshots, review decisions, checkpoints, runs, and reports across
> sessions.

## 3:15–3:40 — Google Cloud proof

**Screen:** Show the Cloud Run services `tcg-frontend` and `tcg-backend`, their
ready revisions, and the backend `/health` response. Do not expose secrets,
tokens, account email, billing data, or unrelated projects.

**Narration:**

> The React frontend and FastAPI backend run as separate Google Cloud Run
> services. Gemini 3.5 Flash is accessed through the Gemini API, and Firestore
> supplies product-level persistence outside ADK's per-run in-memory sessions.

## 3:40–3:55 — Close

**Screen:** Return to the evidence chain or architecture diagram.

**Narration:**

> Test Engineer Agent is an evidence-preserving QA control loop: ADK reasons,
> deterministic systems act, and humans remain accountable. From messy
> requirements to executable evidence—before the next release.

## Recording checklist

- Keep the published video at or below four minutes.
- Use English narration or accurate English subtitles.
- Show a real agent action and its result, not only static screenshots.
- Show Google Cloud proof without revealing personal or secret information.
- Use sanitized fixtures and a clean browser profile.
- Avoid claims of full autonomy, persistent ADK memory, universal accuracy, or
  contest-period authorship of the pre-existing platform.
- Leave the published video unchanged after the deadline until judging ends.

