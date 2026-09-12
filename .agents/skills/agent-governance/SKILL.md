---
name: agent-governance
description: Design or change agent tool authorization, policy enforcement, audit boundaries, or trust controls when that governance behavior is the task. Do not trigger merely because the application uses agents or external APIs.
---

# Agent governance changes

Start from the requested policy behavior, existing enforcement boundary, and
observable acceptance criteria. Read the relevant agent/service and tests;
identify who authorizes the action, which data is available, and where effects
are committed. Preserve established ownership and review gates.

Choose the minimum mechanism that implements the requested control. Do not add
intent classification, trust scoring, audit systems, or human approvals just
because examples include them. Reuse the repository's contracts and boundaries
instead of introducing a parallel governance framework.

For a needed implementation pattern, read only the relevant section in
[optional patterns](references/patterns.md): policy composition, semantic intent,
tool enforcement, trust scoring, audit trails, or framework integration.
Examples are illustrative; check their fit and current API before adopting them.

Governance-policy changes require an issue under [AGENTS.md](../../../AGENTS.md).
Test the permitted action, rejected action, relevant identity/data boundary, and
failure behavior. Select additional checks from
[Testing Patterns](../../../docs/codebase/TESTING.md); no live provider call is a
prerequisite for a deterministic policy test.
