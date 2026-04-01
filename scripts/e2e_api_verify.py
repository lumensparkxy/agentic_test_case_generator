import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT_DIR = "/tmp/tcagent_api_verify"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _mint_auth_token() -> str:
    configured_token = os.getenv("AUTH_TOKEN", "").strip()
    if configured_token:
        return configured_token

    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    algorithm = os.getenv("JWT_ALGORITHM", "HS256").strip() or "HS256"
    minutes_raw = os.getenv("JWT_EXPIRATION_MINUTES", "60").strip() or "60"

    if not secret:
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key == "JWT_SECRET_KEY" and not secret:
                    secret = value.strip()
                elif key == "JWT_ALGORITHM" and not os.getenv("JWT_ALGORITHM"):
                    algorithm = value.strip() or algorithm
                elif key == "JWT_EXPIRATION_MINUTES" and not os.getenv("JWT_EXPIRATION_MINUTES"):
                    minutes_raw = value.strip() or minutes_raw

    if not secret:
        return ""

    try:
        import jwt
    except ImportError:
        return ""

    try:
        expiration_minutes = max(1, int(minutes_raw))
    except ValueError:
        expiration_minutes = 60

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "api-verify-user",
        "email": "api-verify@example.com",
        "name": "API Verify",
        "picture": None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expiration_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


AUTH_TOKEN = _mint_auth_token()
WORKFLOW_SETTINGS = {
    "approval_threshold": 88,
    "max_iterations": 2,
    "timeout_seconds": 180,
    "stall_iteration_limit": 2,
    "retry_attempts": 0,
}


def assert_workflow_metadata(name: str, response: dict) -> None:
    settings = response.get("workflow_settings") or {}
    diagnostics = response.get("workflow_diagnostics") or {}

    if settings.get("approval_threshold") != WORKFLOW_SETTINGS["approval_threshold"]:
        raise AssertionError(f"{name}: expected approval_threshold {WORKFLOW_SETTINGS['approval_threshold']}, got {settings}")
    if settings.get("max_iterations") != WORKFLOW_SETTINGS["max_iterations"]:
        raise AssertionError(f"{name}: expected max_iterations {WORKFLOW_SETTINGS['max_iterations']}, got {settings}")
    if diagnostics.get("attempt_count", 0) < 1:
        raise AssertionError(f"{name}: workflow diagnostics missing valid attempt_count: {diagnostics}")
    if diagnostics.get("status") not in {"completed", "partial", "fallback", "failed"}:
        raise AssertionError(f"{name}: workflow diagnostics missing valid status: {diagnostics}")


def read_parse_requirements() -> list[dict]:
    with open(f"{OUT_DIR}/parse.out", "r", encoding="utf-8") as f:
        raw = f.read()

    status = None
    if "HTTP_STATUS:" in raw:
        status_text = raw.rsplit("HTTP_STATUS:", 1)[-1].strip().splitlines()[0]
        try:
            status = int(status_text)
        except ValueError:
            status = None

    if status != 200:
        return []

    parse_json = raw.split("HTTP_STATUS:")[0].strip()
    data = json.loads(parse_json)
    return data["requirements"]


def post_json(path: str, payload: dict) -> tuple[int, bytes, dict]:
    headers = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"

    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def write_out(name: str, status: int, body: bytes, headers: dict) -> None:
    with open(f"{OUT_DIR}/{name}", "wb") as f:
        ctype = headers.get("Content-Type", "")
        if "application/vnd.openxmlformats-officedocument" in ctype:
            f.write(
                (
                    f"BINARY_BYTES:{len(body)}\n"
                    f"CONTENT_TYPE:{ctype}\n"
                    f"HTTP_STATUS:{status}\n"
                ).encode("utf-8")
            )
        else:
            f.write(body)
            f.write(f"\nHTTP_STATUS:{status}\n".encode("utf-8"))


def main() -> None:
    if not AUTH_TOKEN:
        print("AUTH_TOKEN is not set and could not be minted from .env; skipping protected e2e API verification.")
        return

    requirements = read_parse_requirements()
    if not requirements:
        print("parse.out does not contain a successful parse response; skipping e2e API verification.")
        return

    refine_payload = {
        "feedback": "Make wording concise and keep requirements strictly testable.",
        "existing_requirements": json.dumps(requirements),
        "workflow_settings": json.dumps(WORKFLOW_SETTINGS),
    }
    refine_body = urllib.parse.urlencode(refine_payload).encode("utf-8")
    refine_req = urllib.request.Request(
        BASE + "/requirements/parse",
        data=refine_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {AUTH_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(refine_req, timeout=240) as resp:
            refine_status = resp.status
            refine_data = resp.read()
            refine_headers = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        refine_status = exc.code
        refine_data = exc.read()
        refine_headers = dict(exc.headers)
    write_out("refine.out", refine_status, refine_data, refine_headers)
    if refine_status == 200:
        refine_json = json.loads(refine_data.decode("utf-8", errors="replace"))
        assert_workflow_metadata("requirements_refine", refine_json)

    enrich_payload = {
        "requirements": requirements,
        "app_link": "https://example.test/app",
        "prototype_link": "https://example.test/proto",
        "diagram_links": ["https://example.test/diag1"],
        "image_links": ["https://example.test/img1"],
        "notes": "e2e verification run",
    }
    status, body, headers = post_json("/requirements/enrich", enrich_payload)
    write_out("enrich.out", status, body, headers)

    generate_payload = {
        "requirements": requirements,
        "template": {
            "name": "default",
            "format": "table",
            "fields": [
                "id",
                "title",
                "description",
                "priority",
                "type",
                "status",
                "preconditions",
                "steps",
                "expected_result",
                "test_data",
                "estimated_time",
                "automation_status",
                "component",
                "tags",
            ],
        },
        "context": enrich_payload,
        "feedback": None,
        "workflow_settings": WORKFLOW_SETTINGS,
    }
    status, body, headers = post_json("/testcases/generate", generate_payload)
    write_out("generate.out", status, body, headers)

    generated_data = {"test_cases": []}
    if status == 200:
        try:
            generated_data = json.loads(body.decode("utf-8", errors="replace"))
            assert_workflow_metadata("testcases_generate", generated_data)
        except json.JSONDecodeError:
            pass

    if generated_data.get("test_cases"):
        refine_test_case_payload = {
            "requirements": requirements,
            "test_cases": generated_data.get("test_cases", []),
            "template": generate_payload["template"],
            "context": enrich_payload,
            "feedback": "Tighten expected results and keep at least one negative-path case per requirement.",
            "workflow_settings": WORKFLOW_SETTINGS,
        }
        status, body, headers = post_json("/testcases/refine", refine_test_case_payload)
        write_out("refine_testcases.out", status, body, headers)
        if status == 200:
            refined_test_cases = json.loads(body.decode("utf-8", errors="replace"))
            assert_workflow_metadata("testcases_refine", refined_test_cases)

    endpoints = [
        ("/export/csv", "export_csv.out", generated_data),
        ("/export/excel", "export_excel.out", generated_data),
        ("/export/json", "export_json.out", generated_data),
        (
            "/export/jira",
            "export_jira.out",
            {
                "project_key": "QA",
                "issue_type": "Test",
                "test_cases": generated_data.get("test_cases", []),
            },
        ),
        (
            "/automation/playwright",
            "automation.out",
            {
                "test_cases": generated_data.get("test_cases", []),
                "target_base_url": "https://example.test/app",
            },
        ),
    ]

    for path, outfile, payload in endpoints:
        status, body, headers = post_json(path, payload)
        write_out(outfile, status, body, headers)

    print("verification calls complete")


if __name__ == "__main__":
    main()
