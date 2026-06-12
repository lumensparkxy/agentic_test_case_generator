# Artifact Fetching Threat Model

This document records the accepted security boundary for remote artifact
fetching used by context grounding.

## Scope

Remote artifact fetching is for public, unauthenticated context references
provided by a user, such as application pages, prototypes, diagrams, image
metadata pages, plain text notes, JSON API descriptions, or XML documents.

Accepted sources:

- `http` and `https` URLs only.
- Publicly routable hostnames or IP addresses only.
- Textual bodies only: `text/*`, JSON, or XML content types.
- Responses up to `MAX_FETCH_BYTES` in `backend/app/services/artifact_fetcher.py`.
- Redirect chains up to `MAX_REDIRECTS`, with every redirect target rechecked.

Out of scope:

- Authenticated artifact fetching with cookies, bearer tokens, Basic auth, PATs,
  or embedded URL credentials.
- Internal, private, loopback, link-local, local, multicast, reserved, or
  unresolved hosts.
- Binary document ingestion through the remote URL fetcher.
- Arbitrary user-controlled network access from production infrastructure.

Authenticated or internal artifact access requires a separate issue before
implementation. That design should use an allow-list, brokered proxy, or
approved storage handoff with explicit ownership, audit logging, and egress
controls instead of direct user-supplied URL access.

## Controls

The fetcher fails closed before any network request when a URL:

- uses a scheme other than `http` or `https`
- includes embedded credentials
- uses localhost, loopback literals, `0.0.0.0`, `::1`, or `.local` hosts
- is an IP address in private, loopback, link-local, reserved, multicast, or
  unspecified ranges
- resolves through DNS to any blocked IPv4 or IPv6 address
- cannot be resolved

For responses, the fetcher:

- disables automatic redirects and validates each redirect target before
  following it
- caps redirects at `MAX_REDIRECTS`
- applies `FETCH_TIMEOUT_SECONDS`
- reads at most `MAX_FETCH_BYTES + 1` bytes and rejects oversized responses
- rejects unsupported or missing content types instead of treating them as
  analyzed context

## Failure Behavior

Unsafe or unsupported artifacts return a `Skipped` result with a warning note.
Network failures, timeouts, HTTP failures, and size-limit failures return an
`Unavailable` result with a warning note. Context grounding records those
statuses on `ArtifactSource` entries and continues enrichment for the remaining
requirements and artifacts.

Raw uploaded documents, tokens, cookies, credentials, and binary bodies are not
stored by the artifact fetcher. Timeout normalization avoids returning raw
exception detail to artifact notes.

## Residual Risk

DNS can change between the preflight resolution and the underlying HTTP client
connection. The current control blocks unsafe answers at validation time and
revalidates redirect targets, which is acceptable for the public,
unauthenticated artifact scope. Deployments that need stronger SSRF assurance
should combine this application policy with egress firewall rules or a
brokered fetch service.

## Validation Map

Regression coverage lives in `backend/tests/test_artifact_fetcher.py` and
`backend/tests/test_context_grounding.py`.

Covered cases:

- unsafe schemes, localhost, private IPv4, and private IPv6 literals
- DNS resolution failure, empty DNS results, and mixed public/private answers
- redirects to private hosts
- embedded URL credentials
- oversized responses
- unsupported content types
- direct timeout and `URLError` timeout behavior
- partial grounding warnings when fetches are skipped, unavailable, or raise
  unexpectedly
