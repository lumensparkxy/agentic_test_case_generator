# Credential Rotation Runbook

Issue #54 documents how to rotate user integration credentials and deployment
secrets without committing or exposing secret values.

## Scope

This runbook covers:

- JIRA Cloud API tokens stored per user.
- Azure DevOps Personal Access Tokens stored per user.
- `JIRA_CONNECTION_SECRET_KEY`
- `JIRA_CONNECTION_PREVIOUS_SECRET_KEYS`
- `AZURE_DEVOPS_CONNECTION_SECRET_KEY`
- `AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS`
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

New and re-encrypted records store ciphertext plus a non-secret key identifier
so operators can tell which configured key version encrypted the record without
exposing token material. Legacy records without a key id remain supported during
rotation.

During planned encryption-key rotation, configure:

- `JIRA_CONNECTION_SECRET_KEY` or `AZURE_DEVOPS_CONNECTION_SECRET_KEY` with the
  new primary key used for all new writes.
- `JIRA_CONNECTION_PREVIOUS_SECRET_KEYS` or
  `AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS` with comma-separated previous
  keys used only for read/decrypt.

The backend first uses matching key metadata when available, then tries the
primary and previous keys. After verification, re-encrypt old records with the
primary key by running `python scripts/reencrypt_integration_credentials.py`
for a dry run and `python scripts/reencrypt_integration_credentials.py --apply`
for the write pass.

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
longer decrypts, first restore the previous key through
`JIRA_CONNECTION_PREVIOUS_SECRET_KEYS` and run the re-encryption command. Delete
and recreate the user connection only when the old encryption key is unavailable
or the provider API token itself must be replaced.

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
no longer decrypts, first restore the previous key through
`AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS` and run the re-encryption command.
Delete and recreate the user connection only when the old encryption key is
unavailable or the provider PAT itself must be replaced.

## Integration Encryption Key Rotation

Use this for `JIRA_CONNECTION_SECRET_KEY` or
`AZURE_DEVOPS_CONNECTION_SECRET_KEY`.

Planned rotation:

1. Schedule a maintenance window. The provider tokens/PATs can remain active;
   users should not need to reconnect solely because the encryption key changes.
2. Generate the new dedicated connection secret.
3. Configure the runtime with:
   - New primary key in `JIRA_CONNECTION_SECRET_KEY` or
     `AZURE_DEVOPS_CONNECTION_SECRET_KEY`.
   - Current old key in `JIRA_CONNECTION_PREVIOUS_SECRET_KEYS` or
     `AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS`.
   - For Cloud Run, `scripts/deploy_cloud_run.sh` stores primary and previous
     key variables in Secret Manager when set locally.
   - Override Secret Manager names with `SECRET_JIRA_CONNECTION_NAME`,
     `SECRET_JIRA_CONNECTION_PREVIOUS_NAME`,
     `SECRET_AZURE_DEVOPS_CONNECTION_NAME`, or
     `SECRET_AZURE_DEVOPS_CONNECTION_PREVIOUS_NAME` when needed.
4. Redeploy or restart the backend.
5. Verify representative JIRA and Azure DevOps connection status, import, and
   sync-preview flows. Reads should continue because the old key is configured
   as previous.
6. Run a dry-run re-encryption report:

   ```bash
   source .venv/bin/activate
   python scripts/reencrypt_integration_credentials.py
   ```

7. If the dry run reports no failed records, apply the migration:

   ```bash
   source .venv/bin/activate
   python scripts/reencrypt_integration_credentials.py --apply
   ```

   Use `--provider jira` or `--provider azure-devops` to rotate one provider at
   a time.
8. Review the JSON summary. `failed` must be `0`; `reencrypted` and
   `metadata_updated` describe records written with primary-key metadata.
9. Remove the old key from the previous-key environment variable and redeploy.
10. Verify representative provider status/import/sync-preview flows again.
11. Disable or destroy old Secret Manager versions only after rollback is no
    longer needed.

Do not rotate `JWT_SECRET_KEY` as a substitute for dedicated integration keys
when existing integration records still depend on the fallback. First introduce
dedicated integration keys, include the old `JWT_SECRET_KEY` in the relevant
previous-key variable, and re-encrypt records with the dedicated primary key.

## JWT Secret Rotation

`JWT_SECRET_KEY` signs backend-issued JWTs only in local/E2E compatibility mode.
Production protected endpoints should use Firebase ID tokens with
`AUTH_TOKEN_MODE=firebase-only`.

Rotation steps:

1. Confirm production is using `AUTH_TOKEN_MODE=firebase-only`.
2. Confirm JIRA and Azure DevOps use dedicated connection secrets, or stage the
   old `JWT_SECRET_KEY` as a previous key and re-encrypt integration records
   before removing fallback dependency.
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
- `JIRA_CONNECTION_PREVIOUS_SECRET_KEYS` through
  `SECRET_JIRA_CONNECTION_PREVIOUS_NAME`
- `AZURE_DEVOPS_CONNECTION_SECRET_KEY` through
  `SECRET_AZURE_DEVOPS_CONNECTION_NAME`
- `AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS` through
  `SECRET_AZURE_DEVOPS_CONNECTION_PREVIOUS_NAME`

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
