"""Internal plain-English test execution framework.

This package vendors the deterministic MVP runner pieces used by the webapp:
structured spec parsing, JSON IR compilation, TypeScript Playwright generation,
and local Playwright execution.
"""

from plain_english_test_framework.compiler import (
    CompilerError,
    DataRow,
    ResolvedEnvironment,
    compile_parsed_spec,
    compile_spec_file,
    resolve_data_rows,
    resolve_environment_file,
)
from plain_english_test_framework.ir_validator import (
    IrValidationError,
    ValidatedIr,
    parse_ir_document,
    parse_ir_file,
    validate_ir_document,
)
from plain_english_test_framework.local_runner import (
    LocalPlaywrightPaths,
    LocalPlaywrightRun,
    LocalPlaywrightRunnerError,
    generate_local_playwright_spec,
    run_local_playwright,
)
from plain_english_test_framework.playwright_generator import (
    GeneratedPlaywrightSpec,
    PlaywrightGenerationError,
    generate_playwright_spec,
    generate_playwright_spec_file,
)
from plain_english_test_framework.spec_parser import (
    ParsedSpec,
    SpecValidationError,
    parse_spec_file,
    parse_spec_text,
)
from plain_english_test_framework.validation import ValidationIssue

__all__ = [
    "CompilerError",
    "DataRow",
    "GeneratedPlaywrightSpec",
    "IrValidationError",
    "LocalPlaywrightPaths",
    "LocalPlaywrightRun",
    "LocalPlaywrightRunnerError",
    "ParsedSpec",
    "PlaywrightGenerationError",
    "ResolvedEnvironment",
    "SpecValidationError",
    "ValidatedIr",
    "ValidationIssue",
    "compile_parsed_spec",
    "compile_spec_file",
    "generate_local_playwright_spec",
    "generate_playwright_spec",
    "generate_playwright_spec_file",
    "parse_ir_document",
    "parse_ir_file",
    "parse_spec_file",
    "parse_spec_text",
    "resolve_data_rows",
    "resolve_environment_file",
    "run_local_playwright",
    "validate_ir_document",
]
