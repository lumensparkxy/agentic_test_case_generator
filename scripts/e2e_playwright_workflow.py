#!/usr/bin/env python3
"""
E2E Workflow Script – Playwright Docs Requirements
====================================================
Runs the complete agentic test case generator pipeline:
  1.  Mint a local JWT so the backend accepts requests (no Google OAuth needed)
  2.  Parse requirements from playwright_docs_requirements.md  → /requirements/parse
  3.  Optionally refine requirements with human feedback       → /requirements/parse (refine)
  4.  Enrich requirements with grounded context               → /requirements/enrich
  5.  Generate test cases (LLM + validation loop)             → /testcases/generate
  6.  Export  → CSV, Excel (.xlsx), JSON                      → /export/{csv,excel,json}
  7.  Generate Playwright POM stubs                           → /automation/playwright
  8.  Print a final summary report

Usage:
    cd /Users/m1/learn_python/agentic_test_case_generator
    source .venv/bin/activate
    python scripts/e2e_playwright_workflow.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional rich output
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Table

    _console = Console()

    def _print(msg: str) -> None:
        _console.print(msg)

    def _panel(title: str, content: str, style: str = "cyan") -> None:
        _console.print(Panel(content, title=title, border_style=style))

except ImportError:
    _console = None  # type: ignore[assignment]

    def _print(msg: str) -> None:  # type: ignore[misc]
        print(msg)

    def _panel(title: str, content: str, style: str = "cyan") -> None:  # type: ignore[misc]
        print(f"\n{'='*60}\n{title}\n{'='*60}\n{content}\n")


# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_MD = REPO_ROOT / "scripts" / "playwright_docs_requirements.md"
OUTPUT_DIR = Path("/tmp/pw_workflow_out")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 600  # seconds – LLM pipeline may be slow


# ---------------------------------------------------------------------------
# JWT minting (matches backend jwt_auth.py logic exactly)
# ---------------------------------------------------------------------------
def _mint_jwt() -> str:
    """Create a signed JWT that the backend will accept as a valid user token."""
    try:
        import jwt  # PyJWT is in the project venv
    except ImportError:
        _print("[red]PyJWT not found. Make sure .venv is activated.[/red]")
        sys.exit(1)

    env_path = REPO_ROOT / ".env"
    secret = ""
    algorithm = "HS256"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("JWT_SECRET_KEY="):
                secret = line.split("=", 1)[1].strip()
            if line.startswith("JWT_ALGORITHM="):
                algorithm = line.split("=", 1)[1].strip()

    if not secret:
        secret = os.getenv("JWT_SECRET_KEY", "")
    if not secret:
        _print("[red]JWT_SECRET_KEY not found in .env or environment.[/red]")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "e2e-test-user",
        "email": "e2e@playwright.test",
        "name": "E2E Workflow Test",
        "picture": None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=2)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
_TOKEN: str = ""


def _headers(extra: dict | None = None) -> dict:
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {_TOKEN}"}
    if extra:
        h.update(extra)
    return h


def _post_json(path: str, payload: dict) -> tuple[int, dict | list]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE_URL + path, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            detail = json.loads(body)
        except Exception:
            detail = body.decode(errors="replace")
        return exc.code, {"error": detail}


def _post_multipart(path: str, fields: dict, files: dict) -> tuple[int, dict]:
    """Minimal multipart/form-data POST for file uploads."""
    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    body_parts: list[bytes] = []

    for name, value in fields.items():
        body_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )

    for field_name, (filename, file_bytes, content_type) in files.items():
        body_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + file_bytes
            + b"\r\n"
        )

    body_parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(body_parts)

    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_err = exc.read()
        try:
            detail = json.loads(body_err)
        except Exception:
            detail = body_err.decode(errors="replace")
        return exc.code, {"error": detail}


def _post_download(path: str, payload: dict, out_filename: str, expected_ctype: str) -> str:
    """POST and save a binary/text response to disk, returns output path."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE_URL + path, data=data, headers=_headers(), method="POST")
    out_path = OUTPUT_DIR / out_filename
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            out_path.write_bytes(resp.read())
            return str(out_path)
    except urllib.error.HTTPError as exc:
        err_body = exc.read()
        out_path.write_bytes(err_body)
        _print(f"[red]Download failed ({exc.code}): {err_body[:200]}[/red]")
        return str(out_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _save_json(name: str, data: object) -> Path:
    p = OUTPUT_DIR / name
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return p


def _check_health() -> None:
    try:
        with urllib.request.urlopen(BASE_URL + "/health", timeout=10) as r:
            body = json.loads(r.read())
            if body.get("status") != "ok":
                raise RuntimeError(body)
    except Exception as exc:
        _print(f"[red]Backend health check failed: {exc}[/red]")
        _print("Make sure the backend is running: uvicorn app.main:app --reload --app-dir backend")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------

def step_parse_requirements() -> dict:
    """Step 1 – Parse the markdown requirements file via the pipeline."""
    _print("\n[bold cyan]STEP 1 – Parse Requirements[/bold cyan]")
    md_bytes = REQUIREMENTS_MD.read_bytes()
    status, result = _post_multipart(
        "/requirements/parse",
        fields={},
        files={"file": ("playwright_docs_requirements.md", md_bytes, "text/markdown")},
    )
    _print(f"  Status: {status}")
    if status != 200:
        _print(f"  [red]Error: {result}[/red]")
        sys.exit(1)

    reqs = result.get("requirements", [])
    _print(f"  Parsed [bold]{len(reqs)}[/bold] requirements")
    _print(f"  Approved: {result.get('approved')}")
    review = result.get('review', {})
    if isinstance(review, dict) and review.get('summary'):
        _print(f"  Review summary: {str(review['summary'])[:200]}")
        _print(f"  Score: {review.get('score', '?')}/{review.get('threshold', '?')}")

    # Coverage metrics
    if coverage := result.get('coverage_metrics'):
        _print(f"  Coverage metrics: {json.dumps(coverage)}")

    # Iteration history
    history = result.get('iteration_history', [])
    if not isinstance(history, list):
        history = []
    _print(f"  Iterations taken: {len(history)}")

    _save_json("1_parse.json", result)
    return result


def step_refine_requirements(parse_result: dict) -> dict:
    """Step 2 – Human feedback loop: refine the extracted requirements."""
    _print("\n[bold cyan]STEP 2 – Refine Requirements (Human-in-the-loop)[/bold cyan]")

    feedback = (
        "Focus on testable functional behaviours. "
        "Remove purely informational requirements. "
        "Split compound requirements. "
        "Ensure each requirement has a clear verifiable outcome."
    )
    _print(f"  Feedback: {feedback}")

    import urllib.parse  # noqa: PLC0415

    existing_json = json.dumps(parse_result["requirements"])
    fields = {"feedback": feedback, "existing_requirements": existing_json}
    encoded_body = urllib.parse.urlencode(fields).encode()

    req = urllib.request.Request(
        BASE_URL + "/requirements/parse",
        data=encoded_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            result = json.loads(exc.read())
        except Exception:
            result = {"error": exc.read().decode(errors="replace")}

    _print(f"  Status: {status}")
    if status != 200:
        _print(f"  [red]Error: {result}[/red]")
        _print("  [yellow]Continuing with original parse result...[/yellow]")
        return parse_result

    reqs = result.get("requirements", [])
    _print(f"  Refined to [bold]{len(reqs)}[/bold] requirements")
    _print(f"  Approved: {result.get('approved')}")
    review = result.get('review', {})
    if isinstance(review, dict) and review.get('summary'):
        _print(f"  Review summary: {str(review['summary'])[:200]}")

    _save_json("2_refine.json", result)
    return result


def step_enrich_requirements(requirements: list[dict]) -> dict:
    """Step 3 – Enrich requirements with grounded context."""
    _print("\n[bold cyan]STEP 3 – Enrich Requirements with Context[/bold cyan]")

    payload = {
        "requirements": requirements,
        "app_link": "https://playwright.dev/python/docs/intro",
        "prototype_link": "https://playwright.dev/python/docs/writing-tests",
        "diagram_links": [
            "https://playwright.dev/python/docs/running-tests",
            "https://playwright.dev/python/docs/locators",
        ],
        "image_links": [],
        "notes": (
            "This is the Playwright for Python official documentation. "
            "Focus on pytest plugin usage patterns, assertion APIs, and browser lifecycle management."
        ),
    }

    status, result = _post_json("/requirements/enrich", payload)
    _print(f"  Status: {status}")
    if status != 200:
        _print(f"  [red]Error: {result}[/red]")
        _print("  [yellow]Continuing without enrichment...[/yellow]")
        return {"requirements": requirements, "grounded_context": ""}

    grounded = result.get("grounded_context", {})
    grounded_summary = ""
    if isinstance(grounded, dict):
        grounded_summary = grounded.get("summary") or ""
        artifact_count = len(grounded.get("artifact_sources", []))
        ui_count = len(grounded.get("ui_elements", []))
        _print(f"  Grounded context: summary={'yes' if grounded_summary else 'no'}, "
               f"artifacts={artifact_count}, ui_elements={ui_count}")
    elif isinstance(grounded, str):
        grounded_summary = grounded
        _print(f"  Grounded context (string) length: {len(grounded_summary)} chars")

    if grounded_summary:
        _print(f"  Context summary: {str(grounded_summary)[:300]}")

    _save_json("3_enrich.json", result)
    return result


def step_generate_test_cases(enriched: dict) -> dict:
    """Step 4 – Generate test cases with the multi-agent validation loop."""
    _print("\n[bold cyan]STEP 4 – Generate Test Cases (Agent Loop)[/bold cyan]")
    _print("  This may take several minutes – LLM + validation pipeline is running...")

    # Build context as EnrichInput-compatible dict
    context = {
        "requirements": enriched.get("requirements", []),
        "app_link": "https://playwright.dev/python/docs/intro",
        "prototype_link": "https://playwright.dev/python/docs/writing-tests",
        "diagram_links": [
            "https://playwright.dev/python/docs/running-tests",
            "https://playwright.dev/python/docs/locators",
        ],
        "notes": (
            "Playwright for Python pytest plugin. Focus on: installation, "
            "test structure, locator APIs, assertions, browser contexts, "
            "headed/headless, multi-browser, parallel execution, debugging."
        ),
        "grounded_context": enriched.get("grounded_context") or None,
    }

    payload = {
        "requirements": enriched.get("requirements", []),
        "context": context,
        "template": {
            "name": "default",
            "format": "table",
            "fields": [
                "id", "title", "description", "priority", "type",
                "status", "preconditions", "steps", "expected_result",
                "test_data", "estimated_time", "automation_status",
                "component", "tags",
            ],
        },
    }

    t0 = time.time()
    status, result = _post_json("/testcases/generate", payload)
    elapsed = time.time() - t0
    _print(f"  Status: {status}  ({elapsed:.1f}s)")

    if status != 200:
        _print(f"  [red]Error: {result}[/red]")
        sys.exit(1)

    tcs = result.get("test_cases", [])
    _print(f"  Generated [bold]{len(tcs)}[/bold] test cases")
    _print(f"  Approved: {result.get('approved')}")
    review = result.get('review', {})
    if isinstance(review, dict) and review.get('summary'):
        _print(f"  Review summary: {str(review['summary'])[:300]}")
        _print(f"  Score: {review.get('score', '?')}/{review.get('threshold', '?')}")

    if tcs:
        # Show a summary table
        _print("\n  [bold]Test Case Summary:[/bold]")
        priorities = {}
        types = {}
        for tc in tcs:
            p = tc.get("priority", "Unknown")
            t = tc.get("type", "Unknown")
            priorities[p] = priorities.get(p, 0) + 1
            types[t] = types.get(t, 0) + 1
        _print(f"  Priorities: {dict(sorted(priorities.items()))}")
        _print(f"  Types:      {dict(sorted(types.items()))}")

        # Show first 3 test cases
        _print("\n  [bold]First 3 test cases:[/bold]")
        for tc in tcs[:3]:
            _print(f"    [{tc.get('priority','?')}] {tc.get('id','?')} – {tc.get('title','?')}")
            steps = tc.get("steps", [])
            _print(f"      Steps: {len(steps)}, AutoStatus: {tc.get('automation_status','?')}")

    _save_json("4_generate.json", result)
    return result


def step_export(generate_result: dict) -> None:
    """Step 5 – Export to CSV, Excel and JSON."""
    _print("\n[bold cyan]STEP 5 – Export Test Cases[/bold cyan]")

    # The export endpoints accept a GenerateTestCasesResponse body
    payload = generate_result

    # CSV
    csv_path = _post_download("/export/csv", payload, "5a_test_cases.csv", "text/csv")
    csv_size = os.path.getsize(csv_path)
    _print(f"  CSV  saved → {csv_path}  ({csv_size:,} bytes)")
    # Show first few lines
    try:
        with open(csv_path) as f:
            lines = f.readlines()[:5]
        _print(f"  CSV preview (first {len(lines)} lines):")
        for line in lines:
            _print(f"    {line.rstrip()}")
    except Exception:
        pass

    # Excel
    xlsx_path = _post_download(
        "/export/excel", payload, "5b_test_cases.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    xlsx_size = os.path.getsize(xlsx_path)
    _print(f"  XLSX saved → {xlsx_path}  ({xlsx_size:,} bytes)")

    # JSON
    json_path = _post_download("/export/json", payload, "5c_test_cases.json", "application/json")
    json_size = os.path.getsize(json_path)
    _print(f"  JSON saved → {json_path}  ({json_size:,} bytes)")


def step_playwright_pom(generate_result: dict) -> dict:
    """Step 6 – Generate Playwright POM stubs from the test cases."""
    _print("\n[bold cyan]STEP 6 – Generate Playwright POM Stubs[/bold cyan]")

    payload = {
        "test_cases": generate_result.get("test_cases", []),
        "target_base_url": "https://playwright.dev/python/",
    }

    status, result = _post_json("/automation/playwright", payload)
    _print(f"  Status: {status}")

    if status != 200:
        _print(f"  [red]Error: {result}[/red]")
        return {}

    pom_code = result.get("notes", "")
    pom_files = result.get("files", [])
    _print(f"  Status: {result.get('status', '?')}")
    _print(f"  Files declared: {pom_files}")
    _print(f"  POM code length: {len(pom_code)} chars")
    if pom_code and not pom_code.startswith("POM generation"):
        # Save to disk
        pom_path = OUTPUT_DIR / "6_playwright_pom.py"
        pom_path.write_text(pom_code)
        _print(f"  POM saved → {pom_path}")
        _print("\n  [bold]POM preview (first 60 lines):[/bold]")
        for line in pom_code.splitlines()[:60]:
            _print(f"    {line}")

    _save_json("6_pom.json", result)
    return result


def _print_summary(parse: dict, refined: dict, enriched: dict, generated: dict) -> None:
    """Final consolidated summary."""
    _print("\n")
    _panel(
        "WORKFLOW COMPLETE – Summary",
        "\n".join([
            f"Requirements parsed   : {len(parse.get('requirements', []))}",
            f"Requirements refined  : {len(refined.get('requirements', []))}",
            f"Requirements enriched : {len(enriched.get('requirements', []))}",
            f"Test cases generated  : {len(generated.get('test_cases', []))}",
            f"",
            f"Output directory      : {OUTPUT_DIR}",
            f"Files produced:",
            f"  1_parse.json       – raw parse workflow state",
            f"  2_refine.json      – human feedback refinement",
            f"  3_enrich.json      – grounded context enrichment",
            f"  4_generate.json    – full test case generation",
            f"  5a_test_cases.csv  – CSV export",
            f"  5b_test_cases.xlsx – Excel export",
            f"  5c_test_cases.json – JSON export",
            f"  6_playwright_pom.py– Playwright POM stubs",
        ]),
        style="green",
    )

    tcs = generated.get("test_cases", [])
    if tcs:
        _print("\n[bold]All Generated Test Cases:[/bold]")
        for tc in tcs:
            _print(
                f"  {tc.get('id','?'):<12} [{tc.get('priority','?'):<6}] "
                f"{tc.get('type','?'):<20} {tc.get('title','?')}"
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _TOKEN

    _print("\n[bold green]Agentic Test Case Generator – E2E Playwright Docs Workflow[/bold green]")
    _print(f"  Requirements source : {REQUIREMENTS_MD}")
    _print(f"  Output directory    : {OUTPUT_DIR}")
    _print(f"  Backend URL         : {BASE_URL}\n")

    # Mint token
    _TOKEN = _mint_jwt()
    _print(f"[dim]JWT minted for e2e@playwright.test[/dim]")

    # Health check
    _check_health()
    _print("[green]Backend health check: OK[/green]")

    # Step 1 – Parse
    parse_result = step_parse_requirements()

    # Step 2 – Refine (human-in-the-loop)
    refined_result = step_refine_requirements(parse_result)

    # Step 3 – Enrich with grounded context
    enriched_result = step_enrich_requirements(refined_result.get("requirements", []))

    # Step 4 – Generate test cases
    generated_result = step_generate_test_cases(enriched_result)

    # Step 5 – Export
    step_export(generated_result)

    # Step 6 – Playwright POM stubs
    step_playwright_pom(generated_result)

    # Final summary
    _print_summary(parse_result, refined_result, enriched_result, generated_result)


if __name__ == "__main__":
    main()
