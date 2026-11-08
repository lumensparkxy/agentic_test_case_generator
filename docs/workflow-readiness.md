# Workflow readiness and execution evidence

Test Cases distinguishes approved requirements from current human approval of
Use Cases. Its compact readiness message links to the missing review. A missing
or older orchestrator response does not enable a fallback generation action.
Concrete cases and unfinished generation tasks are counted separately.

The existing optional draft path appears as **Generate draft** when no suite
exists, under one **More actions** disclosure. Once a local or saved suite exists,
replacement retains **Full Regenerate**, confirmation, and failure preservation.
Draft generation does not approve Use Cases or the resulting test suite.

Automation navigation distinguishes a current preview, a stored preview needing
refresh, and a run with its own ID and outcome. A preview never marks execution
complete. Failed, disabled, stale and unrecognized run evidence remain distinct
from a passed execution. Preview classification alone is not business acceptance.

Execution preview includes server-derived `readiness`: blockers, the resolved
target and its source, project revision and source test-case snapshot. Drafts may
be previewed, but Run requires current readiness and selected eligible candidates.
Stored or older responses without readiness require a fresh preview.

A saved preview for the current approved test suite that consistently reports
zero executable candidates leads to Reports. Test-case evidence can be exported
for manual testing, including for saved suites that predate the Use Cases stage.
The Reports navigation shows Complete for current approved evidence and Needs
attention for draft or stale evidence, including after reload.

Before invoking the runtime, the run endpoint verifies project ownership, active
status, the current revision, approved and non-stale requirements and test cases,
and current human approval of Use Cases when that stage exists. The submitted
cases must exactly match the current saved suite, with no unfinished generation
tasks. Recorded upstream snapshot references must still be current. The endpoint
recomputes these checks; client-provided readiness cannot grant permission.
Rejections return HTTP 409 with readiness blockers; unauthorized projects retain
their existing permission response. Selection must be nonempty and contain only
eligible candidates. Legacy source IDs are accepted only when unambiguous.

Use an exact label, CSS selector, or test ID to assert the current value of an
editable control.

```text
Then field "Employee name" value should equal "Alex Rivera"
Then field "Employee name" value should contain "Rivera"
Then css "#employee-name" value should equal "Alex Rivera"
Then test id "employee-name" value should equal "Alex Rivera"
```

Use an empty quoted value to check a blank field. Substring checks require a
nonempty literal. Both compile to Playwright value assertions. Plain equality in
a saved structured spec still checks text content. Preview treats equality
against a label filled by the same source step as a value assertion. When the
fill and check name the same field, both use its exact label. Preview marks
unrepresentable field-value checks unsupported so the case cannot run with that
assertion omitted.

Supply `target_base_url` or explicitly configure `EXECUTION_DEFAULT_BASE_URL`.
The runtime's implicit localhost fallback does not authorize an API run. This
also applies to non-project API requests, which require explicit selection but
have no project approval evidence to validate. Direct runtime callers retain
their existing defaults. A configured target identifies where to execute; it
does not prove the target implements the intended business behavior.

Run receipts retain the source snapshot verified before execution. Existing
persistence revision and idempotency checks remain. This preflight is not a
durable execution reservation: concurrent requests or edits during execution can
still lead to persistence conflicts after a runtime has started. It does not
provide exactly-once external effects. Completeness here means no recorded
generation tasks, not independently proven semantic coverage. No Room Booking
application target was supplied for the original audit.
