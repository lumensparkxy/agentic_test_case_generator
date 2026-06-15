from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

RUN_GATE_ENV = "RUN_REAL_ADK_E2E"
AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT = "firebase-or-backend-jwt"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_OUTPUT_BASE_DIR = Path("/tmp/agentic-tcg-real-adk-e2e")
DEFAULT_REQUIREMENTS_FILE = REPO_ROOT / "scripts" / "playwright_docs_requirements.md"
DEFAULT_TARGET_URL = "https://playwright.dev/python/"
DEFAULT_TIMEOUT_SECONDS = 600
EXECUTION_RUNTIME_MARKER = REPO_ROOT / "backend" / "execution_runtime" / "node_modules" / "@playwright" / "test"


class RealAdkE2EError(RuntimeError):
    """Raised when the opt-in real ADK E2E harness cannot proceed."""


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RealAdkWorkflowConfig:
    base_url: str = DEFAULT_BASE_URL
    output_base_dir: Path = DEFAULT_OUTPUT_BASE_DIR
    requirements_file: Path = DEFAULT_REQUIREMENTS_FILE
    target_url: str = DEFAULT_TARGET_URL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    run_id: str = field(default_factory=_new_run_id)
    require_execution_runtime: bool = True

    @property
    def output_dir(self) -> Path:
        return self.output_base_dir / self.run_id


@dataclass(frozen=True)
class RealAdkWorkflowResult:
    output_dir: Path
    summary_path: Path
    summary: dict[str, Any]


def real_adk_e2e_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return values.get(RUN_GATE_ENV, "").strip() == "1"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def merged_environment(env: Mapping[str, str] | None = None, *, env_file: Path | None = DEFAULT_ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None:
        values.update(_read_env_file(env_file))
    values.update(dict(os.environ if env is None else env))
    return values


def _resolve_path(raw_value: str | None, default: Path) -> Path:
    if not raw_value:
        return default
    path = Path(raw_value).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _parse_timeout(raw_value: str | None) -> int:
    if raw_value is None or not str(raw_value).strip():
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = int(str(raw_value).strip())
    except ValueError as exc:
        raise RealAdkE2EError("REAL_ADK_E2E_TIMEOUT_SECONDS must be a positive integer") from exc
    if timeout <= 0:
        raise RealAdkE2EError("REAL_ADK_E2E_TIMEOUT_SECONDS must be a positive integer")
    return timeout


def config_from_env(env: Mapping[str, str] | None = None, *, env_file: Path | None = DEFAULT_ENV_FILE) -> RealAdkWorkflowConfig:
    values = merged_environment(env, env_file=env_file)
    return RealAdkWorkflowConfig(
        base_url=(values.get("REAL_ADK_E2E_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/"),
        output_base_dir=_resolve_path(values.get("REAL_ADK_E2E_OUTPUT_DIR"), DEFAULT_OUTPUT_BASE_DIR),
        requirements_file=_resolve_path(values.get("REAL_ADK_E2E_REQUIREMENTS_FILE"), DEFAULT_REQUIREMENTS_FILE),
        target_url=(values.get("REAL_ADK_E2E_TARGET_URL") or DEFAULT_TARGET_URL).strip(),
        timeout_seconds=_parse_timeout(values.get("REAL_ADK_E2E_TIMEOUT_SECONDS")),
    )


def validate_environment(
    config: RealAdkWorkflowConfig,
    env: Mapping[str, str] | None = None,
    *,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> None:
    values = merged_environment(env, env_file=env_file)
    missing: list[str] = []

    if not (values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY")):
        missing.append("GEMINI_API_KEY or GOOGLE_API_KEY")

    if not values.get("AUTH_TOKEN"):
        if not values.get("JWT_SECRET_KEY"):
            missing.append("JWT_SECRET_KEY (or provide AUTH_TOKEN)")
        if values.get("AUTH_TOKEN_MODE") != AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT:
            missing.append(f"AUTH_TOKEN_MODE={AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT} (or provide AUTH_TOKEN)")

    if not config.requirements_file.is_file():
        missing.append(f"requirements file not found: {config.requirements_file}")

    if config.require_execution_runtime and not EXECUTION_RUNTIME_MARKER.is_dir():
        missing.append("backend/execution_runtime dependencies missing (run: cd backend/execution_runtime && npm ci)")

    if config.timeout_seconds <= 0:
        missing.append("REAL_ADK_E2E_TIMEOUT_SECONDS must be a positive integer")

    if missing:
        formatted = "\n- ".join(missing)
        raise RealAdkE2EError(f"Real ADK E2E preconditions are not satisfied:\n- {formatted}")


def mint_auth_token(env: Mapping[str, str] | None = None, *, env_file: Path | None = DEFAULT_ENV_FILE) -> str:
    values = merged_environment(env, env_file=env_file)
    configured_token = values.get("AUTH_TOKEN", "").strip()
    if configured_token:
        return configured_token

    if values.get("AUTH_TOKEN_MODE") != AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT:
        raise RealAdkE2EError(
            f"Local JWT minting requires AUTH_TOKEN_MODE={AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT}; provide AUTH_TOKEN for Firebase-token runs."
        )

    secret = values.get("JWT_SECRET_KEY", "").strip()
    if not secret:
        raise RealAdkE2EError("JWT_SECRET_KEY is required to mint a local backend JWT when AUTH_TOKEN is not provided.")

    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - dependency setup failure
        raise RealAdkE2EError("PyJWT is required to mint local backend JWTs. Activate .venv and install backend requirements.") from exc

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "real-adk-e2e-user",
        "email": "real-adk-e2e@example.com",
        "name": "Real ADK E2E",
        "picture": None,
        "organization_domain": None,
        "tenant_id": None,
        "roles": ["tester"],
        "is_org_admin": False,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=2)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=values.get("JWT_ALGORITHM", "HS256") or "HS256")


def write_json_artifact(output_dir: Path, filename: str, payload: Any) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def write_bytes_artifact(output_dir: Path, filename: str, payload: bytes) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_bytes(payload)
    return path


def write_text_artifact(output_dir: Path, filename: str, payload: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(payload, encoding="utf-8")
    return path


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _collect_warnings(*payloads: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for payload in payloads:
        diagnostics = _dict(payload.get("workflow_diagnostics"))
        for warning in _sequence(diagnostics.get("warnings")):
            if str(warning).strip():
                warnings.append(str(warning))
        for warning in _sequence(payload.get("warnings")):
            if str(warning).strip():
                warnings.append(str(warning))
    return warnings


def build_summary(
    *,
    config: RealAdkWorkflowConfig,
    artifacts: Mapping[str, Path],
    parse_result: Mapping[str, Any],
    enrich_result: Mapping[str, Any],
    generate_result: Mapping[str, Any],
    automation_result: Mapping[str, Any],
    preview_result: Mapping[str, Any],
    exports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    review = _dict(generate_result.get("review"))
    grounded_context = _dict(enrich_result.get("grounded_context"))
    preview_summary = _dict(preview_result.get("summary"))
    automation_notes = str(automation_result.get("notes") or "")

    return {
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": config.base_url,
        "target_url": config.target_url,
        "requirements_file": str(config.requirements_file),
        "preview_only": True,
        "counts": {
            "requirements": len(_sequence(parse_result.get("requirements"))),
            "grounded_artifact_sources": len(_sequence(grounded_context.get("artifact_sources"))),
            "grounded_ui_elements": len(_sequence(grounded_context.get("ui_elements"))),
            "requirement_analysis": len(_sequence(generate_result.get("requirement_analysis"))),
            "coverage_plan": len(_sequence(generate_result.get("coverage_plan"))),
            "test_cases": len(_sequence(generate_result.get("test_cases"))),
        },
        "approval": {
            "approved": bool(generate_result.get("approved")),
            "score": review.get("score"),
            "threshold": review.get("threshold"),
            "summary": review.get("summary"),
            "blocking_issues": _sequence(review.get("blocking_issues")),
        },
        "automation": {
            "status": automation_result.get("status"),
            "files": _sequence(automation_result.get("files")),
            "notes_characters": len(automation_notes),
        },
        "execution_preview": preview_summary,
        "exports": {name: dict(details) for name, details in exports.items()},
        "artifact_paths": {name: str(path) for name, path in artifacts.items()},
        "warnings": _collect_warnings(parse_result, generate_result, preview_result),
    }


class RealAdkWorkflowRunner:
    def __init__(
        self,
        config: RealAdkWorkflowConfig,
        *,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = DEFAULT_ENV_FILE,
    ) -> None:
        self.config = config
        self.env = env
        self.env_file = env_file
        self._token = ""

    def run(self) -> RealAdkWorkflowResult:
        validate_environment(self.config, self.env, env_file=self.env_file)
        self._token = mint_auth_token(self.env, env_file=self.env_file)

        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, Path] = {}

        health = self._get_json("/health")
        artifacts["health"] = write_json_artifact(output_dir, "00_health.json", health)
        if health.get("status") != "ok":
            raise RealAdkE2EError(f"Backend health check did not return status=ok. Artifact: {artifacts['health']}")

        parse_result = self._parse_requirements()
        artifacts["parse"] = write_json_artifact(output_dir, "01_parse.json", parse_result)
        self._assert_non_empty(parse_result, "requirements", "requirements parsing", artifacts["parse"])

        enrich_result = self._enrich_requirements(parse_result)
        artifacts["enrich"] = write_json_artifact(output_dir, "02_enrich.json", enrich_result)
        grounded_context = _dict(enrich_result.get("grounded_context"))
        if not grounded_context:
            raise RealAdkE2EError(f"Requirements enrichment did not return grounded_context. Artifact: {artifacts['enrich']}")

        generate_result = self._generate_test_cases(enrich_result)
        artifacts["generate"] = write_json_artifact(output_dir, "03_generate.json", generate_result)
        self._assert_non_empty(generate_result, "test_cases", "test-case generation", artifacts["generate"])
        self._assert_non_empty(generate_result, "coverage_plan", "use-case coverage generation", artifacts["generate"])
        self._assert_non_empty(generate_result, "requirement_analysis", "requirement analysis generation", artifacts["generate"])

        automation_result = self._generate_playwright_pom(generate_result)
        artifacts["playwright_pom"] = write_json_artifact(output_dir, "04_playwright_pom.json", automation_result)
        if automation_result.get("status") != "generated":
            raise RealAdkE2EError(f"Playwright automation generation did not return status=generated. Artifact: {artifacts['playwright_pom']}")
        notes = str(automation_result.get("notes") or "")
        if notes:
            artifacts["playwright_pom_source"] = write_text_artifact(output_dir, "04_playwright_pom.py", notes)

        preview_result = self._preview_execution(generate_result)
        artifacts["execution_preview"] = write_json_artifact(output_dir, "05_execution_preview.json", preview_result)
        if not _dict(preview_result.get("summary")):
            raise RealAdkE2EError(f"Execution preview did not return a summary. Artifact: {artifacts['execution_preview']}")

        exports = self._export_test_cases(output_dir, generate_result, artifacts)
        summary = build_summary(
            config=self.config,
            artifacts=artifacts,
            parse_result=parse_result,
            enrich_result=enrich_result,
            generate_result=generate_result,
            automation_result=automation_result,
            preview_result=preview_result,
            exports=exports,
        )
        summary_path = write_json_artifact(output_dir, "summary_report.json", summary)
        artifacts["summary"] = summary_path
        summary["artifact_paths"]["summary"] = str(summary_path)
        write_json_artifact(output_dir, "summary_report.json", summary)
        return RealAdkWorkflowResult(output_dir=output_dir, summary_path=summary_path, summary=summary)

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(self._url(path), method="GET")
        return self._send_json_request(request, path)

    def _post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url(path),
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        return self._send_json_request(request, path)

    def _post_multipart_file(self, path: str, *, field_name: str, file_path: Path) -> dict[str, Any]:
        boundary = f"----agentic-tcg-{uuid.uuid4().hex}"
        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_bytes = file_path.read_bytes()
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'.encode("utf-8"),
                f"Content-Type: {media_type}\r\n\r\n".encode("utf-8"),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        request = urllib.request.Request(
            self._url(path),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
        )
        return self._send_json_request(request, path)

    def _post_download(self, path: str, payload: Mapping[str, Any], *, filename: str) -> bytes:
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url(path),
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(path, exc) from exc
        except urllib.error.URLError as exc:
            raise RealAdkE2EError(f"Could not connect to backend at {self.config.base_url} for {path}: {exc.reason}") from exc

        if not data:
            raise RealAdkE2EError(f"{path} returned an empty export for {filename}")
        return data

    def _send_json_request(self, request: urllib.request.Request, path: str) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(path, exc) from exc
        except urllib.error.URLError as exc:
            raise RealAdkE2EError(f"Could not connect to backend at {self.config.base_url} for {path}: {exc.reason}") from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            preview = raw.decode("utf-8", errors="replace")[:1000]
            raise RealAdkE2EError(f"{path} did not return JSON. Response preview: {preview}") from exc
        if not isinstance(decoded, dict):
            raise RealAdkE2EError(f"{path} returned JSON but not an object.")
        return decoded

    def _http_error(self, path: str, exc: urllib.error.HTTPError) -> RealAdkE2EError:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        return RealAdkE2EError(f"{path} returned HTTP {exc.code}: {body}")

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def _parse_requirements(self) -> dict[str, Any]:
        return self._post_multipart_file("/requirements/parse", field_name="file", file_path=self.config.requirements_file)

    def _enrich_requirements(self, parse_result: Mapping[str, Any]) -> dict[str, Any]:
        requirements = _sequence(parse_result.get("requirements"))
        payload = {
            "requirements": requirements,
            "app_link": self.config.target_url,
            "prototype_link": f"{self.config.target_url.rstrip('/')}/docs/writing-tests",
            "diagram_links": [
                f"{self.config.target_url.rstrip('/')}/docs/running-tests",
                f"{self.config.target_url.rstrip('/')}/docs/locators",
            ],
            "notes": "Real ADK E2E smoke context for Playwright for Python documentation workflows.",
        }
        return self._post_json("/requirements/enrich", payload)

    def _generate_test_cases(self, enrich_result: Mapping[str, Any]) -> dict[str, Any]:
        requirements = _sequence(enrich_result.get("requirements"))
        context = {
            "requirements": requirements,
            "app_link": self.config.target_url,
            "prototype_link": f"{self.config.target_url.rstrip('/')}/docs/writing-tests",
            "diagram_links": [
                f"{self.config.target_url.rstrip('/')}/docs/running-tests",
                f"{self.config.target_url.rstrip('/')}/docs/locators",
            ],
            "notes": (
                "Playwright for Python pytest plugin. Focus on installation, test structure, locator APIs, assertions, browser contexts, "
                "headed/headless runs, multi-browser coverage, parallel execution, and debugging."
            ),
            "grounded_context": enrich_result.get("grounded_context") or None,
        }
        payload = {
            "requirements": requirements,
            "context": context,
            "template": {
                "name": "real-adk-e2e-default",
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
        }
        return self._post_json("/testcases/generate", payload)

    def _generate_playwright_pom(self, generate_result: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "test_cases": _sequence(generate_result.get("test_cases")),
            "target_base_url": self.config.target_url,
        }
        return self._post_json("/automation/playwright", payload)

    def _preview_execution(self, generate_result: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "test_cases": _sequence(generate_result.get("test_cases")),
            "target_base_url": self.config.target_url,
            "target_environment": "real-adk-e2e-preview",
        }
        return self._post_json("/automation/execution/preview", payload)

    def _export_test_cases(
        self,
        output_dir: Path,
        generate_result: Mapping[str, Any],
        artifacts: dict[str, Path],
    ) -> dict[str, dict[str, Any]]:
        approved = bool(generate_result.get("approved"))
        payload = {
            "test_cases": _sequence(generate_result.get("test_cases")),
            "approved": approved,
            "review": _dict(generate_result.get("review")),
            "draft_override_requested": not approved,
            "draft_override_reason": None if approved else "Real ADK E2E artifact export for smoke validation.",
        }

        exports: dict[str, dict[str, Any]] = {}
        for name, path, filename in (
            ("json", "/export/json", "06_export.json"),
            ("csv", "/export/csv", "07_export.csv"),
            ("excel", "/export/excel", "08_export.xlsx"),
        ):
            content = self._post_download(path, payload, filename=filename)
            artifacts[f"export_{name}"] = write_bytes_artifact(output_dir, filename, content)
            exports[name] = {"path": str(artifacts[f"export_{name}"]), "bytes": len(content)}
        return exports

    @staticmethod
    def _assert_non_empty(payload: Mapping[str, Any], key: str, step_name: str, artifact_path: Path) -> None:
        if not _sequence(payload.get(key)):
            raise RealAdkE2EError(f"{step_name} did not produce non-empty {key}. Artifact: {artifact_path}")
