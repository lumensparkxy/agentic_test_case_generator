# ADK skills and approved knowledge

Epic #269; stories #270–#274. Curated skills teach methods; approved knowledge stores product guidance. Current sources and explicit review decisions remain authoritative.

## Rollout
Both `ADK_SKILLS_ENABLED` and `ADK_MEMORY_ENABLED` default to false. `ADK_GUIDANCE_STAGES` limits enabled entry points. A held Test Cases entry point does not inherit guidance through its internal Use Cases planner. No historic feedback is imported. Four-arm evaluation is required before enabling a stage in production. The current local pilot enables both flags for `requirements,use_cases`; Test Cases and Automation are held on #280 and #281. See the rollout report for measured results and limitations.

## Run contract
One immutable manifest records model, skill versions/hashes, selected memory revisions, omissions and explicit bypass. Skill packages are loaded through ADK's loader before generation, without extra model tool calls. Parallel workers inherit the same manifest; JSON schemas and base guardrails are unchanged. Knowledge context is capped at 12 entries / 6000 characters in deterministic order.

## Delivery checklist
- #270: foundations and skill packages
- #271: durable repository, approvals and personal selections
- #272: shared management and provenance UI
- #273: generation and successful-feedback integration
- #274: validation, evaluation and rollout evidence

## Durable lifecycle (#271)
`GET /skills`, `GET/POST /projects/{id}/knowledge`, and `GET/POST /me/knowledge` expose the approved lifecycle. POST requires `X-Request-ID` and an action, entry ID/base revision where applicable, and draft content. An action accepts propose, revise, approve, dismiss, retire, delete, promote, select or unselect. Personal facts/requirement links are rejected; business facts require current source requirement links. Promotion creates an inactive personal proposal requiring separate approval.

Firestore stores bounded, owner-isolated knowledge state in `agent_knowledge`, accessed only through authenticated backend services. A transaction checks project ownership, optimistic entry revision and a separate idempotency receipt before committing. Reusing an ID with different input is a conflict. Receipts contain identifiers, not guidance text. Deletion purges version text and selections; artifact provenance uses IDs/hashes, and missing historical content is shown as unavailable. Required links are hashed; changed/missing sources cause effective `needs_confirmation` status and retrieval exclusion.

The read-only ADK adapter accepts only the frozen run manifest and owner. Session ingestion is rejected. Selected personal records remain owned by that user and are never automatically included in other projects. Source-based exclusion and retrieval order are deterministic.

## Product UI (#272)
Context includes Project knowledge using the shared table, field, badge and dialog primitives. Filters distinguish active, suggested and needs-confirmation records. Approving knowledge is a separate dialog from artifact review; editing an active entry displays both its active wording and proposed replacement. Failed writes retain the dialog and request ID for retry. Personal selection is explicit and reversible. Settings has a Personal knowledge tab for approval, revision, retirement and deletion. The Guidance used component shows exact recorded references and resolves historical wording through the version endpoint; artifact integration is detailed below in #273.

## Generation integration (#273)
All authenticated generation entry points resolve guidance before billing or model calls: requirements parse/refine, Jira/Azure imports, test-case generation/refinement, and Playwright code generation. The specialist dispatcher resolves a manifest per task; the existing frontend orchestrated actions use the same HTTP entry points. Worker submissions copy the run context; refiners reuse the same manifest. A combined test-case run selects Use Cases and Test Cases methods, then injects only the applicable stage into each agent role. Mandatory JSON contracts and tools remain in base instructions.

For enabled project memory, `agent_guidance_runs` stores an immutable manifest of references under an owner/project/request/stage hash. Retrying the same request restores the original versions; changed input with that ID returns 409. Deleting a referenced memory prevents a saved run from resuming with different content. Start a new run or explicitly bypass memory. In-process iterations never reload knowledge. ADK Runner receives the read-only memory adapter; full sessions are never ingested. Configured model and skill/memory references are returned on responses and recorded on project snapshots. Model fallback diagnostics remain separate from the supplied-input manifest.

HTTP 503 `knowledge_unavailable` occurs before generation when enabled memory cannot load. The shared dialog offers Retry, explicit Run without remembered guidance (`X-Knowledge-Bypass: true`), or Cancel. Retry preserves request identity. Leaving the project or signing out cancels a pending choice. Explicit bypass is recorded. Skills remain enabled when memory is bypassed.

Requirements and Test Cases refinement feedback, and Use Cases review comments, propose inactive knowledge only after the operation succeeds. Suggestions conservatively retain the human wording; there is no hidden summarizer or automatic approval. Feedback too long or containing recognizable secrets requires a concise manual proposal. A failed suggestion returns a retry payload without undoing the completed review; `/projects/{id}/knowledge/suggestions/retry` checks the completed source request and reuses its idempotency key. Artifact approval alone does not create knowledge.

Actual pages display Guidance used with original revisions, omissions, deletion notices and newer-guidance notices. Existing approvals are untouched. Automation execution preview is a deterministic specification validator, not an AI call; its interface identifies source test-case guidance separately. AI Playwright code-generation responses and project snapshots record their own automation manifest. Older/unrecorded snapshots remain explicitly unrecorded.

Validation and live quality comparisons are recorded in `docs/guidance-rollout-report.md`. Offline checks alone do not establish improved model quality. The Context skill catalog displays the backend-reported on/off state of both flags per stage, with an unavailable state when older responses omit that information.

## Evaluation and staged enablement (#274)

Run contract checks without model calls:

```bash
.venv/bin/python scripts/evaluate_guidance.py --output /tmp/guidance-contracts.json
```

After the configured Gemini service is accessible, run each stage with three repeats on the two synthetic fixtures. Keep identical source inputs, model, workflow limits and frozen lesson revision in every arm. Rotate arm ordering between repeats. No project data is written, and the harness does not enable flags. Provide verified input/output token rates with `--input-usd-per-million` and `--output-usd-per-million` to estimate cost; missing prices remain null.

```bash
.venv/bin/python scripts/evaluate_guidance.py --live --stage use_cases --repeats 3 --output /tmp/guidance-use-cases.json
```

An independent reviewer inspects the saved outputs with the source requirements and approved lesson. Score coverage as the fraction of applicable source behaviors covered; count assertions/business rules with no supporting source as unsupported assumptions; count violations of the explicit reviewer lesson as repeated reviewer corrections. Do not use the generating agent's self-rating as these measurements. Record notes with disputed cases. Review JSON maps each sample ID to `output_hash`, `reviewer`, `coverage` (0–1), `unsupported_assumptions` and `repeated_reviewer_corrections` (nonnegative integers). Missing measurements must remain null.

Attach review to the exact recorded output without generating again:

```bash
.venv/bin/python scripts/evaluate_guidance.py --review-report /tmp/guidance-use-cases.json --reviews /tmp/guidance-review.json --output /tmp/guidance-use-cases-reviewed.json
```

The report compares every arm's coverage, assumptions, repeated corrections, latency and estimated cost. Output hashes prevent attaching an old judgment to a newly generated result. Live runs checkpoint each completed sample. Record prompt/completion token totals (including thinking output), API failures and fallback runs. Fallbacks cannot demonstrate model-quality improvement. Delivered Test Cases must agree with final generation counts and requirement coverage; Automation must contain complete, syntactically valid Python test functions. The evaluator parses/compiles code as data and never executes it. Passing these checks does not establish selector grounding. The initial corpus is a smoke benchmark, not evidence for every domain.

Before enabling any stage, require all correctness gates, complete paired measurements, and useful improvement in coverage or repeated corrections without increased unsupported assumptions. Report any latency/cost increase explicitly in the rollout PR and justify acceptance. Expand fixtures for the target domain before a broad release. Enable skills and memory independently for approved stages; rollback by disabling the relevant flag. Do not rewrite approved records or existing artifact decisions during rollout. This release starts with no imported historical lessons or personal selections.

See `docs/guidance-rollout-report.md` for actual measured/blocked status; infrastructure readiness is separate from model-quality evidence. ADK marks [skills experimental](https://adk.dev/skills/), so loader/instruction injection stays behind the adapter. ADK's [memory service](https://adk.dev/sessions/memory/) remains separate from session state; our adapter cannot ingest sessions or approve records.
