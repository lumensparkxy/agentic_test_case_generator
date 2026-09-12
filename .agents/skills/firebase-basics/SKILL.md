---
name: firebase-basics
description: Inspect or change Firebase Authentication, Firestore, Firebase rules, indexes, SDK integration, or Firebase deployment. Do not trigger for unrelated UI or backend work merely because the project uses Firebase.
---

# Firebase work

Start with the requested Firebase behavior and the existing integration. Inspect
relevant source/configuration and [integration boundaries](../../../docs/codebase/INTEGRATIONS.md).
Use the current project, auth mode, SDK, and deployment scripts; do not create a
new project or replace an established auth flow as incidental setup.

For code inspection, mocked tests, or planning, no CLI login is needed. Reuse
working dependencies and authentication. Check a CLI/SDK only when the task needs
it; install a missing dependency only within the requested scope. Do not install
or update additional skill collections as a prerequisite.

When a live action is required, discover the available connector or CLI and
confirm the target project using read-only state. Reuse existing authorization;
request login only if authentication is actually missing. Apply the user's scope
to deployments, IAM, rules, indexes, and data writes. Report authentication or
access blockers at the affected step without blocking independent local work.

Read only the reference needed for the operation:

- [Client libraries](references/client-library-usage.md): SDK integration.
- [CLI](references/cli-usage.md): an actual CLI operation.
- [Security](references/iam-security.md): rules, identities, or permissions.
- [Infrastructure as code](references/iac-usage.md): infrastructure changes.
- [Core concepts](references/core-concepts.md) or [MCP](references/mcp-usage.md):
  unfamiliar Firebase behavior or relevant available tools.

Choose focused checks from [Testing Patterns](../../../docs/codebase/TESTING.md).
Local mocked checks do not prove live Firebase behavior.
