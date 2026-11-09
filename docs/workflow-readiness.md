# Workflow readiness and execution evidence

Test Cases distinguishes approved requirements from current human approval of
Use Cases. Its compact readiness message links to the missing review. A missing
or older orchestrator response does not enable a fallback generation action.
Concrete cases and unfinished generation tasks are counted separately.

The existing optional draft path appears as **Generate draft** when no suite
exists, under one **More actions** disclosure. Once a local or saved suite exists,
replacement retains **Full Regenerate**, confirmation, and failure preservation.
Draft generation does not approve Use Cases or the resulting test suite.

In Generated Test Cases, search includes saved priority and automation intent.
Open **Filters** to narrow the list within the full suite, changed tests,
or an independent review group. The filters recover with the same saved source
and review scope. Automation intent describes the test design. Check the
Automation preview for current browser eligibility and target readiness.

Automation navigation distinguishes a current preview, a stored preview needing
refresh, and a run with its own ID and outcome. A preview never marks execution
complete. Failed, disabled, stale and unrecognized run evidence remain distinct
from a passed execution. Preview classification alone is not business acceptance.

Execution preview includes server-derived `readiness`: blockers, the resolved
target and its source, project revision and source test-case snapshot. Drafts may
be previewed, but Run requires current readiness and selected eligible candidates.
Stored or older responses without readiness require a fresh preview.

Project previews require an active, owned project, the current revision and
the exact saved case array. The compact preview and its completed audit commit
together under that revision. After an interrupted response, the workbench
reuses the request ID. The server verifies that the preview is still current
and the compiler returns the same evidence before returning the result again.
This leaves the saved revision and usage unchanged. A prior preview remains
visible during recovery, with Run disabled until a current preview is confirmed.

Home and the project overview use the same current execution evidence. A ready
preview for the approved test suite recommends Execute even when an earlier
report is stale. A complete preview with zero executable candidates leads to
Reports, including for approved suites that predate the Use Cases stage. A
partial classification, inconsistent counts, or a blocked runnable preview
leads back to Automation. Home reads compact preview facts and reads candidate
buckets separately when an older preview needs them to verify its counts.

A failed current run prompts evidence review. Earlier and historical failures
remain available in Runs. A stale report with no newer preview still offers
regeneration. The Reports navigation shows Complete for current approved
evidence and Needs attention for draft or stale evidence, including after reload.

Saving a preview preserves the current report and its evidence. A completed
run can replace that report, and upstream changes can make it stale. Older
reports marked stale by a preview regain their status when the saved sources
and run evidence can be verified. An exact retry of a current file export
returns the original audit and file after a preview, with no new publication.

New CSV and Excel exports include an **Exact Steps (JSON)** column after
the existing columns. Read the column as a JSON array in saved step order.
Each entry contains the step number, action, expectation and test data.
`null` means no data was supplied and `""` means the step explicitly uses
an empty value. In Excel, if the cell links to **Long Values**, join its Text
parts in numeric Part order. If Encoding is **JSON string**, decode the
joined text as a JSON string first, then parse the step array. Saved exports
replay the layout recorded at publication.

Before invoking the runtime, the run endpoint verifies project ownership, active
status, the current revision, approved and non-stale requirements and test cases,
and current human approval of Use Cases when that stage exists. The submitted
cases must exactly match the current saved suite, with no unfinished generation
tasks. Recorded upstream snapshot references must still be current. The endpoint
recomputes these checks; client-provided readiness cannot grant permission.
Rejections return HTTP 409 with readiness blockers; unauthorized projects retain
their existing permission response. Selection must be nonempty and contain only
eligible candidates. Legacy source IDs are accepted only when unambiguous.

Each nonblank step expectation needs a supported browser assertion that accounts
for the entire expectation. A successful action or a different assertion in the
same case cannot cover an unmapped result. Automation shows the source step and
expected result in the Unsupported bucket. Use separate steps for independent
observations or verify outcomes outside the browser manually. Action-only steps
can leave the expected result blank. The run endpoint rechecks the current case
selection, including when another case in the same preview is eligible.

Use an exact label, CSS selector, or test ID to assert the current value of an
editable control.

```text
Then field "Employee name" value should equal "Alex Rivera"
Then field "Employee name" value should contain "Rivera"
Then css "#employee-name" value should equal "Alex Rivera"
Then test id "employee-name" value should equal "Alex Rivera"
```

Use an empty quoted value to check a blank field. Substring checks require a
nonempty literal. Both compile to Playwright value assertions. Use
`Enter "literal input" into field "Employee name"` for an exact fill and
`Enter "" into field "Employee name"` to clear a populated field. Quoted input
preserves spaces, including at the edges. Escape embedded double quotes and
backslashes. Plain equality in
a saved structured spec still checks text content. Preview treats equality
against a label filled by the same source step as a value assertion. When the
fill and check name the same field, both use its exact label. Preview marks
unrepresentable field-value checks unsupported so the case cannot run with that
assertion omitted.

For exact saved text, use `css "#saved-value" text should equal "literal"`
or `test id "saved-value" text should equal "literal"` with an observed
locator. The comparison includes edge spaces, repeated spaces, and line
breaks in the actual text. An empty expectation requires empty text. Use a
separate check when the element must also be visible. Legacy unscoped label
text checks continue to normalize whitespace.

The step-data syntax uses only the string saved on that step. It preserves
newlines and braces in values such as JSON, including deliberately malformed
JSON. Placeholder-shaped text in this data stays literal. Use a credential
prerequisite for values that should come from the environment.

Supply `target_base_url` or explicitly configure `EXECUTION_DEFAULT_BASE_URL`.
The runtime's implicit localhost fallback does not authorize an API run. This
also applies to non-project API requests, which require explicit selection but
have no project approval evidence to validate. Direct runtime callers retain
their existing defaults. A configured target identifies where to execute; it
does not prove the target implements the intended business behavior.

An explicit execution request ID is claimed before browser launch and fences
repeated delivery. A current run commits its execution, report, result parts,
findings, completed receipt and audit together. When the project changes during
execution, the completed run is saved in history with its original revision and
suite. The existing current snapshots retain their status, and the workbench
links directly to the historical run. An unconfirmed request remains fenced.
The request claim protects portal replay. External systems still control the
effects of each browser action.

If the Playwright configuration retries a failed attempt and the case later
passes, the portal records the case as flaky and retains the initial failure
details. Flaky cases have their own count. A run with flaky cases and no failed
or invalid cases requires review. If any case fails or is invalid, the run is
failed and still shows its flaky cases. A retry-only pass cannot verify a
quality finding. Runs and the saved report carry the same outcome. Earlier
records retain the evidence available when they were saved.

Playwright can report a runner error after a case completes, for example
during global cleanup. The portal keeps the original case outcomes and marks
the run as failed when Playwright reports a runner error or exits unsuccessfully
without a corresponding case failure. Automation and the exact saved report
show runner diagnostics separately. Such a run requires review and cannot
verify a quality finding.
