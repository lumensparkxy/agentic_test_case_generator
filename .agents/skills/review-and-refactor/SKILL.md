---
name: review-and-refactor
description: Review specified code or a diff, or refactor an identified area. Keep the review and any edits within the requested behavior; do not scan or refactor the whole repository by default.
---

# Scoped review and refactoring

Identify the requested files, diff, or behavior. Read relevant callers, tests,
and conventions only as needed to understand that scope. If the scope is unclear,
inspect the current diff before asking a targeted question.

A review request produces findings; edit only when fixes/refactoring are also
requested. Refactor only to serve the stated outcome, preserving behavior unless
a change is explicitly intended. Split or extract files when this improves the
requested design; do not impose a blanket keep-files-intact rule.

Follow [AGENTS.md](../../../AGENTS.md) for PR-only versus issue-linked work.
Use [Testing Patterns](../../../docs/codebase/TESTING.md) to select focused
verification and broaden only for affected shared behavior. Report actionable
findings with file/line evidence or explain what changed and how it was checked.
