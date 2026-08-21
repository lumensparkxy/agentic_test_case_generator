# All Things Agentic Hackathon Submission Package

This file is the reviewed source copy for the Test Engineer Agent Devpost entry.
It deliberately separates verified product claims from the eligibility
disclosure required for the pre-existing codebase.

## Project name

Test Engineer Agent

## Category

Collaborative Partner

## Elevator pitch

An ADK-powered QA teammate that turns messy requirements into traceable test
plans, executable Playwright checks, and auditable evidence—while humans retain
release control.

## Project story

# Test Engineer Agent

Software teams do not lack test-case text. They lack a trustworthy path from
fragmented requirements to release evidence.

Requirements arrive in documents, Jira issues, Azure DevOps work items, and
product references. Test engineers must normalize them, identify risks and edge
cases, maintain traceability, write automation, execute it, and repeat that work
whenever requirements change. A chatbot that returns plausible test cases
solves only a small part of that workflow.

Test Engineer Agent is a policy-bounded AI QA teammate. It performs the
repetitive analysis, planning, generation, and execution work between explicit
human approval gates, while the test engineer remains accountable for release
decisions.

## What it does

Test Engineer Agent provides a guided, stateful QA workflow:

1. Imports requirements from Markdown, Word, Excel, Jira Cloud, or Azure DevOps.
2. Extracts testable requirements while preserving source context.
3. Lets a reviewer approve, reject, refine, or provide feedback.
4. Grounds public product and documentation links into UI, API, and workflow facts.
5. Analyzes business rules, constraints, permissions, transitions, dependencies, and risks.
6. Creates a requirement-linked scenario coverage plan.
7. Generates structured test cases using the team's selected template.
8. Validates and refines the suite against configurable quality gates.
9. Shows traceability, coverage, analysis, and workflow diagnostics.
10. Classifies cases as executable, manual, unsupported, or invalid.
11. Compiles supported browser scenarios into Playwright tests and executes selected candidates.
12. Preserves project snapshots, execution history, checkpoints, and report evidence.
13. Exports test artifacts as CSV, Excel, or JSON.

When requirements change, impact analysis identifies affected requirements,
scenarios, and tests so unchanged evidence does not have to be discarded.

## Why it is a Collaborative Partner

The agent leads the user through a structured QA process, presents the next
meaningful action, explains blockers, captures explicit feedback, and adapts
generated artifacts to that feedback. Durable project snapshots and review
decisions preserve the workflow across sessions. Machine quality review and
human approval remain separate, so a model cannot approve its own work on
behalf of a test engineer.

The system actively transforms messy source material through a connected
evidence chain:

`requirement source → testable requirement → risk analysis → planned scenario → test case → executable candidate → run result → report evidence`

## How Google ADK is used

Google ADK is the orchestration backbone for the reasoning-intensive stages.

The requirements workflow combines an Initial Extractor Agent, Reviewer Agent,
Refiner Agent, and a bounded ADK `LoopAgent` that exits only when quality
conditions are satisfied. The test-design workflow combines Requirement
Analysis, Coverage Planner, Test Case Generator, Validator, and Test Case
Refiner agents through `SequentialAgent` and `LoopAgent` orchestration.

ADK session state passes requirements, analysis, coverage plans, test cases,
and validation feedback between specialists. `Runner` event streams provide
agent-level output and failure diagnostics. `ToolContext` supplies a controlled
exit action when review thresholds are met. Larger workloads can be divided
into bounded shards, while deterministic coordination restores order, remaps
conflicting identifiers, merges evidence, and applies suite-level validation.

ADK is therefore not a wrapper around one prompt. It supplies the compositional
workflow, shared state, event stream, and critique loops. Deterministic
application services handle operations that require reproducibility.

## The agentic/deterministic boundary

Agents handle ambiguous intent, risk, coverage, and test design. Pydantic
contracts, JSON schemas, parsers, deterministic fallbacks, a structured
intermediate representation, and the Playwright compiler control what becomes
executable.

The system does not silently turn every generated instruction into browser
automation. Missing assertions, unsupported actions, ambiguous selectors, and
non-browser operations remain visible as manual, unsupported, or invalid rather
than becoming misleading tests.

## Architecture and production design

The application combines:

- React and Vite
- FastAPI and Pydantic
- Gemini 3.5 Flash
- Google ADK and Google Gen AI SDK
- Firebase Authentication and Firestore
- Google Cloud Run, Artifact Registry, and Secret Manager
- An isolated Node.js Playwright execution runtime
- Jira Cloud and Azure DevOps adapters
- OpenTelemetry-compatible tracing and Prometheus-compatible metrics

Firestore stores durable QA projects, immutable workflow snapshots, human
review decisions, orchestrator runs, checkpoints, execution records, and report
evidence. ADK's per-run in-memory session state remains separate from this
product-level persistence.

Production authentication accepts Firebase ID tokens. Integration credentials
are encrypted before storage, rotation procedures are documented, and remote
artifact fetching applies SSRF, redirect, content-type, and response-size
controls.

## Failure handling and trust

Agent output is treated as untrusted until it passes validation. The workflow
uses structured contracts, parser recovery diagnostics, configurable timeouts
and iteration limits, stall detection, deterministic completion paths, exact
scenario and requirement references, human approval gates, idempotent retries,
immutable snapshots, and explicit unsupported or invalid execution states.

A failed browser check becomes review evidence; it does not silently rewrite
approved requirements or test cases.

## Challenges and learnings

Preserving source meaning while converting unstructured documents into
testable requirements required retaining source sections, excerpts, hierarchy,
quality flags, and reviewer feedback.

Maintaining traceability through parallel generation required central
coordination to restore coverage-plan order, preserve scenario references, and
prevent duplicate generated identifiers from overwriting cases or results.

The hardest boundary was converting natural-language tests into safe
automation. A structured specification and intermediate representation now
undergo deterministic validation before Playwright code is generated.

The central lesson was that agents are effective at ambiguous analysis and
planning, but reliable QA automation also needs explicit schemas, source
evidence, deterministic compilation, honest unsupported states, and durable
human decisions.

## Accomplishments

- Deployed Cloud Run frontend and backend with Firestore-backed project state.
- 375-test backend regression suite.
- Protected 130-test frontend browser gate.
- Strict offline requirement, generation, and orchestrator evaluations.
- OpenAPI export plus generated frontend contract checks.
- Browser accessibility scans covering serious and critical WCAG A/AA findings without exclusions.
- Reproducible local, container, and Cloud Run deployment workflows.

## What is next

Planned work includes queued background execution for longer workflows, richer
team-level testing-policy memory, broader deterministic assertion support,
stronger execution isolation, and compliance-grade persistence adapters behind
the existing repository boundaries.

## Pre-existing-work disclosure

This submission incorporates substantial pre-existing work.

The repository's first commit is dated February 4, 2026, before the August 3,
2026 submission period. The application architecture, frontend, backend, ADK
workflows, integrations, persistence, Playwright execution framework, tests,
and initial production deployment were substantially developed before the
contest began.

The current main-branch history records an August 18, 2026 dependency
modernization during the submission period. We do not represent the
pre-existing implementation as work newly created during this hackathon.

## Built with

Google ADK, Gemini 3.5 Flash, Google Gen AI SDK, Google Cloud Run, Google Cloud
Firestore, Firebase Authentication, Google Cloud Secret Manager, Google
Artifact Registry, FastAPI, Pydantic, React, Vite, Playwright, OpenAPI,
OpenTelemetry, Prometheus, Jira Cloud, Azure DevOps, Python, JavaScript, Docker,
and GitHub Actions.

## Links

- Hosted application: https://test-engineer-agent.maswadkar.com/
- Source: https://github.com/lumensparkxy/agentic_test_case_generator
- Architecture: ../assets/test-engineer-agent-architecture.png
- Devpost thumbnail: ../assets/test-engineer-agent-devpost-thumbnail.png
- Four-minute demo script: demo-script.md
- Reproducible setup: ../../README.md#setup

## Testing instructions

The hosted application uses Firebase Authentication. Evaluators can use any
provider shown on the sign-in screen with their own account; no invitation,
shared credential, or paid subscription is required.

Suggested evaluation flow:

1. Download the two-item synthetic [`demo-requirements.md`](demo-requirements.md).
2. Sign in, create a QA project, and upload the fixture under Requirements.
3. Review and approve suitable parsed requirements.
4. Optionally ground a public product or documentation URL under Context.
5. Generate and review the Use Cases artifact, then record human approval.
6. Generate test cases and inspect Test Cases, Traceability, Coverage, and Diagnostics.
7. Under Automation, provide a matching public browser target and preview execution.
8. Select executable candidates, run them, and inspect consolidated evidence.
9. Open Reports or Export and create CSV, Excel, or JSON output when approval permits.

Gemini-backed stages can take several minutes. Execution availability depends
on whether generated steps can be represented safely by the supported
deterministic Playwright actions and assertions. Manual and unsupported
classifications are expected safety behavior rather than silent failures.

Complete local and Cloud Run spin-up instructions are in the repository
[`README`](../../README.md#setup), with validation commands in
[`docs/codebase/TESTING.md`](../codebase/TESTING.md).
