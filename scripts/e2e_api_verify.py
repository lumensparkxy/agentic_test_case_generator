import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT_DIR = "/tmp/tcagent_api_verify"


def read_parse_requirements() -> list[dict]:
    with open(f"{OUT_DIR}/parse.out", "r", encoding="utf-8") as f:
        raw = f.read()
    parse_json = raw.split("HTTP_STATUS:")[0].strip()
    data = json.loads(parse_json)
    return data["requirements"]


def post_json(path: str, payload: dict) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
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
    requirements = read_parse_requirements()

    refine_payload = {
        "feedback": "Make wording concise and keep requirements strictly testable.",
        "existing_requirements": json.dumps(requirements),
    }
    refine_body = urllib.parse.urlencode(refine_payload).encode("utf-8")
    refine_req = urllib.request.Request(
        BASE + "/requirements/parse",
        data=refine_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
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
    }
    status, body, headers = post_json("/testcases/generate", generate_payload)
    write_out("generate.out", status, body, headers)

    generated_data = {"test_cases": []}
    if status == 200:
        try:
            generated_data = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            pass

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
