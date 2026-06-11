#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command not found: $name" >&2
    exit 1
  fi
}

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required variable: $name" >&2
    exit 1
  fi
}

ensure_artifact_repo() {
  local repo="$1"
  local region="$2"
  if ! gcloud artifacts repositories describe "$repo" --location "$region" >/dev/null 2>&1; then
    gcloud artifacts repositories create "$repo" \
      --repository-format=docker \
      --location "$region"
  fi
}

upsert_secret() {
  local secret_name="$1"
  local secret_value="$2"

  if gcloud secrets describe "$secret_name" >/dev/null 2>&1; then
    printf '%s' "$secret_value" | gcloud secrets versions add "$secret_name" --data-file=- >/dev/null
  else
    printf '%s' "$secret_value" | gcloud secrets create "$secret_name" --data-file=- >/dev/null
  fi
}

grant_secret_accessor() {
  local secret_name="$1"
  local service_account="$2"

  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member="serviceAccount:${service_account}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
}

require_command docker
require_command gcloud
require_command curl

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
ARTIFACT_REPO="${ARTIFACT_REPO:-agentic-tcg}"
BACKEND_SERVICE="${BACKEND_SERVICE:-tcg-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-tcg-frontend}"
SECRET_GEMINI_NAME="${SECRET_GEMINI_NAME:-tcg-gemini-api-key}"
SECRET_JWT_NAME="${SECRET_JWT_NAME:-tcg-jwt-secret-key}"
SECRET_FIREBASE_SA_NAME="${SECRET_FIREBASE_SA_NAME:-tcg-firebase-service-account-json}"
RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SERVICE_ACCOUNT:-}"
MODEL_NAME="${MODEL_NAME:-gemini-3.5-flash}"
JWT_ALGORITHM="${JWT_ALGORITHM:-HS256}"
JWT_EXPIRATION_MINUTES="${JWT_EXPIRATION_MINUTES:-60}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-${VITE_GOOGLE_CLIENT_ID:-}}"
GOOGLE_CLIENT_IDS="${GOOGLE_CLIENT_IDS:-}"
VITE_GOOGLE_CLIENT_ID="${VITE_GOOGLE_CLIENT_ID:-${GOOGLE_CLIENT_ID:-}}"
FIREBASE_PROJECT_ID="${FIREBASE_PROJECT_ID:-${VITE_FIREBASE_PROJECT_ID:-}}"
FIREBASE_SERVICE_ACCOUNT_JSON="${FIREBASE_SERVICE_ACCOUNT_JSON:-}"
GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-}"
VITE_FIREBASE_API_KEY="${VITE_FIREBASE_API_KEY:-}"
VITE_FIREBASE_AUTH_DOMAIN="${VITE_FIREBASE_AUTH_DOMAIN:-}"
VITE_FIREBASE_PROJECT_ID="${VITE_FIREBASE_PROJECT_ID:-${FIREBASE_PROJECT_ID:-}}"
VITE_FIREBASE_STORAGE_BUCKET="${VITE_FIREBASE_STORAGE_BUCKET:-}"
VITE_FIREBASE_MESSAGING_SENDER_ID="${VITE_FIREBASE_MESSAGING_SENDER_ID:-}"
VITE_FIREBASE_APP_ID="${VITE_FIREBASE_APP_ID:-}"
VITE_FIREBASE_MEASUREMENT_ID="${VITE_FIREBASE_MEASUREMENT_ID:-}"
JWT_SECRET_KEY="${JWT_SECRET_KEY:-}"
GEMINI_API_KEY="${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}"

require_var PROJECT_ID
require_var GOOGLE_CLIENT_ID
require_var VITE_GOOGLE_CLIENT_ID
require_var VITE_FIREBASE_API_KEY
require_var VITE_FIREBASE_AUTH_DOMAIN
require_var VITE_FIREBASE_PROJECT_ID
require_var VITE_FIREBASE_APP_ID
require_var JWT_SECRET_KEY
require_var GEMINI_API_KEY

if [[ -z "$FIREBASE_SERVICE_ACCOUNT_JSON" && -n "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
  if [[ ! -f "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
    echo "Warning: GOOGLE_APPLICATION_CREDENTIALS file not found: $GOOGLE_APPLICATION_CREDENTIALS" >&2
    echo "Falling back to application default credentials for Firebase Admin." >&2
  else
    printf 'Loading Firebase Admin credentials from %s\n' "$GOOGLE_APPLICATION_CREDENTIALS"
    FIREBASE_SERVICE_ACCOUNT_JSON="$(<"$GOOGLE_APPLICATION_CREDENTIALS")"
  fi
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEFAULT_RUNTIME_SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
EFFECTIVE_RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SERVICE_ACCOUNT:-$DEFAULT_RUNTIME_SERVICE_ACCOUNT}"

BACKEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${BACKEND_SERVICE}:latest"
FRONTEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${FRONTEND_SERVICE}:latest"

INITIAL_CORS="http://localhost:5173,http://127.0.0.1:5173"
GCLOUD_ENV_DELIMITER='@'

join_with_delimiter() {
  local delimiter="$1"
  shift

  local first=1
  local item
  for item in "$@"; do
    if (( first )); then
      printf '%s' "$item"
      first=0
    else
      printf '%s%s' "$delimiter" "$item"
    fi
  done
}

dedupe_csv() {
  printf '%s' "$1" \
    | tr ',' '\n' \
    | sed 's#[[:space:]]*##g' \
    | sed '/^$/d' \
    | sed 's#/$##' \
    | awk '!seen[$0]++' \
    | paste -sd, -
}

cloud_run_service_origin() {
  local service_name="$1"
  printf 'https://%s-%s.%s.run.app' "$service_name" "$PROJECT_NUMBER" "$REGION"
}

build_env_var_arg() {
  local cors_allow_origins="$1"
  local env_entries=(
    "MODEL_NAME=$MODEL_NAME"
    "GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID"
    "JWT_ALGORITHM=$JWT_ALGORITHM"
    "JWT_EXPIRATION_MINUTES=$JWT_EXPIRATION_MINUTES"
    "CORS_ALLOW_ORIGINS=$cors_allow_origins"
  )

  if [[ -n "$GOOGLE_CLIENT_IDS" ]]; then
    env_entries+=("GOOGLE_CLIENT_IDS=$GOOGLE_CLIENT_IDS")
  fi
  if [[ -n "$FIREBASE_PROJECT_ID" ]]; then
    env_entries+=("FIREBASE_PROJECT_ID=$FIREBASE_PROJECT_ID")
  fi

  printf '^%s^' "$GCLOUD_ENV_DELIMITER"
  join_with_delimiter "$GCLOUD_ENV_DELIMITER" "${env_entries[@]}"
}

build_secret_arg() {
  local secret_entries=(
    "GEMINI_API_KEY=${SECRET_GEMINI_NAME}:latest"
    "JWT_SECRET_KEY=${SECRET_JWT_NAME}:latest"
  )

  if [[ -n "$FIREBASE_SERVICE_ACCOUNT_JSON" ]]; then
    secret_entries+=("FIREBASE_SERVICE_ACCOUNT_JSON=${SECRET_FIREBASE_SA_NAME}:latest")
  fi

  join_with_delimiter ',' "${secret_entries[@]}"
}

assert_cors_origin_allowed() {
  local backend_url="$1"
  local origin="$2"
  local response

  response="$(curl -sS -D - -o /dev/null -X OPTIONS "${backend_url}/requirements/enrich" \
    -H "Origin: ${origin}" \
    -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: content-type,authorization' | tr -d '\r')"

  if ! printf '%s\n' "$response" | awk 'NR == 1 { exit ($2 == 200 ? 0 : 1) }'; then
    echo "CORS smoke check failed: unexpected preflight status for origin ${origin}" >&2
    printf '%s\n' "$response" >&2
    exit 1
  fi

  if ! printf '%s\n' "$response" | grep -Fqi "access-control-allow-origin: ${origin}"; then
    echo "CORS smoke check failed: backend did not allow origin ${origin}" >&2
    printf '%s\n' "$response" >&2
    exit 1
  fi
}

DEPLOY_ARGS=(--region "$REGION" --allow-unauthenticated --port 8080)
if [[ -n "$RUNTIME_SERVICE_ACCOUNT" ]]; then
  DEPLOY_ARGS+=(--service-account "$RUNTIME_SERVICE_ACCOUNT")
fi

printf 'Using project: %s\n' "$PROJECT_ID"
printf 'Using region: %s\n' "$REGION"
printf 'Artifact repo: %s\n' "$ARTIFACT_REPO"
printf 'Backend service: %s\n' "$BACKEND_SERVICE"
printf 'Frontend service: %s\n\n' "$FRONTEND_SERVICE"
printf 'Target platform: %s\n\n' "$TARGET_PLATFORM"
printf 'Runtime service account: %s\n\n' "$EFFECTIVE_RUNTIME_SERVICE_ACCOUNT"

gcloud config set project "$PROJECT_ID" >/dev/null

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  generativelanguage.googleapis.com \
  secretmanager.googleapis.com >/dev/null

ensure_artifact_repo "$ARTIFACT_REPO" "$REGION"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null

printf 'Updating secrets in Secret Manager...\n'
upsert_secret "$SECRET_GEMINI_NAME" "$GEMINI_API_KEY"
upsert_secret "$SECRET_JWT_NAME" "$JWT_SECRET_KEY"
if [[ -n "$FIREBASE_SERVICE_ACCOUNT_JSON" ]]; then
  upsert_secret "$SECRET_FIREBASE_SA_NAME" "$FIREBASE_SERVICE_ACCOUNT_JSON"
fi

printf 'Granting Secret Manager access to runtime service account...\n'
grant_secret_accessor "$SECRET_GEMINI_NAME" "$EFFECTIVE_RUNTIME_SERVICE_ACCOUNT"
grant_secret_accessor "$SECRET_JWT_NAME" "$EFFECTIVE_RUNTIME_SERVICE_ACCOUNT"
if [[ -n "$FIREBASE_SERVICE_ACCOUNT_JSON" ]]; then
  grant_secret_accessor "$SECRET_FIREBASE_SA_NAME" "$EFFECTIVE_RUNTIME_SERVICE_ACCOUNT"
fi

printf 'Building backend image...\n'
docker build \
  --platform "$TARGET_PLATFORM" \
  --provenance=false \
  -f backend/Dockerfile \
  -t "$BACKEND_IMAGE" \
  .

printf 'Pushing backend image...\n'
docker push "$BACKEND_IMAGE"

printf 'Deploying backend service...\n'
gcloud run deploy "$BACKEND_SERVICE" \
  --image "$BACKEND_IMAGE" \
  "${DEPLOY_ARGS[@]}" \
  --set-env-vars "$(build_env_var_arg "$INITIAL_CORS")" \
  --set-secrets "$(build_secret_arg)" >/dev/null

BACKEND_URL="$(gcloud run services describe "$BACKEND_SERVICE" --region "$REGION" --format='value(status.url)')"
printf 'Backend deployed: %s\n' "$BACKEND_URL"

printf 'Building frontend image with API base %s...\n' "$BACKEND_URL"
FRONTEND_BUILD_ARGS=(
  --build-arg "VITE_API_BASE=${BACKEND_URL}"
  --build-arg "VITE_GOOGLE_CLIENT_ID=${VITE_GOOGLE_CLIENT_ID}"
  --build-arg "VITE_FIREBASE_API_KEY=${VITE_FIREBASE_API_KEY}"
  --build-arg "VITE_FIREBASE_AUTH_DOMAIN=${VITE_FIREBASE_AUTH_DOMAIN}"
  --build-arg "VITE_FIREBASE_PROJECT_ID=${VITE_FIREBASE_PROJECT_ID}"
  --build-arg "VITE_FIREBASE_APP_ID=${VITE_FIREBASE_APP_ID}"
)
if [[ -n "$VITE_FIREBASE_STORAGE_BUCKET" ]]; then
  FRONTEND_BUILD_ARGS+=(--build-arg "VITE_FIREBASE_STORAGE_BUCKET=${VITE_FIREBASE_STORAGE_BUCKET}")
fi
if [[ -n "$VITE_FIREBASE_MESSAGING_SENDER_ID" ]]; then
  FRONTEND_BUILD_ARGS+=(--build-arg "VITE_FIREBASE_MESSAGING_SENDER_ID=${VITE_FIREBASE_MESSAGING_SENDER_ID}")
fi
if [[ -n "$VITE_FIREBASE_MEASUREMENT_ID" ]]; then
  FRONTEND_BUILD_ARGS+=(--build-arg "VITE_FIREBASE_MEASUREMENT_ID=${VITE_FIREBASE_MEASUREMENT_ID}")
fi

docker build \
  --platform "$TARGET_PLATFORM" \
  --provenance=false \
  "${FRONTEND_BUILD_ARGS[@]}" \
  -f frontend/Dockerfile \
  -t "$FRONTEND_IMAGE" .

printf 'Pushing frontend image...\n'
docker push "$FRONTEND_IMAGE"

printf 'Deploying frontend service...\n'
gcloud run deploy "$FRONTEND_SERVICE" \
  --image "$FRONTEND_IMAGE" \
  "${DEPLOY_ARGS[@]}" >/dev/null

FRONTEND_URL="$(gcloud run services describe "$FRONTEND_SERVICE" --region "$REGION" --format='value(status.url)')"
FRONTEND_SERVICE_URL="$(cloud_run_service_origin "$FRONTEND_SERVICE")"
FRONTEND_DOMAIN="$(printf '%s' "$FRONTEND_URL" | sed -E 's#^https?://##' | sed 's#/$##')"
FRONTEND_SERVICE_DOMAIN="$(printf '%s' "$FRONTEND_SERVICE_URL" | sed -E 's#^https?://##' | sed 's#/$##')"
FINAL_CORS="$(dedupe_csv "$INITIAL_CORS,$FRONTEND_URL,$FRONTEND_SERVICE_URL")"
printf 'Frontend deployed: %s\n' "$FRONTEND_URL"

printf 'Updating backend CORS to frontend URL...\n'
gcloud run services update "$BACKEND_SERVICE" \
  --region "$REGION" \
  --set-env-vars "$(build_env_var_arg "$FINAL_CORS")" \
  --set-secrets "$(build_secret_arg)" >/dev/null

printf 'Running backend CORS smoke check for %s...\n' "$FRONTEND_URL"
assert_cors_origin_allowed "$BACKEND_URL" "$FRONTEND_URL"
printf 'CORS smoke check passed.\n'

cat <<EOF

Cloud Run deployment complete.

Frontend URL: ${FRONTEND_URL}
Backend URL:  ${BACKEND_URL}

Next steps:
1. In Google Cloud Console, open the OAuth 2.0 Web Client used by this app.
2. Add these Authorized JavaScript origins:
   ${FRONTEND_URL}
  ${FRONTEND_SERVICE_URL}
3. In Firebase Console -> Authentication -> Settings -> Authorized domains, add:
   ${FRONTEND_DOMAIN}
  ${FRONTEND_SERVICE_DOMAIN}
4. If you later attach a custom domain, add that origin/domain too and rerun this script.
5. Verify sign-in and the full app flow in the deployed frontend.

You can rerun this script anytime after changing the app:
  PROJECT_ID=${PROJECT_ID} REGION=${REGION} ./scripts/deploy_cloud_run.sh
EOF
