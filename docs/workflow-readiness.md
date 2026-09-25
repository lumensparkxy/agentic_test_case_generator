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

This presentation change does not enforce execution eligibility. The current
backend can fall back to a configured local target and invokes execution before
project persistence checks. [Issue 320](https://github.com/lumensparkxy/agentic_test_case_generator/issues/320)
owns pre-execution approval, snapshot, revision and target guards and shared
readiness. Until that correction lands, the UI must not claim preview validation
establishes those permissions.
