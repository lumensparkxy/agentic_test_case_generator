"""Local Playwright Test runner for generated TypeScript specs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Mapping, Sequence
from urllib.parse import urlparse

from plain_english_test_framework.ir_validator import parse_ir_file
from plain_english_test_framework.playwright_generator import generate_playwright_spec_file
from plain_english_test_framework.validation import ValidationIssue


DEFAULT_GENERATED_DIR = Path("generated/playwright")
DEFAULT_ARTIFACTS_DIR = Path("artifacts/playwright")
DEFAULT_CONFIG_PATH = Path("playwright.config.ts")
EXAMPLE_APP_BASE_URL = "http://127.0.0.1:41731"
EXAMPLE_APP_COMMAND = "node examples/apps/calculator/server.mjs"


class LocalPlaywrightRunnerError(Exception):
    """Raised when the local Playwright runner cannot start or mutates generated specs."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class LocalPlaywrightPaths:
    """Predictable local paths used by the runner and Playwright config."""

    generated_dir: Path
    artifacts_dir: Path
    test_results_dir: Path
    html_report_dir: Path


@dataclass(frozen=True)
class LocalPlaywrightRun:
    """Completed local Playwright invocation metadata."""

    command: tuple[str, ...]
    generated_spec_path: Path | None
    generated_spec_paths: tuple[Path, ...]
    paths: LocalPlaywrightPaths
    returncode: int
    stdout: str
    stderr: str


def generate_local_playwright_spec(
    ir_path: str | Path,
    *,
    generated_dir: str | Path = DEFAULT_GENERATED_DIR,
) -> Path:
    """Generate one TypeScript Playwright spec file from one IR file."""

    generated = generate_playwright_spec_file(ir_path)
    output_dir = Path(generated_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{generated.spec_id}.spec.ts"
    output_path.write_text(generated.contents, encoding="utf-8")
    return output_path


def run_local_playwright(
    ir_path: str | Path,
    *,
    generated_dir: str | Path = DEFAULT_GENERATED_DIR,
    artifacts_dir: str | Path = DEFAULT_ARTIFACTS_DIR,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    extra_args: Sequence[str] = (),
    cwd: str | Path | None = None,
) -> LocalPlaywrightRun:
    """Generate a Playwright spec from IR and execute it with local Playwright Test."""

    working_dir = Path(cwd) if cwd is not None else Path.cwd()
    spec_path = generate_local_playwright_spec(ir_path, generated_dir=generated_dir)
    return run_local_playwright_specs(
        [spec_path],
        generated_dir=generated_dir,
        artifacts_dir=artifacts_dir,
        config_path=config_path,
        extra_args=extra_args,
        cwd=working_dir,
        env_overrides=_example_app_env(ir_path),
    )


def run_local_playwright_batch(
    ir_paths: Sequence[str | Path],
    *,
    generated_dir: str | Path = DEFAULT_GENERATED_DIR,
    artifacts_dir: str | Path = DEFAULT_ARTIFACTS_DIR,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    extra_args: Sequence[str] = (),
    cwd: str | Path | None = None,
) -> LocalPlaywrightRun:
    """Generate Playwright specs from multiple IR files and execute them in one run."""

    if not ir_paths:
        raise LocalPlaywrightRunnerError((ValidationIssue("$", "at least one IR file is required", "runner.no_specs"),))

    working_dir = Path(cwd) if cwd is not None else Path.cwd()
    spec_paths = tuple(generate_local_playwright_spec(ir_path, generated_dir=generated_dir) for ir_path in ir_paths)
    return run_local_playwright_specs(
        spec_paths,
        generated_dir=generated_dir,
        artifacts_dir=artifacts_dir,
        config_path=config_path,
        extra_args=extra_args,
        cwd=working_dir,
        env_overrides=_example_app_env(ir_paths[0]),
    )


def run_local_playwright_specs(
    spec_paths: Sequence[str | Path],
    *,
    generated_dir: str | Path = DEFAULT_GENERATED_DIR,
    artifacts_dir: str | Path = DEFAULT_ARTIFACTS_DIR,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    extra_args: Sequence[str] = (),
    cwd: str | Path | None = None,
    env_overrides: Mapping[str, str] | None = None,
) -> LocalPlaywrightRun:
    """Execute already-generated Playwright specs in one local Playwright Test run."""

    if not spec_paths:
        raise LocalPlaywrightRunnerError((ValidationIssue("$", "at least one generated spec file is required", "runner.no_specs"),))

    working_dir = Path(cwd) if cwd is not None else Path.cwd()
    generated_spec_paths = tuple(Path(path) for path in spec_paths)
    paths = _build_paths(generated_dir=generated_dir, artifacts_dir=artifacts_dir)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    spec_hashes_before = {path: _file_sha256(path) for path in generated_spec_paths}

    command = (
        "npx",
        "playwright",
        "test",
        *(str(path) for path in generated_spec_paths),
        "--config",
        str(config_path),
        *tuple(extra_args),
    )
    env = {
        "PETF_PLAYWRIGHT_TEST_DIR": str(paths.generated_dir),
        "PETF_PLAYWRIGHT_ARTIFACTS_DIR": str(paths.artifacts_dir),
        **(env_overrides or {}),
        **_node_resolution_env(working_dir),
    }

    try:
        completed = subprocess.run(
            command,
            cwd=working_dir,
            env={**_subprocess_env(), **env},
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise LocalPlaywrightRunnerError(
            (ValidationIssue("$", "npx was not found; install Node.js and npm before running Playwright", "runner.npx_missing"),)
        ) from exc

    mutated_paths = [path for path, digest in spec_hashes_before.items() if _file_sha256(path) != digest]
    if mutated_paths:
        raise LocalPlaywrightRunnerError(
            tuple(ValidationIssue(str(path), "Playwright runner modified the generated spec file", "runner.generated_spec_mutated") for path in mutated_paths)
        )

    return LocalPlaywrightRun(
        command=command,
        generated_spec_path=generated_spec_paths[0] if len(generated_spec_paths) == 1 else None,
        generated_spec_paths=generated_spec_paths,
        paths=paths,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _build_paths(*, generated_dir: str | Path, artifacts_dir: str | Path) -> LocalPlaywrightPaths:
    artifacts = Path(artifacts_dir)
    return LocalPlaywrightPaths(
        generated_dir=Path(generated_dir),
        artifacts_dir=artifacts,
        test_results_dir=artifacts / "test-results",
        html_report_dir=artifacts / "html-report",
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _subprocess_env() -> Mapping[str, str]:
    import os

    return os.environ.copy()


def _node_resolution_env(working_dir: Path) -> Mapping[str, str]:
    import os

    node_modules_dir = working_dir / "node_modules"
    entries: list[str] = []
    if node_modules_dir.exists():
        entries.append(str(node_modules_dir))
    existing_node_path = os.environ.get("NODE_PATH", "").strip()
    if existing_node_path:
        entries.append(existing_node_path)
    return {"NODE_PATH": os.pathsep.join(entries)} if entries else {}


def _example_app_env(ir_path: str | Path) -> Mapping[str, str]:
    ir = parse_ir_file(ir_path)
    base_url = ir.raw["environment"]["baseUrl"]
    parsed = urlparse(base_url)
    example = urlparse(EXAMPLE_APP_BASE_URL)

    if parsed.scheme == example.scheme and parsed.hostname == example.hostname and parsed.port == example.port:
        return {
            "PETF_PLAYWRIGHT_WEB_SERVER_COMMAND": EXAMPLE_APP_COMMAND,
            "PETF_PLAYWRIGHT_WEB_SERVER_URL": f"{base_url.rstrip('/')}/calculator",
        }

    return {}


__all__ = [
    "DEFAULT_ARTIFACTS_DIR",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_GENERATED_DIR",
    "EXAMPLE_APP_BASE_URL",
    "EXAMPLE_APP_COMMAND",
    "LocalPlaywrightPaths",
    "LocalPlaywrightRun",
    "LocalPlaywrightRunnerError",
    "generate_local_playwright_spec",
    "run_local_playwright",
    "run_local_playwright_batch",
    "run_local_playwright_specs",
]
