"""Project-level config, compilation, and local Playwright orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Sequence

import yaml

from plain_english_test_framework.compiler import CompilerError, compile_spec_file
from plain_english_test_framework.ir_validator import IrValidationError
from plain_english_test_framework.local_runner import LocalPlaywrightRun, LocalPlaywrightRunnerError, run_local_playwright
from plain_english_test_framework.playwright_generator import PlaywrightGenerationError
from plain_english_test_framework.spec_parser import SpecValidationError
from plain_english_test_framework.validation import ValidationIssue, deduplicate_issues


DEFAULT_PROJECT_CONFIG_PATH = Path("qa-framework.yaml")


class ProjectWorkflowError(Exception):
    """Raised when project-level config or orchestration cannot continue."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class ProjectEnvironmentConfig:
    """Environment selection used for project compilation."""

    name: str
    file: Path


@dataclass(frozen=True)
class ProjectOutputConfig:
    """Generated output directories for project workflows."""

    ir_dir: Path
    playwright_dir: Path
    artifacts_dir: Path


@dataclass(frozen=True)
class ProjectPlaywrightConfig:
    """Playwright runner configuration for project workflows."""

    config: Path


@dataclass(frozen=True)
class ProjectConfig:
    """Resolved project workflow configuration."""

    source_path: Path
    root_dir: Path
    specs_dir: Path
    data_dir: Path
    environment: ProjectEnvironmentConfig
    outputs: ProjectOutputConfig
    playwright: ProjectPlaywrightConfig


@dataclass(frozen=True)
class ProjectCompileItem:
    """Compilation result for one configured spec."""

    spec_path: Path
    status: str
    spec_id: str | None = None
    ir_path: Path | None = None
    case_count: int | None = None
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class ProjectCompileResult:
    """Compilation results for all configured specs."""

    config: ProjectConfig
    items: tuple[ProjectCompileItem, ...]

    @property
    def succeeded(self) -> bool:
        return all(item.status == "compiled" for item in self.items)


@dataclass(frozen=True)
class ProjectRunItem:
    """Run result for one configured spec."""

    spec_path: Path
    status: str
    spec_id: str | None = None
    ir_path: Path | None = None
    generated_spec_path: Path | None = None
    artifacts_dir: Path | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class ProjectRunResult:
    """Run results for all configured specs."""

    config: ProjectConfig
    items: tuple[ProjectRunItem, ...]

    @property
    def succeeded(self) -> bool:
        return all(item.status == "passed" for item in self.items)

    @property
    def returncode(self) -> int:
        return 0 if self.succeeded else 1


def load_project_config(path: str | Path = DEFAULT_PROJECT_CONFIG_PATH) -> ProjectConfig:
    """Load and validate the project workflow config."""

    config_path = Path(path)
    if not config_path.exists():
        raise ProjectWorkflowError((ValidationIssue("$", f"project config not found: {config_path}", "project_config.not_found"),))

    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectWorkflowError((ValidationIssue("$", f"invalid YAML: {exc}", "yaml.parse"),)) from exc

    if not isinstance(document, dict):
        raise ProjectWorkflowError((ValidationIssue("$", "project config must be a YAML mapping", "project_config.type"),))

    root_dir = config_path.resolve().parent
    issues: list[ValidationIssue] = []

    specs_dir = _required_path(document, "specsDir", root_dir, issues)
    data_dir = _required_path(document, "dataDir", root_dir, issues)
    environment = _environment_config(document.get("environment"), root_dir, issues)
    outputs = _output_config(document.get("outputs"), root_dir, issues)
    playwright = _playwright_config(document.get("playwright"), root_dir, issues)

    if specs_dir is not None and not specs_dir.is_dir():
        issues.append(ValidationIssue("$.specsDir", f"specsDir does not exist: {specs_dir}", "project_config.specs_dir_missing"))
    if data_dir is not None and not data_dir.is_dir():
        issues.append(ValidationIssue("$.dataDir", f"dataDir does not exist: {data_dir}", "project_config.data_dir_missing"))
    if environment is not None and not environment.file.is_file():
        issues.append(
            ValidationIssue("$.environment.file", f"environment file does not exist: {environment.file}", "project_config.environment_file_missing")
        )
    if playwright is not None and not playwright.config.is_file():
        issues.append(
            ValidationIssue("$.playwright.config", f"Playwright config does not exist: {playwright.config}", "project_config.playwright_config_missing")
        )

    if issues:
        raise ProjectWorkflowError(tuple(deduplicate_issues(issues)))

    assert specs_dir is not None
    assert data_dir is not None
    assert environment is not None
    assert outputs is not None
    assert playwright is not None

    return ProjectConfig(
        source_path=config_path,
        root_dir=root_dir,
        specs_dir=specs_dir,
        data_dir=data_dir,
        environment=environment,
        outputs=outputs,
        playwright=playwright,
    )


def discover_project_specs(config: ProjectConfig) -> tuple[Path, ...]:
    """Discover configured YAML specs in stable order."""

    specs = [path for pattern in ("*.yaml", "*.yml") for path in config.specs_dir.glob(pattern)]
    return tuple(sorted((path for path in specs if path.is_file()), key=lambda path: path.name))


def compile_project(config: ProjectConfig) -> ProjectCompileResult:
    """Compile every configured spec into the configured IR output directory."""

    config.outputs.ir_dir.mkdir(parents=True, exist_ok=True)
    items = tuple(_compile_project_spec(config, spec_path) for spec_path in discover_project_specs(config))
    return ProjectCompileResult(config=config, items=items)


def run_project(config: ProjectConfig, *, extra_args: Sequence[str] = ()) -> ProjectRunResult:
    """Compile and run every configured spec with local Playwright."""

    config.outputs.ir_dir.mkdir(parents=True, exist_ok=True)
    items: list[ProjectRunItem] = []
    for spec_path in discover_project_specs(config):
        compiled = _compile_project_spec(config, spec_path)
        if compiled.status != "compiled" or compiled.ir_path is None:
            items.append(
                ProjectRunItem(
                    spec_path=compiled.spec_path,
                    status="invalid",
                    spec_id=compiled.spec_id,
                    ir_path=compiled.ir_path,
                    issues=compiled.issues,
                )
            )
            continue

        items.append(_run_project_ir(config, compiled, extra_args=extra_args))

    return ProjectRunResult(config=config, items=tuple(items))


def _compile_project_spec(config: ProjectConfig, spec_path: Path) -> ProjectCompileItem:
    try:
        compiled = compile_spec_file(
            spec_path,
            environment_path=config.environment.file,
            environment_name=config.environment.name,
            data_dir=config.data_dir,
        )
    except (CompilerError, IrValidationError, SpecValidationError) as exc:
        return ProjectCompileItem(spec_path=spec_path, status="invalid", issues=tuple(exc.issues))

    ir_path = config.outputs.ir_dir / f"{compiled.spec_id}.ir.json"
    ir_path.write_text(json.dumps(compiled.raw, indent=2) + "\n", encoding="utf-8")
    return ProjectCompileItem(
        spec_path=spec_path,
        status="compiled",
        spec_id=compiled.spec_id,
        ir_path=ir_path,
        case_count=compiled.case_count,
    )


def _run_project_ir(config: ProjectConfig, compiled: ProjectCompileItem, *, extra_args: Sequence[str]) -> ProjectRunItem:
    assert compiled.ir_path is not None
    try:
        run = run_local_playwright(
            compiled.ir_path,
            generated_dir=config.outputs.playwright_dir,
            artifacts_dir=config.outputs.artifacts_dir,
            config_path=config.playwright.config,
            extra_args=extra_args,
            cwd=config.root_dir,
        )
    except (IrValidationError, PlaywrightGenerationError, LocalPlaywrightRunnerError) as exc:
        return ProjectRunItem(
            spec_path=compiled.spec_path,
            status="invalid",
            spec_id=compiled.spec_id,
            ir_path=compiled.ir_path,
            issues=tuple(exc.issues),
        )

    return _run_item_from_local_run(compiled, run)


def _run_item_from_local_run(compiled: ProjectCompileItem, run: LocalPlaywrightRun) -> ProjectRunItem:
    return ProjectRunItem(
        spec_path=compiled.spec_path,
        status="passed" if run.returncode == 0 else "failed",
        spec_id=compiled.spec_id,
        ir_path=compiled.ir_path,
        generated_spec_path=run.generated_spec_path,
        artifacts_dir=run.paths.artifacts_dir,
        returncode=run.returncode,
        stdout=run.stdout,
        stderr=run.stderr,
    )


def _required_path(document: dict[str, object], key: str, root_dir: Path, issues: list[ValidationIssue]) -> Path | None:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        issues.append(ValidationIssue(f"$.{key}", f"{key} is required", f"project_config.{_snake(key)}_required"))
        return None
    return _resolve_path(root_dir, value)


def _environment_config(value: object, root_dir: Path, issues: list[ValidationIssue]) -> ProjectEnvironmentConfig | None:
    if not isinstance(value, dict):
        issues.append(ValidationIssue("$.environment", "environment config is required", "project_config.environment_required"))
        return None

    name = value.get("name")
    file_value = value.get("file")
    if not isinstance(name, str) or not name:
        issues.append(ValidationIssue("$.environment.name", "environment name is required", "project_config.environment_name_required"))
    if not isinstance(file_value, str) or not file_value:
        issues.append(ValidationIssue("$.environment.file", "environment file is required", "project_config.environment_file_required"))

    if not isinstance(name, str) or not name or not isinstance(file_value, str) or not file_value:
        return None
    return ProjectEnvironmentConfig(name=name, file=_resolve_path(root_dir, file_value))


def _output_config(value: object, root_dir: Path, issues: list[ValidationIssue]) -> ProjectOutputConfig | None:
    if not isinstance(value, dict):
        issues.append(ValidationIssue("$.outputs", "outputs config is required", "project_config.outputs_required"))
        return None

    ir_dir = _nested_path(value, "irDir", "$.outputs.irDir", root_dir, issues)
    playwright_dir = _nested_path(value, "playwrightDir", "$.outputs.playwrightDir", root_dir, issues)
    artifacts_dir = _nested_path(value, "artifactsDir", "$.outputs.artifactsDir", root_dir, issues)
    if ir_dir is None or playwright_dir is None or artifacts_dir is None:
        return None
    return ProjectOutputConfig(ir_dir=ir_dir, playwright_dir=playwright_dir, artifacts_dir=artifacts_dir)


def _playwright_config(value: object, root_dir: Path, issues: list[ValidationIssue]) -> ProjectPlaywrightConfig | None:
    if not isinstance(value, dict):
        issues.append(ValidationIssue("$.playwright", "playwright config is required", "project_config.playwright_required"))
        return None

    config = _nested_path(value, "config", "$.playwright.config", root_dir, issues)
    if config is None:
        return None
    return ProjectPlaywrightConfig(config=config)


def _nested_path(document: dict[object, object], key: str, path: str, root_dir: Path, issues: list[ValidationIssue]) -> Path | None:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        issues.append(ValidationIssue(path, f"{key} is required", f"project_config.{_snake(key)}_required"))
        return None
    return _resolve_path(root_dir, value)


def _resolve_path(root_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root_dir / path


def _snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.extend(("_", char.lower()))
        else:
            chars.append(char)
    return "".join(chars).strip("_")


__all__ = [
    "DEFAULT_PROJECT_CONFIG_PATH",
    "ProjectCompileItem",
    "ProjectCompileResult",
    "ProjectConfig",
    "ProjectEnvironmentConfig",
    "ProjectOutputConfig",
    "ProjectPlaywrightConfig",
    "ProjectRunItem",
    "ProjectRunResult",
    "ProjectWorkflowError",
    "compile_project",
    "discover_project_specs",
    "load_project_config",
    "run_project",
]
