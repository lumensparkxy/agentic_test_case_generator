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
