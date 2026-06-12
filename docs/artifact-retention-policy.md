# Generated Artifact Retention Policy

Issue #53 defines retention and cleanup expectations for ignored generated
artifacts that may contain screenshots, traces, reports, exports, generated
specs, or client-sensitive workflow data.

## Scope

The default cleanup targets are:

- `.execution_artifacts/`
- `client_submission/`
- `/tmp/pw_workflow_out`

Other ignored generated directories, such as
`backend/execution_runtime/artifacts/`, can be cleaned by passing `--target`
explicitly.

## Retention Expectations

- Local synthetic development artifacts may be kept briefly for debugging. The
  cleanup command defaults to selecting files older than 14 days.
- Real client or operational data should not be generated locally unless a
  linked issue explicitly approves it. When approved, delete generated local
  artifacts as soon as the review or handoff is complete, and no later than 7
  days unless a separate retention issue authorizes a longer window.
- Production containers should treat local generated artifacts as ephemeral.
  Do not use container-local artifact directories as durable records. If a
  production workflow needs durable artifact retention, create a follow-up issue
  for an approved storage location, access policy, audit trail, and retention
  schedule.
- Generated artifacts must remain out of git. Do not commit screenshots,
  traces, exports, browser profiles, credentials, generated briefs, or real
  operational data.

## Cleanup Command

Run a dry-run from the repository root:

```bash
python scripts/cleanup_generated_artifacts.py
```

Delete files older than the default 14-day window:

```bash
python scripts/cleanup_generated_artifacts.py --apply
```

Use a stricter window for real-data cleanup:

```bash
python scripts/cleanup_generated_artifacts.py --max-age-days 7 --apply
```

Override targets when needed:

```bash
python scripts/cleanup_generated_artifacts.py \
  --target .execution_artifacts \
  --target client_submission \
  --target /tmp/pw_workflow_out
```

Safety defaults:

- The command is dry-run unless `--apply` is passed.
- In-repository targets must be ignored by git unless
  `--allow-unignored-target` is passed.
- Tracked files are skipped even when an override target is allowed.
- Missing targets are skipped so the command is safe on fresh checkouts.

Use `--allow-unignored-target` only for deliberate one-off maintenance, and
review the dry-run output before applying it.
