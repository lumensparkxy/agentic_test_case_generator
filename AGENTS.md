# Agent Instructions

Applies repository-wide unless a nested `AGENTS.md` adds more specific guidance.

## Choose the work record

- Use a **PR-only path** for small, localized fixes, copy/style adjustments,
  comments, and documentation corrections. The PR records the problem, intended
  result, and validation. A separate issue is optional; link an existing one when
  it already owns the outcome.
- Use an **issue-linked PR** for features, substantial refactors, cross-subsystem
  changes, dependencies, auth/billing, persistence, API contracts, and changes to
  generation, review, or development policy. These categories require an issue
  regardless of diff size.
- Read the selected issue's acceptance criteria and relevant dependencies before
  editing. Issue-linked work must be ready and unblocked. If no issue exists,
  create one when authorized or prepare an issue-ready proposal before coding.
- Issues describe value, observable acceptance criteria, and a test plan. Use
  the existing type, area, priority, status, and active milestone where applicable;
  add parent/dependency links only when relevant. Use native types when available,
  otherwise existing `type:*` labels. Do not create an epic for a small task.
- Group related edits around one outcome. Do not create issues for individual
  implementation steps or unrelated speculative follow-ups.

## Sources and scope

- GitHub issues and PRs are authoritative for current work status.
  `docs/github-issues-backlog.md` holds roadmap context and unfiled proposals;
  historical status notes are snapshots, not a second live queue.
- Read the files and documentation needed for the requested outcome. Expand the
  search when evidence requires it; full codebase mapping is a separate task.
- Preserve unrelated user changes. Keep refactors, documentation, and tests
  within the requested outcome and any linked acceptance criteria.
- Record implementation and validation evidence once in the PR. Add issue
  comments for blockers, decisions, or information absent from the PR; routine
  work does not require mirrored backlog updates or closing summaries.

## Branch and delivery workflow

1. Inspect the working tree, branch, and relevant open work before editing.
   Use an isolated worktree when needed to preserve unrelated work.
2. Branch from current `main`: `codex/<short-slug>` for PR-only work, or
   `codex/issue-<number>-<short-slug>` for issue-linked work. Use another base only
   for an intentional dependency and identify it in the PR.
3. Implement the scoped outcome. Add or update tests for changed behavior and
   documentation for changed interfaces, configuration, or workflows.
4. Run the local checks selected below and inspect the final diff. Record exact
   results and any remaining limitations in the PR; include the issue number in
   commits when applicable. Use `Closes #N` only when acceptance is complete.
5. Open or update the PR. Merge only when requested or already authorized, the
   branch is up to date with `main`, conversations are resolved, and required
   checks pass. Required approvals are zero for this solo-maintained repository.
6. After an authorized merge, verify merged state and ancestry before deleting
   branches; preserve branches needed by open PRs or other worktrees.

`main` is protected: no direct pushes, force pushes, or deletion. Admin
enforcement applies. Required CI checks remain:

- `Backend tests and offline benchmarks`
- `Frontend build and focused E2E`

## Local validation and setup

- Select checks by changed behavior using [Testing Patterns](docs/codebase/TESTING.md).
  Documentation needs diff/link/command checks; backend work needs affected tests;
  UI work needs build and affected browser checks; contracts and shared changes
  need the broader checks listed there. Local selection does not bypass CI.
- Use the repository's `.venv` for Python. Reuse working environments; install
  dependencies only when missing, stale, or changed. Setup is in [README](README.md).
- Run relevant lint/format checks and `git diff --check`. Rerun passing checks
  after relevant edits, changed dependencies/base, failures, or new evidence;
  avoid repeating an unchanged suite merely to restate the result.
- A build alone does not prove a rendered UI flow. Use scoped browser evidence
  for the affected interaction; full product audits are for audit requests.
- Report unavailable checks and their practical risk. Do not claim mocked or
  offline checks establish live model, integration, or production behavior.

## Skills and next-task overrides

- Use only skills relevant to the task. Repository development skills live in
  `.agents/skills`; see [maintenance and invocation](docs/developer-skills.md).
  Application runtime guidance in `backend/app/skills` is a separate subsystem.
- These repository rules override generic `spec-driven-next-task` defaults:
  complete one task unless the user requests continuous work; in continuous
  mode, finish one authorized lifecycle before selecting the next ready issue.
- Refresh live candidates/dependencies needed for selection and reconcile only
  the completed issue, its parent, and affected dependents. Do not audit the full
  backlog, traceability matrix, or meta-process after every merge. Preserve real
  blockers and capture distinct follow-ups in an existing owner or scoped issue.
- PR-only work uses the same scoped delivery flow without an issue-readiness
  requirement. Stop after PR delivery unless the user authorized merging.

## Repository boundaries

- Backend/tests: `backend/`, `backend/tests/`; frontend/E2E: `frontend/`,
  `frontend/e2e/`; generated execution runtime: `backend/execution_runtime/`.
- Schemas: `schemas/`; architecture and planning references: `docs/`.
- Do not commit environments, credentials, secrets, node modules, generated
  artifacts, screenshots, or local browser profiles. Do not modify global skills
  or shared plugin caches as part of a repository change.
