# Automation generation evidence and validation

`POST /automation/playwright` generates Python artifacts; it does not execute a
browser. It is separate from the deterministic execution preview/run endpoints.
The response distinguishes generated/partial/skipped output, per-case diagnostics,
syntax/evidence validation, target configuration and `execution_status: not_executed`.
No Room Booking execution target has been supplied for the audit.

Existing requests remain valid but no longer manufacture executable smoke tests
when evidence or model output is missing. Supply `target_base_url` and per-case
`interface_evidence`. A case marked Manual stays manual. Missing target, observed
interface/assertion mapping or ambiguous case/step identity yields an actionable
manual/unsupported diagnostic without a generation call. No example URL is used
as a default. Missing credentials and invalid model output produce unsupported
cases, not generic body-visible tests reported as business coverage.

Example for a supplied synthetic page contract (not a live observation):

```json
{
  "target_base_url": "https://automation-fixture.example.test/",
  "test_cases": [{
    "id": "TC-1", "title": "Page title", "automation_status": "To Be Automated",
    "steps": [{"step": 1, "action": "Open supplied target", "expected": "Title is Synthetic QA."}]
  }],
  "interface_evidence": [{
    "test_case_id": "TC-1",
    "source_reference": "Supplied synthetic HTML contract: title is Synthetic QA",
    "assertions": [{"step": 1, "method": "to_have_title", "expected": "Synthetic QA"}]
  }]
}
```

For element assertions, supply `locator` with `method`, `value` and optional
`name` (for example `get_by_role`, `status`). Additional action locators go in
`locators`; navigation beyond the target goes in `allowed_urls`; form values,
including any deliberately supplied test credentials, go in `input_values`.
Never infer credentials or selectors from a generated case title. Evidence is
provided by the caller, not independently fetched or certified by this endpoint.

Every expected step maps to an assertion. A distinct overall `expected_result`
requires an additional assertion at `step: 0`. The prompt receives all source
steps/data/expectations; diagnostics retain their original expected-result text.
Supported assertions are the explicit methods in the OpenAPI contract.

Both small and parallel paths parse and compile every emitted Python file
without importing or executing it. Exactly one top-level discoverable
`test_<normalized_case_id>` is required per eligible case. Tests need direct
`expect(...)` expressions matching each supplied assertion's method, locator and
value. Missing/truncated code, missing/duplicate tests, invented selectors/URLs
or input values, fixed sleeps, skipped/decorated tests, bypassing control flow,
suppressed exceptions and disabled TLS verification are rejected. Unsupported
Python patterns remain unsupported rather than being counted as validated.
This conservative static validation is not a security sandbox or runtime proof.

There is one model attempt per small suite or shard, with no repair loop. A
failed shard retains its diagnostics and does not erase valid siblings. Final
assembly is validated again. Counts come from delivered validated artifacts,
not the model's self-reported diagnostics; usage events count generated cases.
Saved generation reports include the per-case diagnostics and are explicitly not execution evidence. A captured project revision prevents a late report from overwriting newer project state. No artifact is
written to disk or executed by this generation endpoint.

The existing four-arm guidance evaluator treats complete explicit manual
resolution separately from malformed code and model success. Manual resolution
with zero model calls does not prove model quality or guidance improvement.
Keep Automation guidance held until independent quality/cost review justifies a
separate rollout decision. Compilation and supplied assertion preservation do
not prove business acceptance against an application.
