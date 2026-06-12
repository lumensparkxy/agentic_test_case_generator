# Frontend API Contracts

The frontend keeps a small committed contract surface generated from the
FastAPI OpenAPI schema:

- `frontend/src/api/generated/api-contracts.d.ts` contains TypeScript
  declarations for high-traffic request and response payloads.
- `frontend/src/api/generated/api-contracts.js` contains runtime endpoint
  constants used by the current React workflow.

These files are committed because they are stable review artifacts and make API
drift visible in pull requests. Do not edit them by hand.

## Regenerate

```bash
source .venv/bin/activate
python scripts/export_openapi.py --output /tmp/agentic-tcg-openapi.json --indent 0
python scripts/generate_frontend_api_types.py
```

## Check Freshness

```bash
source .venv/bin/activate
python scripts/generate_frontend_api_types.py --check
```

The check regenerates contracts from the current FastAPI app and fails if the
committed files are stale. CI runs this after the OpenAPI export gate.

## Covered Workflows

The first slice covers the payloads that the UI calls most often:

- requirements parse and context enrichment
- test-case generation and refinement
- CSV, Excel, JSON, and JIRA export inputs
- execution preview and run
- billing entitlements

Broader endpoint coverage can be added by extending `SELECTED_OPERATIONS` in
`scripts/generate_frontend_api_types.py`.
