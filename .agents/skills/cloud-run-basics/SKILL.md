---
name: cloud-run-basics
description: Inspect, troubleshoot, or change a Cloud Run service, job, or worker pool when Cloud Run is the requested task. Do not trigger for local application changes or tests that happen to target a Cloud Run-hosted project.
---

# Cloud Run work

Inspect the existing deployment script, Dockerfile, and relevant configuration
before choosing a command. In this repository start with
[scripts/deploy_cloud_run.sh](../../../scripts/deploy_cloud_run.sh) and the
[deployment integration](../../../docs/codebase/INTEGRATIONS.md). Derive service,
project, region, image, identity, and auth policy from the actual target; tutorial
values are placeholders and must not replace existing settings.

For troubleshooting, read the relevant service/revision state and logs before
proposing a change. A local server or successful deploy command alone does not
prove live routing. Verify the deployed revision and intended endpoint when the
task includes deployment. Service containers must listen on the configured port
on `0.0.0.0`.

Reuse working tooling and authentication. Check/install tools or request login
only if the required operation is unavailable. Do not enable APIs, change IAM,
make a service public, or create infrastructure as incidental setup. Follow the
user's authorization and preserve existing deployment/security configuration.

Read only the needed reference:

- [CLI](references/cli-usage.md): service/job commands.
- [IAM/security](references/iam-security.md): identity or access changes.
- [Infrastructure as code](references/iac-usage.md): managed infrastructure edits.
- [Core concepts](references/core-concepts.md): choosing a resource kind.
- [Client libraries](references/client-library-usage.md) or [MCP](references/mcp-usage.md):
  a requested SDK/tool integration.
- [Optional examples](references/examples.md): a specific deployment/job/worker
  procedure not covered by the existing script; read only that section.

Use [Testing Patterns](../../../docs/codebase/TESTING.md) for local verification.
