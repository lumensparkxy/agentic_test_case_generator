# Client Submission Workflow Artifacts

Issue #21 keeps source-controlled automation separate from generated client
outputs.

## Tracked Inputs

- `frontend/e2e/capture-client-screenshots.mjs` runs against a local frontend
  and mocks backend API responses with synthetic data.
- `scripts/build_client_solution_brief.py` builds a Word brief from tracked
  narrative content and optional screenshots produced by the capture script.

## Ignored Outputs

Generated outputs are written under `client_submission/`, which is ignored by
git. This includes screenshots, downloaded exports, observer summaries, upload
fixtures, and generated `.docx` briefs.

Do not commit real client data, credentials, local browser profiles,
screenshots, downloaded exports, or generated briefs unless a future issue
explicitly approves a publication path.

## Retention and Cleanup

Use synthetic fixture data for client-submission artifacts unless a linked issue
explicitly approves real client data. Delete generated local outputs as soon as
the review or handoff is complete; real client or operational data should not
remain in ignored local directories for more than 7 days without a follow-up
retention issue.

Dry-run cleanup from the repository root:

```bash
python scripts/cleanup_generated_artifacts.py --target client_submission
```

Delete dry-run matches after review:

```bash
python scripts/cleanup_generated_artifacts.py --target client_submission --max-age-days 7 --apply
```

The broader policy and default cleanup targets are documented in
`docs/artifact-retention-policy.md`.

## Local Reproduction

From the repository root:

```bash
cd frontend
npm run dev
```

In another terminal:

```bash
node frontend/e2e/capture-client-screenshots.mjs
python scripts/build_client_solution_brief.py
```

The scripts use synthetic demo users and `example.test`/`example.com` fixture
values only.
