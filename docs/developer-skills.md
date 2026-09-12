# Repository developer skills

[AGENTS.md](../AGENTS.md) defines work-record selection, scoped delivery, and
repository overrides for shared workflows. Use [Testing Patterns](codebase/TESTING.md)
to select local checks. The versioned `.agents/skills/` folders are developer
workflows; `backend/app/skills/` contains separate application runtime guidance.

## Invocation and scope

The skill name/description determines relevance; read its body only when needed
and follow reference links only for the current operation. Use
`repository-qa` for scoped browser checks. Full product audits and full seven-file
codebase maps require the corresponding audit/mapping request.

These six skills use `agents/openai.yaml` with
`policy.allow_implicit_invocation: false`: `add-educational-comments`,
`cloud-sql-basics`, `gke-basics`, `gtm-ai-gtm`, `microsoft-docs`, and `drawio`.
They remain available by explicit invocation. Other repository skills retain
automatic selection with narrower descriptions. Hosts that do not implement this
policy should treat these six skills as explicit-only too.

Do not run skill installers/updaters as part of ordinary coding, documentation,
or validation. Do not modify user-wide skills or shared plugin caches to enforce
repository policy. The root overrides the shared `spec-driven-next-task` workflow
without changing it for other projects.

## Maintaining the versioned copies

The retained skill folders, their references, and invocation policies are checked
in. `.gitignore` allows this curated set while excluding other local agent state
and dependency/output directories. When adding/removing a skill, update both the
allowlist and `skills-lock.json` in the same scoped PR.

Lock entries use `sourceType: local` and a relative `source` pointing to the
versioned folder. `upstream` is repository provenance metadata preserving the
previous installation entry; its hash is not the hash of our customized copy.
Do not change the source back to upstream merely to refresh the local hash.
New repository-authored skills do not need an upstream entry.

From the repository root:

```bash
# Read-only consistency check; no network or dependency installation.
node scripts/check_repo_skills.mjs
# After reviewing skill/reference/policy edits, refresh local content hashes.
node scripts/check_repo_skills.mjs --update-lock
node scripts/check_repo_skills.mjs
git diff --check
```

The hash matches the skills CLI algorithm: SHA-256 over each relative file path
and file bytes, sorted with JavaScript `localeCompare`, excluding `.git` and
`node_modules`. Changes to references and invocation policy are included.
The checker fails on missing/extra skill folders, non-local sources, symlinks,
missing entrypoints, and stale hashes. It does not install, delete, or activate
anything, and does not replace frontmatter/link or behavioral validation.

For a skill edit, validate YAML/name/description, relative references, and the
requested invocation behavior. Use the installed Skill Creator validator when
available; do not install a tooling collection just to run it. Exercise realistic
requests and inspect decisions/actions, not only whether prose matches a pattern.

## Existing checkouts with ignored skills

Earlier installs left some skills as ignored local files, even though the lock
listed them. A fresh checkout of this revision includes all retained skills.
An older checkout may still discover these retired folders:

- `folder-structure-blueprint-generator`
- `playwright-automation-fill-in-form`
- `playwright-explore-website`
- `playwright-generate-test`

Inspect those folders for local changes and move them outside `.agents/skills/`
before using the revised catalog. Keep any desired originals in a local archive
outside skill discovery; do not delete unrelated skills or user configuration.
The consistency checker reports leftover folders without removing them.

## Behavioral checks

Review these representative requests when revising routing or policy:

| Request | Expected behavior |
| --- | --- |
| Fix button wrapping in one screen | PR-only path; relevant style/build/browser checks; no Firebase setup. |
| Add a new product feature | Issue with acceptance criteria and relevant dependencies, then a linked PR. |
| Correct a README command typo | Direct bounded edit and command/link check; no outline approval or app suite. |
| Explain one workflow's architecture | Targeted source discovery and explanation; no full scan or seven-file rewrite. |
| Fix local backend parsing | Affected tests and changed-file lint/format checks; broaden only for impacted contracts/shared behavior. |
| Do the next task | One task by default; no automatic merge or full backlog audit. |
| Use the educational-comments or a specialist skill explicitly | Available on demand; preserve the requested output and scope. |

These are developer-workflow checks. They do not enable application guidance,
relax product approval gates, or change the required CI checks.
