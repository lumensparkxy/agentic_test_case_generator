# Agent Instructions

These instructions apply to the entire repository unless a more specific
`AGENTS.md` exists in a nested directory.

## Project Contract

- This is a spec-driven development project.
- Every requirement, bug, enhancement, refactor, documentation change, or
  operational change must be tied to a GitHub issue or story before code changes
  are made.
- Do not make orphan changes. If no issue exists, create one or draft an
  issue-ready proposal before implementation.
- Keep every implementation scoped to the linked issue acceptance criteria.
- Link commits, pull requests, and follow-up notes back to the issue.
- Treat `docs/github-issues-backlog.md` and current GitHub issues as planning
  sources of truth; keep them aligned when plans change.

## GitHub Planning Model

Use GitHub issues as the unit of work.

- `Epic`: a larger outcome grouping related stories and tasks.
- `Story`: user-facing or stakeholder-facing behavior with clear acceptance
  criteria.
- `Task`: technical work needed to support a story or project operation.
- `Bug`: incorrect behavior, regression, failed validation, or broken workflow.
- `Spike`: time-boxed research needed before implementation decisions.

Use GitHub issue types when enabled. If issue types are unavailable, use labels
as the fallback taxonomy.

Required issue fields:

- Summary of the intended change and user/business value.
- Acceptance criteria with observable outcomes.
- Test plan listing required automated and manual checks.
- Labels for type, area, priority, and status where applicable.
- Milestone for the active phase or release.
- Dependencies, blockers, and parent epic links when applicable.

Required labels:

- Type: `type:epic`, `type:story`, `type:task`, `type:bug`, `type:spike`
- Area: `area:backend`, `area:frontend`, `area:qa`, `area:docs`,
  `area:devops`
- Priority: `priority:p0`, `priority:p1`, `priority:p2`, `priority:p3`
- Status: `status:blocked`, `status:ready`, `status:in-progress`

Use phase or release milestones. Existing examples include:

- `Phase 0 - Evaluation`
- `Phase 1 - Coverage Intelligence`
- `Phase 2 - Grounded Context`

For epics, prefer GitHub's native `Epic` issue type where available. Otherwise,
use the `type:epic` label and link child stories/tasks from the epic body.

## Development Workflow

1. Read the linked issue, parent epic, acceptance criteria, dependencies, and
   relevant repository docs before editing.
2. Confirm the issue is ready and unblocked. If it is not, document the blocker
   instead of implementing around it.
3. Create or use a branch named `codex/issue-<number>-<short-slug>` when working
   from a GitHub issue.
4. Implement only the issue scope. Avoid unrelated refactors and cleanup.
5. Add or update focused tests for changed behavior.
6. Update documentation when API behavior, configuration, setup, workflow, or
   user-facing behavior changes.
7. Run the required validation gates and record the commands and results.
8. Commit with the issue number in the message when an issue number exists.
9. Open or update a pull request linked to the issue.
10. Comment on the issue with the implementation summary, validation results,
    and any follow-up work.

## Local Environment

Use the repo-local Python virtual environment for backend work. Do not rely on
globally installed Python packages.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Backend development:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --app-dir backend --reload-dir backend
```

Frontend development:

```bash
cd frontend
npm ci
npm run dev
```

Execution runtime setup:

```bash
cd backend/execution_runtime
npm ci
```

## Validation Gates

Run the smallest gate that proves the issue, then broaden checks when the blast
radius crosses backend, frontend, integrations, or workflow boundaries.

Backend:

```bash
source .venv/bin/activate
python -m unittest discover -s backend/tests -p 'test_*.py'
python scripts/evaluate_requirements.py --offline --strict
python scripts/evaluate_generation.py --offline --strict
python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json --indent 0
```

Frontend:

```bash
cd frontend
npm ci
npm run build
```

E2E and browser workflow changes:

```bash
cd frontend
npm run test:e2e -- <focused-spec>
```

Execution runtime changes:

```bash
cd backend/execution_runtime
npm ci
npm run test:playwright -- --list
```

If a validation gate cannot be run, report the reason and the remaining risk in
the PR and issue comment.

## Repository Boundaries

- Backend FastAPI code lives in `backend/`.
- Backend tests live in `backend/tests/`.
- Frontend React/Vite code lives in `frontend/`.
- Frontend E2E specs live in `frontend/e2e/`.
- Generated Playwright execution runtime code lives in
  `backend/execution_runtime/`.
- Schemas live in `schemas/`.
- Planning and architecture docs live in `docs/`.
- Do not commit `.venv/`, `.env`, generated artifacts, node modules,
  screenshots, local browser profiles, credentials, API keys, tokens, or secrets.

## Completion Criteria

A change is complete only when:

- The linked issue acceptance criteria are satisfied.
- The implementation stays within issue scope.
- Relevant tests and validation gates were run and reported.
- Documentation is updated for changed workflow, API, configuration, or behavior.
- The pull request is linked to the issue and clearly summarizes what changed.
- Follow-up work is captured as linked issues instead of hidden TODOs.

