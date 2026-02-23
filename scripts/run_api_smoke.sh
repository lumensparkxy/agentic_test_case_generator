#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="/tmp/tcagent_api_verify"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
HOST="127.0.0.1"
PORT="8000"

mkdir -p "$TMP_DIR"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

"$PYTHON_BIN" -m uvicorn app.main:app --app-dir backend --host "$HOST" --port "$PORT" >/tmp/tcagent_uvicorn.log 2>&1 &
SERVER_PID=$!

for _ in {1..40}; do
  if curl -sS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

curl -sS -w "\nHTTP_STATUS:%{http_code}\n" "http://$HOST:$PORT/health" > "$TMP_DIR/health.out"
curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/requirements/enrich" \
  -H "Content-Type: application/json" \
  --data-binary @"$ROOT_DIR/scripts/api_payloads/enrich_payload.json" > "$TMP_DIR/enrich.out"

curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/export/csv" \
  -H "Content-Type: application/json" \
  --data-binary @"$ROOT_DIR/scripts/api_payloads/export_payload.json" > "$TMP_DIR/export_csv.out"

curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/export/excel" \
  -H "Content-Type: application/json" \
  --data-binary @"$ROOT_DIR/scripts/api_payloads/export_payload.json" > "$TMP_DIR/export_excel.bin"

curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/export/json" \
  -H "Content-Type: application/json" \
  --data-binary @"$ROOT_DIR/scripts/api_payloads/export_payload.json" > "$TMP_DIR/export_json.out"

curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/export/jira" \
  -H "Content-Type: application/json" \
  --data-binary @"$ROOT_DIR/scripts/api_payloads/jira_payload.json" > "$TMP_DIR/export_jira.out"

curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/automation/playwright" \
  -H "Content-Type: application/json" \
  --data-binary @"$ROOT_DIR/scripts/api_payloads/automation_payload.json" > "$TMP_DIR/automation.out"

if [[ -n "${GOOGLE_API_KEY:-}" || -n "${GEMINI_API_KEY:-}" ]]; then
  curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/requirements/parse" \
    -F "file=@$ROOT_DIR/sample-requirements.md" > "$TMP_DIR/parse.out"
  curl -sS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://$HOST:$PORT/testcases/generate" \
    -H "Content-Type: application/json" \
    --data-binary @"$ROOT_DIR/scripts/api_payloads/generate_payload.json" > "$TMP_DIR/generate.out"
fi

check_status_file() {
  local f="$1"
  if ! grep -q "HTTP_STATUS:200" "$f"; then
    echo "Smoke check failed: $f"
    cat "$f"
    exit 1
  fi
}

check_status_file "$TMP_DIR/health.out"
check_status_file "$TMP_DIR/enrich.out"
check_status_file "$TMP_DIR/export_csv.out"
check_status_file "$TMP_DIR/export_json.out"
check_status_file "$TMP_DIR/export_jira.out"
check_status_file "$TMP_DIR/automation.out"

if [[ -f "$TMP_DIR/parse.out" ]]; then
  check_status_file "$TMP_DIR/parse.out"
fi
if [[ -f "$TMP_DIR/generate.out" ]]; then
  check_status_file "$TMP_DIR/generate.out"
fi

if ! strings "$TMP_DIR/export_excel.bin" | grep -q "HTTP_STATUS:200"; then
  echo "Smoke check failed: $TMP_DIR/export_excel.bin"
  exit 1
fi

echo "API smoke checks passed"
echo "health=$(tail -n 1 "$TMP_DIR/health.out")"
echo "enrich=$(tail -n 1 "$TMP_DIR/enrich.out")"
echo "export_csv=$(tail -n 1 "$TMP_DIR/export_csv.out")"
echo "export_json=$(tail -n 1 "$TMP_DIR/export_json.out")"
echo "export_jira=$(tail -n 1 "$TMP_DIR/export_jira.out")"
echo "automation=$(tail -n 1 "$TMP_DIR/automation.out")"
