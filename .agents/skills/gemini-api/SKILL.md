---
name: gemini-api
description: Implement or troubleshoot a Gemini or Google Gen AI SDK integration, including ADK provider configuration and structured/tool outputs. Do not trigger for unrelated backend work merely because Gemini is a dependency.
---

# Gemini integration work

Read the relevant adapter/agent, [configuration](../../../backend/app/config.py),
dependency manifest, and nearby tests. Reuse the existing Google Gen AI SDK and
ADK boundaries. Derive model names, auth mode, provider endpoint, and region from
current configuration and the user's request; do not substitute a tutorial model,
switch to Vertex AI, or install another SDK as incidental setup.

Reuse working dependencies and credentials. For mocked tests or code inspection,
a live API key/login is not a prerequisite. If the task requires a live call,
confirm the provider and authorized scope, check the current official API when
version-sensitive details matter, and report missing access at that step. Do not
print credentials or put them in generated artifacts.

Preserve structured contracts, state/tool flow, retries, fallback reporting, and
application guidance stage gates. Test malformed/partial output and the intended
successful path; select related offline benchmarks from
[Testing Patterns](../../../docs/codebase/TESTING.md).

Read only the reference matching the task:

- [Structured output and tools](references/structured_and_tools.md).
- [Text/multimodal](references/text_and_multimodal.md) or [embeddings](references/embeddings.md).
- [Caching, batch, and reasoning](references/advanced_features.md).
- [Live API](references/live_api.md), [media](references/media_generation.md),
  [bounding boxes](references/bounding_box.md), [safety](references/safety.md),
  or [tuning](references/model_tuning.md) for an explicitly relevant capability.
- [Optional SDK examples](references/examples.md): a language or initialization
  pattern needed for the task. Treat example versions/models as illustrative and
  adapt them to the installed SDK and configured provider.
