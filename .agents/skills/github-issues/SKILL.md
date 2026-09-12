---
name: github-issues
description: Find, create, or update GitHub issues, priorities, dependencies, and project records when requested or required by repository policy. Do not create an issue automatically for eligible PR-only work.
---

# GitHub work records

First apply [AGENTS.md](../../../AGENTS.md): small localized work can use a PR
without a separate issue. Features and substantial or sensitive changes need an
issue. Reuse an existing issue that owns the outcome before creating another.

Discover currently callable GitHub tools and their schemas. Use a connected tool
that supports the operation, or the authenticated `gh` CLI when needed. Do not
assume a particular MCP server, tool name, or lack of write support. Read-only
inspection does not imply authorization for unrelated writes.

Confirm the repository from the current remote/context. For issue-linked work,
read the issue and relevant parent/dependencies; discover labels and milestones
only when needed, and reuse that result within the task. Preserve unrelated
fields when updating an existing record.

Keep issue content proportional: value, observable acceptance criteria, test
plan, and relevant metadata/dependencies. Prefer the repository's types and
labels to invented defaults. A distinct schedulable outcome may need an issue;
checklist-sized implementation steps belong in the existing record.

Use structured tool arguments for bodies. With `gh`, write multiline Markdown
to a temporary file and use `--body-file` for issue/PR creation or comments.
Read the resulting record or returned URL to verify the write.

The PR holds implementation/validation evidence. Add an issue comment only for
a blocker, decision, or new information; do not mirror status to the backlog.
Use `Closes #N` only when acceptance is fulfilled. Repository merge policy and
the user's authorization determine whether to merge.

Read a reference only for the corresponding advanced operation; examples must
be adapted to the available API and current repository policy:

- [Templates](references/templates.md): issue structure when useful.
- [Search](references/search.md): advanced queries.
- [Sub-issues](references/sub-issues.md) or [dependencies](references/dependencies.md).
- [Issue types](references/issue-types.md), [fields](references/issue-fields.md),
  or [Projects](references/projects.md): requested metadata/hierarchy work.
- [Images](references/images.md): requested issue attachments.
