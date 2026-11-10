# Test Case Generator 2.0 desktop workspace sketches

Each sketch uses the existing project header, navigation, disclosure, status, and workbench surfaces. Project name and current saved state stay visible across all three. At higher zoom the queue and reader stack in DOM order, with a visible way back to the selected row.

## Inputs

```text
Projects / Mercury Checkout       Source current · draft recovered locally
[Inputs]   Review   Deliver
┌─ Sources ──────────────────────┬─ Supporting context ────────────────────┐
│ Paste · Upload · Jira · Azure │ Testing objective / risk emphasis        │
│ Source name, version, excerpt │ Notes, application URL and evidence      │
│ Pending change comparison     │ Each marked supplied, read, or stale     │
│ Confirm replacements/removals │ Prerequisite references under Setup     │
└────────────────────────────────┴─────────────────────────────────────────┘
Open questions (2) · Answer, defer, or exclude with source     [Review scope]
```

Source comparison replaces the left body while a draft is active. Save/prepare is deliberate, and a link remains supplied until a supported collector confirms access. The scope count reflects the server's eligible items, including visible exclusions.

## Review

```text
Projects / Mercury Checkout          Source current · plan v3 · suite v2
Inputs   [Review]   Deliver
┌─ Queue ────────────────────────┬─ Selected item ─────────────────────────┐
│ Scope (2) · Coverage (4)      │ Source excerpt / expected behavior       │
│ Test findings (1) · All tests │ Scenario coverage or ordered case steps  │
│ Search          Filters      │ Expected outcomes and exact test data    │
│ • REQ-101 · scope decision    │ Findings and supporting case references  │
│ • REQ-202 · missing assertion │ Setup details and traceability links     │
└────────────────────────────────┴─────────────────────────────────────────┘
Saved progress / recovery         [Approve scope and prepare scenarios]
```

Use the existing requirement and scenario workbenches as sections before composing a common reader. The primary action changes with the current decision: approve scope and prepare scenarios, approve scenarios and generate tests, review saved suite, or improve selected tests. The operation/status area shows the specific committed decision, queued work, or recovery state. Do not combine current and historical evidence into one row.

## Deliver

```text
Projects / Mercury Checkout       Saved suite v2 · current source
Inputs   Review   [Deliver]
Design complete · Human acceptance unavailable · Execution blocked · Not run
┌─ Export test suite ─────────────┬─ Run browser tests ─────────────────────┐
│ Select format and saved suite   │ Target and outstanding prerequisites    │
│ Source, scope, assessment       │ Preview · eligible/manual/unsupported  │
│ [Download JSON]                │ [Run selected] (after current preview) │
└─────────────────────────────────┴─────────────────────────────────────────┘
Saved reports / runs  →  original suite and cases     [Open execution evidence]
```

A historical selection remains labelled and read-only. When a case opens from a saved file report, **Back to saved report** and browser Back return to that file and focus the original case link. A draft export is a separate explicit state requiring a reason under the existing gate. Preview counts never stand in for execution outcomes.
