# Credential Rotation Runbook

Issue #54 documents how to rotate user integration credentials and deployment
secrets without committing or exposing secret values.

## Scope

This runbook covers:

- JIRA Cloud API tokens stored per user.
- Azure DevOps Personal Access Tokens stored per user.
- `JIRA_CONNECTION_SECRET_KEY`
- `AZURE_DEVOPS_CONNECTION_SECRET_KEY`
- `JWT_SECRET_KEY`
- `GEMINI_API_KEY`
- Firebase Admin credentials through `FIREBASE_SERVICE_ACCOUNT_JSON` or
  `GOOGLE_APPLICATION_CREDENTIALS`.
- Cloud Run Secret Manager entries managed by `scripts/deploy_cloud_run.sh`,
  including optional `METRICS_ACCESS_TOKEN`.

Do not paste real tokens, service account JSON, project IDs, or client
credentials into docs, issues, pull requests, logs, screenshots, or fixtures.

## Current Encryption Behavior

JIRA API tokens and Azure DevOps PATs are encrypted before Firestore storage.
The backend derives a Fernet key from:

- `JIRA_CONNECTION_SECRET_KEY` for JIRA, falling back to `JWT_SECRET_KEY`.
- `AZURE_DEVOPS_CONNECTION_SECRET_KEY` for Azure DevOps, falling back to
  `JWT_SECRET_KEY`.

Existing records do not store a key id and the backend does not currently accept
previous decryption keys. If one of these encryption keys changes, records
encrypted with the old key cannot be decrypted until the user connection is
deleted and recreated, or until follow-up issue
[#77](https://github.com/lumensparkxy/agentic_test_case_generator/issues/77)
adds seamless multi-key re-encryption support.

For production, prefer dedicated `JIRA_CONNECTION_SECRET_KEY` and
`AZURE_DEVOPS_CONNECTION_SECRET_KEY` values instead of relying on
`JWT_SECRET_KEY` fallback encryption. This decouples integration-record
encryption from local/E2E backend JWT compatibility.

## JIRA API Token Rotation

Per-user JIRA rotation:

1. The user creates a new Atlassian API token with the smallest necessary JIRA
   scopes for requirement import/sync.
2. The user reconnects JIRA in the app with the same site URL and email plus
   the new token.
3. The backend validates the token, encrypts it with the configured JIRA
   connection secret, stores it in `jira_user_connections`, and updates the
   token hint plus validation timestamps.
4. Run a small import or sync preview to verify the new token.
5. Revoke the old Atlassian API token after the new connection is verified.

If the JIRA encryption key was already rotated and the existing record no
longer decrypts, delete the user connection first through
`DELETE /integrations/jira/connection` or by an approved admin Firestore cleanup,
then reconnect with the new token.

## Azure DevOps PAT Rotation

Per-user Azure DevOps rotation:

1. The user creates a new PAT in the target organization/account with the
   smallest useful scopes:
   - Project and team: read
   - Work items: read/write
2. The user reconnects Azure DevOps in the app with the organization or project
   URL and the new PAT.
3. The backend validates the PAT, encrypts it with the configured Azure DevOps
   connection secret, stores it in `azure_devops_user_connections`, and updates
   the token hint plus validation timestamps.
4. Run a search, import, or sync preview to verify the new PAT.
5. Revoke the old PAT after the new connection is verified.

If the Azure DevOps encryption key was already rotated and the existing record
no longer decrypts, delete the user connection first through
`DELETE /integrations/azure-devops/connection` or by an approved admin Firestore
cleanup, then reconnect with the new PAT.

## Integration Encryption Key Rotation

Use this for `JIRA_CONNECTION_SECRET_KEY` or
`AZURE_DEVOPS_CONNECTION_SECRET_KEY`.

Planned rotation with current code:

1. Schedule a maintenance window and notify users that stored JIRA/Azure DevOps
   connections may need to be recreated.
2. Keep provider tokens/PATs active until users verify reconnection.
3. Set the new dedicated connection secret in the runtime environment.
   - For Cloud Run, `scripts/deploy_cloud_run.sh` stores these variables in
     Secret Manager when `JIRA_CONNECTION_SECRET_KEY` or
     `AZURE_DEVOPS_CONNECTION_SECRET_KEY` are set locally.
   - Override Secret Manager names with `SECRET_JIRA_CONNECTION_NAME` or
     `SECRET_AZURE_DEVOPS_CONNECTION_NAME` when needed.
4. Redeploy or restart the backend.
5. Existing records encrypted with the old key will fail decrypting. Users
   should delete and recreate affected connections, or an approved admin should
   delete stale Firestore connection documents before users reconnect.
6. Verify representative JIRA and Azure DevOps connection status, import, and
   sync-preview flows.
7. Revoke old provider tokens/PATs after new encrypted records are verified.

Do not rotate `JWT_SECRET_KEY` as a substitute for dedicated integration keys
when existing integration records still depend on the fallback. First introduce
dedicated integration keys and plan user reconnection or #77-style
re-encryption.

## JWT Secret Rotation

`JWT_SECRET_KEY` signs backend-issued JWTs only in local/E2E compatibility mode.
Production protected endpoints should use Firebase ID tokens with
`AUTH_TOKEN_MODE=firebase-only`.

Rotation steps:

1. Confirm production is using `AUTH_TOKEN_MODE=firebase-only`.
2. Confirm JIRA and Azure DevOps use dedicated connection secrets, or accept
   that records encrypted with `JWT_SECRET_KEY` fallback will need deletion and
   reconnection after rotation.
3. Generate a new long random `JWT_SECRET_KEY`.
4. Update the runtime secret:
   - Local: update `.env`.
   - Cloud Run: rerun `scripts/deploy_cloud_run.sh` with the new value or add a
     new Secret Manager version and redeploy the service.
5. Restart/redeploy the backend.
6. Local/E2E backend JWTs minted before the rotation are invalid. Sign out/in or
   mint new compatibility tokens.

## Gemini API Key Rotation

1. Create a new Gemini/Google API key with the same intended API access.
2. Update `GEMINI_API_KEY`:
   - Local: update `.env`.
   - Cloud Run: rerun `scripts/deploy_cloud_run.sh` or add a new Secret Manager
     version for the configured Gemini secret and redeploy.
3. Run offline validation first, then a small model-backed smoke workflow only
   if live model access is approved for the environment.
4. Disable or delete the old API key after the new key is verified.

The offline evaluation scripts remain the default validation path when live
model credentials are unavailable.

## Firebase Credential Rotation

For Firebase web app config values, update the frontend environment and rebuild
the frontend image.

For Firebase Admin credentials:

1. Prefer Application Default Credentials or a managed runtime service account
   on Google Cloud where possible.
2. If `FIREBASE_SERVICE_ACCOUNT_JSON` is required, create a new service account
   key in Google Cloud with the minimum roles required by Firebase Admin and
   Firestore access.
3. Update the local `.env` value or the file referenced by
   `GOOGLE_APPLICATION_CREDENTIALS`.
4. For Cloud Run, rerun `scripts/deploy_cloud_run.sh`; it uploads
   `FIREBASE_SERVICE_ACCOUNT_JSON` to Secret Manager when provided, or reads the
   file referenced by `GOOGLE_APPLICATION_CREDENTIALS`.
5. Redeploy/restart and verify protected endpoint authentication plus a
   Firestore-backed read/write path in the target environment.
6. Disable/delete the old service account key after verification.

## Cloud Run Secret Manager Rotation

`scripts/deploy_cloud_run.sh` creates a new Secret Manager version when a
managed secret already exists. The backend is deployed with `:latest` secret
references for the values it manages.

Managed by the helper when set:

- `GEMINI_API_KEY` through `SECRET_GEMINI_NAME`
- `JWT_SECRET_KEY` through `SECRET_JWT_NAME`
- `FIREBASE_SERVICE_ACCOUNT_JSON` through `SECRET_FIREBASE_SA_NAME`
- `METRICS_ACCESS_TOKEN` through `SECRET_METRICS_NAME`
- `JIRA_CONNECTION_SECRET_KEY` through `SECRET_JIRA_CONNECTION_NAME`
- `AZURE_DEVOPS_CONNECTION_SECRET_KEY` through
  `SECRET_AZURE_DEVOPS_CONNECTION_NAME`

Rotation steps:

1. Export the new local environment value without printing it.
2. Run `./scripts/deploy_cloud_run.sh`.
3. Confirm the deployment completed and the runtime service account has
   `roles/secretmanager.secretAccessor` on the relevant secret.
4. Verify the affected runtime path.
5. Disable or destroy old Secret Manager versions only after rollback is no
   longer needed.

Do not store production secret values in issue comments, PR bodies, screenshots,
terminal transcripts, or generated artifacts.
