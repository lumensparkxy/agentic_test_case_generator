# Home Workspace Production Rollout

GitHub issue: [#214](https://github.com/lumensparkxy/agentic_test_case_generator/issues/214)

This runbook deploys the bounded Home workspace read model, its three required
Firestore composite indexes, and the backend/frontend Cloud Run revisions. It
is intentionally additive: the index helper never deletes remote indexes, and
the Cloud Run release mode does not rotate secrets, change IAM, replace runtime
environment variables, or rebuild CORS policy.

## Guardrails

- Deploy only from a clean `main` whose `HEAD` equals the fetched
  `origin/main`.
- Keep the backend on `AUTH_TOKEN_MODE=firebase-only`.
- Do not use the bootstrap deploy mode for a routine code release. Bootstrap
  enables APIs, creates infrastructure, writes Secret Manager versions, applies
  IAM bindings, and replaces managed runtime configuration.
- Keep `https://test-engineer-agent.maswadkar.com` in the live backend CORS
  allow-list and include it in `CORS_SMOKE_ORIGINS` for this production app.
- Do not add an unbounded project-scan fallback. A missing or building index
  must continue to surface as HTTP 503.
- Keep rollout verification read-only. Do not approve/request changes,
  regenerate, execute, or export while checking production.
- Never paste `.env`, tokens, service-account JSON, Secret Manager values, or
  browser session data into logs, issues, or pull requests.

## 1. Prepare and capture the rollback point

Authenticate Docker and `gcloud` using the existing operator account. Do not
create a new service account or key for this release.

```bash
git switch main
git fetch origin --prune
git pull --ff-only origin main
git status --short --branch
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"

export PROJECT_ID=testcase-generatorxy
export REGION=us-central1
export BACKEND_SERVICE=tcg-backend
export FRONTEND_SERVICE=tcg-frontend
export ARTIFACT_REPO=agentic-tcg
export PROD_ORIGIN=https://test-engineer-agent.maswadkar.com

export PREVIOUS_BACKEND_REVISION="$(gcloud run services describe "$BACKEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --format='value(status.latestReadyRevisionName)')"
export PREVIOUS_FRONTEND_REVISION="$(gcloud run services describe "$FRONTEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --format='value(status.latestReadyRevisionName)')"

printf 'Backend rollback revision: %s\n' "$PREVIOUS_BACKEND_REVISION"
printf 'Frontend rollback revision: %s\n' "$PREVIOUS_FRONTEND_REVISION"
```

Before changing anything, verify the current frontend, backend health, custom
origin CORS, runtime service account, public/private access policy, and current
`AUTH_TOKEN_MODE`. Record revision names and image digests, but do not print
secret values.

```bash
curl -fsS "$(gcloud run services describe "$BACKEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')/health"
curl -fsS -o /dev/null "$PROD_ORIGIN/"
curl -fsS -D - -o /dev/null -X OPTIONS \
  "$(gcloud run services describe "$BACKEND_SERVICE" \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')/workspace/summary" \
  -H "Origin: $PROD_ORIGIN" \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

Stop if the baseline is already unhealthy or if live authentication is not
`firebase-only`. Fixing IAM, runtime identities, or credentials is separate
security work and is not authorized by this rollout.

## 2. Validate and deploy the Firestore indexes

The versioned manifest is `firestore.indexes.json`; `firebase.json` points only
to that file. The two `qa_projects` indexes and the nested `execution_runs`
index all use `COLLECTION` query scope because the runtime queries a specific
collection (including `project_doc.collection("execution_runs")`). The
collection-group identifier for the last index is still `execution_runs`.

Validate JSON, run the focused contract test, and run the pinned Firebase CLI
schema/deployment dry run:

```bash
python3 -m json.tool firebase.json >/dev/null
python3 -m json.tool firestore.indexes.json >/dev/null

source .venv/bin/activate
python -m unittest backend.tests.test_firestore_index_manifest

npx --yes firebase-tools@15.22.4 deploy \
  --only firestore:indexes \
  --project "$PROJECT_ID" \
  --config firebase.json \
  --dry-run \
  --non-interactive
```

The Firebase CLI command is schema validation only. Keep `--dry-run`: this is a
partial workspace-index manifest, so using `firebase deploy` as the apply path
could propose deletion of unrelated live indexes that are not represented in
the file. The additive Python helper below is the only authorized apply path.

Preview the live additive plan, then apply it. The helper refuses `--apply`
outside clean, synchronized `main`, creates only missing indexes through the
supported `gcloud firestore indexes composite create` command, preserves any
unmanaged indexes, and waits for all versioned definitions to report `READY`.

```bash
python scripts/deploy_firestore_indexes.py --project "$PROJECT_ID"
python scripts/deploy_firestore_indexes.py --project "$PROJECT_ID" --apply
```

Do not deploy either Cloud Run service until the helper reports all three
indexes `READY`. If an index reports `NEEDS_REPAIR` or the wait times out, leave
the existing services in place, inspect the Firestore operation, and do not
delete unrelated indexes.

## 3. Deploy the Cloud Run release

`DEPLOY_MODE=release` is the code-only path. It verifies a clean synchronized
`main`, existing services and Artifact Registry, the live
`AUTH_TOKEN_MODE=firebase-only` setting, existing health, frontend availability,
and configured CORS origins before building. It tags both images with the
current commit by default and updates only the service images. Existing
environment variables, Secret Manager references, IAM policy, runtime service
account, ingress, and CORS configuration are preserved.

The frontend Firebase build values still come from `DEPLOY_ENV_FILE` (default
`.env`). Explicit caller-exported values take precedence over that file, so the
target project, region, services, image tag, and CORS smoke origin cannot be
silently replaced by local defaults. A release ignores a local compatibility
`AUTH_TOKEN_MODE` value because it preserves and validates the live production
value rather than replacing it.

```bash
export DEPLOY_MODE=release
export DEPLOY_ENV_FILE=.env
export CORS_SMOKE_ORIGINS="$PROD_ORIGIN"
export IMAGE_TAG="$(git rev-parse --short=12 HEAD)"

./scripts/deploy_cloud_run.sh
```

For release mode, the helper builds and pushes both commit-tagged images before
either service receives new traffic. It then deploys backend first, verifies
backend health, deploys frontend, and finishes with frontend HTTP 200 and exact
custom-origin CORS checks. A failure after the backend switch prints the exact
captured frontend/backend traffic-rollback commands; inspect the failure and
run those commands if compatibility is uncertain.

## 4. Verify production without mutation

Record the new ready revisions and image digests:

```bash
gcloud run services describe "$BACKEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --format='yaml(status.latestCreatedRevisionName,status.latestReadyRevisionName,status.traffic,spec.template.spec.containers[0].image)'

gcloud run services describe "$FRONTEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --format='yaml(status.latestCreatedRevisionName,status.latestReadyRevisionName,status.traffic,spec.template.spec.containers[0].image)'
```

Required checks:

1. Backend `GET /health` returns HTTP 200.
2. Unauthenticated `GET /workspace/summary` returns HTTP 401. HTTP 404 means the
   old backend is still serving; HTTP 503 after authentication means an index
   is missing or not ready.
3. The custom-origin preflight returns HTTP 200 and exactly
   `Access-Control-Allow-Origin: https://test-engineer-agent.maswadkar.com`.
4. Both the Cloud Run frontend URL and custom domain return HTTP 200.
5. An authenticated, read-only browser check receives HTTP 200 from
   `/workspace/summary`, renders Home, Continue working, My work, Projects, and
   Recent activity, opens the canonical pending Use Cases route, marks Use Cases
   current, and reaches review controls by keyboard.
6. No project, review, generation, execution, or export request is submitted.
7. New backend/frontend revisions have no new startup, authentication,
   Firestore-index, or HTTP 5xx errors in Cloud Logging.

Attach the revision names, index states, command outcomes, and read-only browser
observations to #214. Do not attach credentials, response tokens, browser
profiles, or screenshots containing private data.

## Recorded production evidence

On 2026-07-18, PR #216 merged to protected `main` as `62a60e9`.
The additive deployment completed with all three versioned Firestore composite
indexes `READY`. Cloud Run routed 100% of production traffic to backend revision
`tcg-backend-00027-tdr` and frontend revision
`tcg-frontend-00011-w8z`.

An authenticated Computer Use smoke against
`https://test-engineer-agent.maswadkar.com/` received HTTP 200 from
`/workspace/summary`, rendered Continue working, My work, Projects, and Recent
activity on Home, and showed 2 projects, 8 prioritized work items, 2 recent
runs, and 2 recent reports within the endpoint's default 20/50/20/20 bounds. It
opened the canonical pending Use Cases route with Use Cases current and its
review controls keyboard-reachable.

Post-deploy checks confirmed `AUTH_TOKEN_MODE=firebase-only`, the existing
runtime service account and public-invoker policies, and the exact custom-domain
CORS origin. No secret or environment configuration changed. The smoke was
read-only: no project, review, generation, execution, or export mutation was
submitted.

## 5. Roll back

If the frontend is unhealthy, route it to the captured frontend revision first.
If the backend is unhealthy or incompatible, route it to the captured backend
revision second:

```bash
gcloud run services update-traffic "$FRONTEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --to-revisions="${PREVIOUS_FRONTEND_REVISION}=100" --quiet

gcloud run services update-traffic "$BACKEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --to-revisions="${PREVIOUS_BACKEND_REVISION}=100" --quiet
```

Re-run health, frontend, CORS, and read-only authentication checks after traffic
rollback. Leave the three additive Firestore indexes in place: they are
backward-compatible, and deleting them is unnecessary and destructive. Release
mode does not rotate secrets, so revision traffic rollback retains the same
secret references and runtime policy as the pre-release service.

## Bootstrap and credential rotation

`DEPLOY_MODE=bootstrap` preserves the helper's original infrastructure/setup
behavior and is intentionally separate from a release. Use it only for a new
environment or an explicitly approved credential/configuration rotation. Set a
production `CORS_ALLOW_ORIGINS` value before bootstrap; the helper now keeps
those configured origins when it adds the Cloud Run frontend URLs. Follow
`docs/credential-rotation-runbook.md` for any secret change.
