#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_DECLARATION_OUTPUT = REPO_ROOT / "frontend" / "src" / "api" / "generated" / "api-contracts.d.ts"
DEFAULT_RUNTIME_OUTPUT = REPO_ROOT / "frontend" / "src" / "api" / "generated" / "api-contracts.js"


class SelectedOperation(NamedTuple):
    key: str
    method: str
    path: str
    request_alias: str
    response_alias: str


SELECTED_OPERATIONS: tuple[SelectedOperation, ...] = (
    SelectedOperation("requirementsParse", "post", "/requirements/parse", "RequirementsParseRequest", "RequirementsParseResponse"),
    SelectedOperation("requirementsEnrich", "post", "/requirements/enrich", "RequirementsEnrichRequest", "RequirementsEnrichResponse"),
    SelectedOperation("testCasesGenerate", "post", "/testcases/generate", "TestCasesGenerateRequest", "TestCasesGenerateResponse"),
    SelectedOperation("testCasesRefine", "post", "/testcases/refine", "TestCasesRefineRequest", "TestCasesRefineResponse"),
    SelectedOperation("exportCsv", "post", "/export/csv", "ExportCsvRequest", "ExportCsvResponse"),
    SelectedOperation("exportExcel", "post", "/export/excel", "ExportExcelRequest", "ExportExcelResponse"),
    SelectedOperation("exportJson", "post", "/export/json", "ExportJsonRequest", "ExportJsonResponse"),
    SelectedOperation("exportJira", "post", "/export/jira", "ExportJiraRequest", "ExportJiraResponse"),
    SelectedOperation(
        "automationExecutionPreview", "post", "/automation/execution/preview", "AutomationExecutionPreviewRequest", "AutomationExecutionPreviewResponse"
    ),
    SelectedOperation("automationExecutionRun", "post", "/automation/execution/run", "AutomationExecutionRunRequest", "AutomationExecutionRunResponse"),
    SelectedOperation("billingEntitlementsMe", "get", "/entitlements/me", "BillingEntitlementsMeRequest", "BillingEntitlementsMeResponse"),
)

STREAMING_RESPONSE_TYPES = {
    ("post", "/export/csv"): "Blob",
    ("post", "/export/excel"): "Blob",
    ("post", "/export/json"): "Blob",
}

IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def load_openapi(openapi_path: Path | None) -> dict[str, Any]:
    if openapi_path:
        return json.loads(openapi_path.read_text(encoding="utf-8"))

    from app.main import app

    return app.openapi()


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def operation_for(openapi: dict[str, Any], selected: SelectedOperation) -> dict[str, Any]:
    try:
        return openapi["paths"][selected.path][selected.method]
    except KeyError as exc:
        raise ValueError(f"Missing OpenAPI operation {selected.method.upper()} {selected.path}") from exc


def schema_from_content(content: dict[str, Any] | None, preferred_media_types: Iterable[str]) -> dict[str, Any] | None:
    if not content:
        return None
    for media_type in preferred_media_types:
        schema = content.get(media_type, {}).get("schema")
        if schema is not None:
            return schema
    for media in content.values():
        schema = media.get("schema") if isinstance(media, dict) else None
        if schema is not None:
            return schema
    return None


def request_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    return schema_from_content(
        operation.get("requestBody", {}).get("content"),
        ("application/json", "multipart/form-data"),
    )


def response_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    response = operation.get("responses", {}).get("200") or {}
    return schema_from_content(response.get("content"), ("application/json",))


def collect_refs(schema: Any, refs: set[str]) -> None:
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            refs.add(ref_name(ref))
        for value in schema.values():
            collect_refs(value, refs)
    elif isinstance(schema, list):
        for item in schema:
            collect_refs(item, refs)


def collect_component_names(openapi: dict[str, Any], roots: Iterable[str]) -> list[str]:
    schemas = openapi.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    pending = list(roots)

    while pending:
        name = pending.pop(0)
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        refs: set[str] = set()
        collect_refs(schemas[name], refs)
        pending.extend(sorted(refs - seen))

    return sorted(seen)


def literal_type(value: Any) -> str:
    return json.dumps(value)


def quote_property(name: str) -> str:
    return name if IDENTIFIER_RE.match(name) else json.dumps(name)


def schema_to_ts(schema: dict[str, Any] | None, refs: set[str]) -> str:
    if not schema:
        return "unknown"

    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref_name(ref)
        refs.add(name)
        return name

    if "const" in schema:
        return literal_type(schema["const"])

    enum_values = schema.get("enum")
    if isinstance(enum_values, list):
        return " | ".join(literal_type(value) for value in enum_values) or "unknown"

    for union_key in ("anyOf", "oneOf"):
        options = schema.get(union_key)
        if isinstance(options, list):
            option_types = [schema_to_ts(option, refs) for option in options]
            unique_types = list(dict.fromkeys(option_types))
            return " | ".join(unique_types) or "unknown"

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        option_types = [schema_to_ts(option, refs) for option in all_of]
        unique_types = list(dict.fromkeys(option_types))
        return " & ".join(unique_types) or "unknown"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(schema_to_ts({**schema, "type": item}, refs) for item in schema_type)

    if schema_type == "null":
        return "null"
    if schema_type == "boolean":
        return "boolean"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "string":
        if schema.get("format") == "binary" or schema.get("contentMediaType") == "application/octet-stream":
            return "Blob | File | string"
        return "string"
    if schema_type == "array":
        return f"Array<{schema_to_ts(schema.get('items') or {}, refs)}>"
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        if properties:
            required = set(schema.get("required") or [])
            lines = ["{"]
            for prop_name in sorted(properties):
                prop_schema = properties[prop_name]
                marker = "" if prop_name in required else "?"
                lines.append(f"\t{quote_property(prop_name)}{marker}: {schema_to_ts(prop_schema, refs)};")
            lines.append("}")
            return "\n".join(lines)

        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {schema_to_ts(additional, refs)}>"
        return "Record<string, unknown>"

    return "unknown"


def render_component(name: str, schema: dict[str, Any], refs: set[str]) -> str:
    schema_type = schema.get("type")
    properties = schema.get("properties") or {}
    if schema_type == "object" and properties:
        required = set(schema.get("required") or [])
        lines = [f"export interface {name} {{"]
        for prop_name in sorted(properties):
            prop_schema = properties[prop_name]
            marker = "" if prop_name in required else "?"
            lines.append(f"\t{quote_property(prop_name)}{marker}: {schema_to_ts(prop_schema, refs)};")
        lines.append("}")
        return "\n".join(lines)
    return f"export type {name} = {schema_to_ts(schema, refs)};"


def operation_type(schema: dict[str, Any] | None, refs: set[str], fallback: str = "undefined") -> str:
    if schema is None:
        return fallback
    if schema == {}:
        return "unknown"
    return schema_to_ts(schema, refs)


def build_operation_types(openapi: dict[str, Any]) -> tuple[list[str], list[str], set[str]]:
    refs: set[str] = set()
    operation_lines: list[str] = []
    alias_lines: list[str] = []

    for selected in SELECTED_OPERATIONS:
        operation = operation_for(openapi, selected)
        request_type = operation_type(request_schema(operation), refs)
        response_type = STREAMING_RESPONSE_TYPES.get((selected.method, selected.path)) or operation_type(response_schema(operation), refs)
        method = selected.method.upper()

        operation_lines.append(f'\t{selected.key}: ApiOperation<{selected.request_alias}, {selected.response_alias}, "{method}", "{selected.path}">;')
        alias_lines.append(f"export type {selected.request_alias} = {request_type};")
        alias_lines.append(f"export type {selected.response_alias} = {response_type};")

    return operation_lines, alias_lines, refs


def render_declarations(openapi: dict[str, Any]) -> str:
    operation_lines, alias_lines, root_refs = build_operation_types(openapi)
    component_names = collect_component_names(openapi, root_refs)
    schemas = openapi.get("components", {}).get("schemas", {})
    refs: set[str] = set()

    lines = [
        "// Generated by scripts/generate_frontend_api_types.py.",
        "// Source: FastAPI OpenAPI schema. Do not edit by hand.",
        "",
        "export interface ApiOperation<Request, Response, Method extends string, Path extends string> {",
        "\tmethod: Method;",
        "\tpath: Path;",
        "\trequest: Request;",
        "\tresponse: Response;",
        "}",
        "",
        "export interface ApiContractOperations {",
        *operation_lines,
        "}",
        "",
        *alias_lines,
        "",
    ]

    for name in component_names:
        lines.append(render_component(name, schemas[name], refs))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_runtime() -> str:
    lines = [
        "// Generated by scripts/generate_frontend_api_types.py.",
        "// Source: FastAPI OpenAPI schema. Do not edit by hand.",
        "",
        "export const API_CONTRACT_ENDPOINTS = Object.freeze({",
    ]
    for selected in SELECTED_OPERATIONS:
        lines.append(f'\t{selected.key}: Object.freeze({{ method: "{selected.method.upper()}", path: "{selected.path}" }}),')
    lines.extend(
        [
            "});",
            "",
        ]
    )
    return "\n".join(lines)


def generate_outputs(openapi: dict[str, Any]) -> tuple[str, str]:
    return render_declarations(openapi), render_runtime()


def write_if_changed(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def check_file(path: Path, expected: str) -> bool:
    actual = path.read_text(encoding="utf-8") if path.exists() else ""
    if actual == expected:
        return True
    diff = difflib.unified_diff(
        actual.splitlines(),
        expected.splitlines(),
        fromfile=str(path),
        tofile=f"{path} (generated)",
        lineterm="",
    )
    print("\n".join(diff), file=sys.stderr)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate frontend API contract types from FastAPI OpenAPI.")
    parser.add_argument("--openapi", type=Path, help="Read OpenAPI JSON from this path instead of importing the FastAPI app.")
    parser.add_argument("--declarations-output", type=Path, default=DEFAULT_DECLARATION_OUTPUT)
    parser.add_argument("--runtime-output", type=Path, default=DEFAULT_RUNTIME_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Fail if committed generated files are stale.")
    args = parser.parse_args()

    declarations, runtime = generate_outputs(load_openapi(args.openapi))
    if args.check:
        declarations_ok = check_file(args.declarations_output, declarations)
        runtime_ok = check_file(args.runtime_output, runtime)
        if not declarations_ok or not runtime_ok:
            raise SystemExit(1)
        print("Frontend API contract types are up to date.")
        return

    write_if_changed(args.declarations_output, declarations)
    write_if_changed(args.runtime_output, runtime)
    print(f"Wrote {args.declarations_output}")
    print(f"Wrote {args.runtime_output}")


if __name__ == "__main__":
    main()
