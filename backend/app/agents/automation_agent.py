import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google import genai

from ..config import GenerationSettings, get_generation_settings, get_settings
from ..models import AutomationCaseDiagnostic, AutomationInput, AutomationResponse, TestCase
from ..utils.genai_response import extract_response_text


@dataclass(frozen=True)
class _AutomationShard:
    index: int
    group_name: str
    test_cases: List[TestCase]

    @property
    def shard_id(self) -> str:
        return f"automation-shard-{self.index:02d}"


@dataclass(frozen=True)
class _AutomationFragmentResult:
    shard: _AutomationShard
    files: Dict[str, str]
    case_diagnostics: List[AutomationCaseDiagnostic]
    represented_case_ids: set[str]
    merge_warnings: List[str]
    used_fallback: bool = False
    failed: bool = False


def _get_model_settings_or_none() -> Any | None:
    try:
        return get_settings()
    except RuntimeError as exc:
        if "GEMINI_API_KEY" not in str(exc):
            raise
        return None


def _identifier(value: str, *, default: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not normalized:
        normalized = default
    if normalized[0].isdigit():
        normalized = f"{default}_{normalized}"
    return normalized


def _unique_identifier(value: str, seen: set[str], *, default: str) -> str:
    base = _identifier(value, default=default)
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _class_name(value: str, *, default: str) -> str:
    identifier = _identifier(value, default=default)
    parts = [part for part in identifier.split("_") if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or default
    if name[0].isdigit():
        name = f"{default}{name}"
    return name


def _case_group_name(test_case: TestCase) -> str:
    component = str(test_case.component or "").strip()
    if component:
        return component

    for step in test_case.steps or []:
        action = str(step.action or "")
        url_match = re.search(r"https?://[^/\s]+/([A-Za-z0-9_/-]+)", action)
        if url_match:
            first_path_part = url_match.group(1).strip("/").split("/")[0]
            if first_path_part:
                return first_path_part.replace("-", " ").replace("_", " ").title()

        label_match = re.search(
            r"\b(?:open|visit|navigate to|go to)\s+(?:the\s+)?([A-Za-z][A-Za-z0-9 _/-]{2,60}?)(?:\s+(?:page|screen|workflow))?\b",
            action,
            flags=re.IGNORECASE,
        )
        if label_match:
            return label_match.group(1).strip().title()

    for tag in test_case.tags or []:
        normalized = str(tag or "").strip()
        if normalized and not normalized.upper().startswith("REQ-") and not normalized.startswith("scenario:"):
            return normalized

    return "General"


def _plan_automation_shards(test_cases: List[TestCase]) -> List[_AutomationShard]:
    groups: list[tuple[str, list[TestCase]]] = []
    index_by_group: dict[str, int] = {}
    for test_case in test_cases:
        group_name = _case_group_name(test_case)
        group_key = _identifier(group_name, default="general")
        if group_key not in index_by_group:
            index_by_group[group_key] = len(groups)
            groups.append((group_name, []))
        groups[index_by_group[group_key]][1].append(test_case)

    return [
        _AutomationShard(
            index=index,
            group_name=group_name,
            test_cases=list(group_cases),
        )
        for index, (group_name, group_cases) in enumerate(groups, start=1)
    ]


def _should_use_parallel_automation_generation(payload: AutomationInput, generation_settings: GenerationSettings) -> bool:
    if not generation_settings.parallel_automation_generation_enabled:
        return False
    return len(payload.test_cases) >= generation_settings.parallel_automation_min_cases


def _build_pom_prompt(payload: AutomationInput) -> str:
    base_url = str(payload.target_base_url) if payload.target_base_url else "https://example.com"

    tcs_summary = []
    for tc in payload.test_cases:
        steps_text = "; ".join(f"Step {s.step}: {s.action}" for s in (tc.steps or [])[:5])
        tcs_summary.append(f"  - [{tc.id}] {tc.title} | Component: {tc.component or 'General'} | Steps: {steps_text}")

    tc_block = "\n".join(tcs_summary) if tcs_summary else "  (no test cases provided)"

    return f"""You are a Playwright Python test automation engineer.

Generate a complete Playwright pytest Page Object Model (POM) for the following small test suite.

Base URL: {base_url}
Test Cases:
{tc_block}

Requirements:
1. Create a `BasePage` class with `__init__(self, page: Page)` and common helpers (navigate, wait_for_url, etc.)
2. Create one or more page classes (e.g. `DocsPage`, `NavigationPage`) that extend `BasePage` and encapsulate relevant locators and actions.
3. Create a `conftest.py` with a `browser_context_args` and a `page` fixture.
4. Create a test file that uses the page classes to implement at least one test per test case above.
5. All code must use `from playwright.sync_api import Page, expect` (sync API).
6. Use `page.get_by_role`, `page.get_by_text`, `page.get_by_label`, and `page.locator` for all selectors - no raw CSS or XPath unless unavoidable.
7. Include `# type: ignore` only where strictly needed. Add triple-quoted docstrings for every class and test function.
8. Output ONLY valid Python source code - no markdown fences, no explanations outside comments.
9. Separate files with a comment header like: `# === FILE: tests/pages/base_page.py ===`

Begin generating the code now.
"""


def _build_fragment_prompt(shard: _AutomationShard, *, base_url: str) -> str:
    cases = []
    for tc in shard.test_cases:
        steps_text = "; ".join(f"Step {step.step}: {step.action} -> {step.expected}" for step in (tc.steps or [])[:8])
        cases.append(
            {
                "id": tc.id,
                "title": tc.title,
                "component": tc.component or shard.group_name,
                "automation_status": tc.automation_status,
                "steps": steps_text,
            }
        )

    return f"""You are generating one shard of a larger Playwright pytest project.

Base URL: {base_url}
Shard ID: {shard.shard_id}
Shard group: {shard.group_name}
Test cases JSON:
{json.dumps(cases, indent=2)}

Return ONLY a JSON object with this shape:
{{
  "files": [
    {{
      "path": "tests/generated/test_{_identifier(shard.group_name, default="generated")}.py",
      "content": "from playwright.sync_api import Page\\n..."
    }}
  ],
  "case_diagnostics": [
    {{
      "test_case_id": "TC-001",
      "title": "Case title",
      "status": "generated",
      "reason": "Generated a pytest function for this case."
    }}
  ]
}}

Rules:
- Generate fragments only. Do not output base_page.py, conftest.py, package files, billing calls, persistence calls, or reports.
- Use imports from tests.pages.generated_page only.
- Represent each non-manual test case with either a pytest function or a diagnostic with status "manual" or "unsupported".
- Keep function names unique within this shard.
- Do not include markdown fences or prose outside the JSON object.
"""


def _strip_markdown_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))


def _extract_file_headers(pom_code: str) -> List[str]:
    files: List[str] = []
    for line in pom_code.splitlines():
        if line.strip().startswith("# === FILE:") and "===" in line:
            fname = line.split("# === FILE:", 1)[1].replace("===", "").strip()
            if fname:
                files.append(fname)
    return files


def _shared_project_files(base_url: str) -> Dict[str, str]:
    return {
        "tests/pages/base_page.py": f'''from playwright.sync_api import Page


class BasePage:
    """Common navigation helpers for generated Playwright tests."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.base_url = {json.dumps(base_url)}

    def navigate(self, path: str = "") -> None:
        self.page.goto(f"{{self.base_url.rstrip('/')}}/{{path.lstrip('/')}}")
''',
        "tests/pages/generated_page.py": '''from playwright.sync_api import Page, expect
from .base_page import BasePage


class GeneratedPage(BasePage):
    """Generic page object used by assembled automation fragments."""

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self.body = page.locator("body")

    def assert_loaded(self) -> None:
        expect(self.body).to_be_visible()
''',
        "tests/conftest.py": """import pytest


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args}
""",
    }


def _is_manual_case(test_case: TestCase) -> bool:
    return str(test_case.automation_status or "").strip().lower() == "manual"


def _is_unsupported_case(test_case: TestCase) -> bool:
    return not bool(test_case.steps)


def _diagnostic(
    test_case: TestCase,
    *,
    status: str,
    reason: str,
    shard_id: Optional[str] = None,
) -> AutomationCaseDiagnostic:
    return AutomationCaseDiagnostic(
        test_case_id=test_case.id,
        title=test_case.title,
        status=status,
        reason=reason,
        shard_id=shard_id,
    )


def _build_deterministic_fragment(shard: _AutomationShard, *, used_fallback: bool, failed: bool, warning: Optional[str] = None) -> _AutomationFragmentResult:
    module_name = _identifier(shard.group_name, default=f"generated_{shard.index}")
    path = f"tests/generated/test_{module_name}.py"
    function_names: set[str] = set()
    test_functions: List[str] = []
    case_diagnostics: List[AutomationCaseDiagnostic] = []
    represented_case_ids: set[str] = set()

    for index, test_case in enumerate(shard.test_cases, start=1):
        manual_case = _is_manual_case(test_case)
        if manual_case:
            represented_case_ids.add(test_case.id)
            case_diagnostics.append(
                _diagnostic(
                    test_case,
                    status="manual",
                    reason="Test case is marked Manual and was represented as a manual automation diagnostic.",
                    shard_id=shard.shard_id,
                )
            )
        if _is_unsupported_case(test_case):
            if manual_case:
                continue
            represented_case_ids.add(test_case.id)
            case_diagnostics.append(
                _diagnostic(
                    test_case,
                    status="unsupported",
                    reason="Test case has no executable steps to convert into an automation fragment.",
                    shard_id=shard.shard_id,
                )
            )
            continue

        function_name = _unique_identifier(f"{test_case.id}_{test_case.title}", function_names, default=f"generated_case_{index}")
        reason = "Generated deterministic fallback fragment for this case." if used_fallback else "Generated automation fragment for this case."
        status = "fallback" if used_fallback else "generated"
        represented_case_ids.add(test_case.id)
        if not manual_case:
            case_diagnostics.append(_diagnostic(test_case, status=status, reason=reason, shard_id=shard.shard_id))
        test_functions.append(
            f'''
def test_{function_name}(page: Page) -> None:
    """Generated smoke coverage for {test_case.id}: {test_case.title}."""
    app = GeneratedPage(page)
    app.navigate()
    app.assert_loaded()
    # Source test case: {test_case.id}
'''
        )

    files: Dict[str, str] = {}
    if test_functions:
        files[path] = f"""from playwright.sync_api import Page
from tests.pages.generated_page import GeneratedPage

{"".join(test_functions)}
"""

    merge_warnings = [warning] if warning else []
    return _AutomationFragmentResult(
        shard=shard,
        files=files,
        case_diagnostics=case_diagnostics,
        represented_case_ids=represented_case_ids,
        merge_warnings=merge_warnings,
        used_fallback=used_fallback,
        failed=failed,
    )


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    text = _strip_markdown_fences(str(raw_text or "").strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Automation fragment worker returned non-object JSON.")
    return parsed


def _sanitize_fragment_path(path: str, *, shard: _AutomationShard) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    if not filename.endswith(".py"):
        filename = f"test_{_identifier(shard.group_name, default=f'generated_{shard.index}')}.py"
    if not filename.startswith("test_"):
        filename = f"test_{filename}"
    return f"tests/generated/{filename}"


def _parse_worker_fragment(raw_text: str, shard: _AutomationShard) -> _AutomationFragmentResult:
    parsed = _extract_json_object(raw_text)
    files: Dict[str, str] = {}
    merge_warnings: List[str] = []

    for item in parsed.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = _sanitize_fragment_path(str(item.get("path") or ""), shard=shard)
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if path in files:
            merge_warnings.append(f"Worker returned duplicate fragment path {path}; later content was ignored.")
            continue
        files[path] = content

    case_diagnostics: List[AutomationCaseDiagnostic] = []
    represented_case_ids: set[str] = set()
    for item in parsed.get("case_diagnostics") or []:
        if not isinstance(item, dict):
            continue
        test_case_id = str(item.get("test_case_id") or "").strip()
        if not test_case_id:
            continue
        diagnostic = AutomationCaseDiagnostic(
            test_case_id=test_case_id,
            title=item.get("title"),
            status=item.get("status") if item.get("status") in {"generated", "fallback", "manual", "unsupported"} else "generated",
            reason=str(item.get("reason") or "Worker returned a fragment diagnostic."),
            shard_id=shard.shard_id,
        )
        case_diagnostics.append(diagnostic)
        represented_case_ids.add(test_case_id)

    for test_case in shard.test_cases:
        if test_case.id in represented_case_ids:
            continue
        status = "manual" if _is_manual_case(test_case) else "unsupported"
        reason = "Worker did not return a fragment or diagnostic for this test case."
        case_diagnostics.append(_diagnostic(test_case, status=status, reason=reason, shard_id=shard.shard_id))
        represented_case_ids.add(test_case.id)

    if not files:
        raise ValueError("Automation fragment worker returned no files.")

    return _AutomationFragmentResult(
        shard=shard,
        files=files,
        case_diagnostics=case_diagnostics,
        represented_case_ids=represented_case_ids,
        merge_warnings=merge_warnings,
    )


def _run_model_automation_fragment_worker(
    *,
    shard: _AutomationShard,
    base_url: str,
    model_name: str,
    api_key: str,
) -> _AutomationFragmentResult:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=_build_fragment_prompt(shard, base_url=base_url),
        config=genai.types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=12000,
        ),
    )
    return _parse_worker_fragment(extract_response_text(response), shard)


def _run_automation_shard_with_fallback(
    *,
    shard: _AutomationShard,
    base_url: str,
    model_settings: Any | None,
) -> _AutomationFragmentResult:
    if model_settings is None:
        return _build_deterministic_fragment(
            shard,
            used_fallback=True,
            failed=False,
            warning=f"Automation shard {shard.shard_id} used deterministic fallback because model credentials are unavailable.",
        )

    try:
        return _run_model_automation_fragment_worker(
            shard=shard,
            base_url=base_url,
            model_name=model_settings.model_name,
            api_key=model_settings.gemini_api_key,
        )
    except Exception as exc:
        logging.warning("[AutomationAgent] shard %s fell back to deterministic fragments: %s", shard.shard_id, exc)
        return _build_deterministic_fragment(
            shard,
            used_fallback=True,
            failed=True,
            warning=f"Automation shard {shard.shard_id} failed and used deterministic fallback fragments: {exc}",
        )


def _dedupe_path(path: str, seen_paths: set[str]) -> str:
    if path not in seen_paths:
        seen_paths.add(path)
        return path
    stem, dot, suffix = path.rpartition(".")
    base = stem if dot else path
    extension = f".{suffix}" if dot else ""
    counter = 2
    while True:
        candidate = f"{base}_{counter}{extension}"
        if candidate not in seen_paths:
            seen_paths.add(candidate)
            return candidate
        counter += 1


def _rename_duplicate_symbols(source: str, *, seen_classes: set[str], seen_functions: set[str]) -> tuple[str, List[str]]:
    warnings: List[str] = []
    lines: List[str] = []
    for line in source.splitlines():
        class_match = re.match(r"(\s*)class\s+([A-Za-z_][A-Za-z0-9_]*)\b", line)
        if class_match:
            original = class_match.group(2)
            if original in seen_classes:
                replacement = _class_name(_unique_identifier(original, seen_classes, default="GeneratedClass"), default="GeneratedClass")
                line = line.replace(f"class {original}", f"class {replacement}", 1)
                warnings.append(f"Renamed duplicate class {original} to {replacement}.")
            else:
                seen_classes.add(original)

        def_match = re.match(r"(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\b", line)
        if def_match:
            original = def_match.group(2)
            if not (original.startswith("__") and original.endswith("__")):
                if original in seen_functions:
                    replacement = _unique_identifier(original, seen_functions, default="generated_test")
                    line = line.replace(f"def {original}", f"def {replacement}", 1)
                    warnings.append(f"Renamed duplicate function or method {original} to {replacement}.")
                else:
                    seen_functions.add(original)
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n", warnings


def _assemble_response(
    payload: AutomationInput,
    shard_results: List[_AutomationFragmentResult],
    *,
    base_url: str,
    parallel_enabled: bool,
    worker_count: int,
) -> AutomationResponse:
    files_by_path = _shared_project_files(base_url)
    seen_paths = set(files_by_path)
    seen_classes = {"BasePage", "GeneratedPage"}
    seen_functions: set[str] = set()
    merge_warnings: List[str] = []
    case_diagnostics: List[AutomationCaseDiagnostic] = []
    represented_case_ids: set[str] = set()

    for result in sorted(shard_results, key=lambda item: item.shard.index):
        merge_warnings.extend(result.merge_warnings)
        for diagnostic in result.case_diagnostics:
            if isinstance(diagnostic, AutomationCaseDiagnostic):
                case_diagnostics.append(diagnostic)
            else:
                case_diagnostics.append(AutomationCaseDiagnostic.model_validate(diagnostic))
        represented_case_ids.update(result.represented_case_ids)
        for path, source in result.files.items():
            merged_path = _dedupe_path(_sanitize_fragment_path(path, shard=result.shard), seen_paths)
            if merged_path != path:
                merge_warnings.append(f"Renamed duplicate fragment file {path} to {merged_path}.")
            normalized_source, symbol_warnings = _rename_duplicate_symbols(
                source,
                seen_classes=seen_classes,
                seen_functions=seen_functions,
            )
            merge_warnings.extend(symbol_warnings)
            files_by_path[merged_path] = normalized_source

    for test_case in payload.test_cases:
        if test_case.id in represented_case_ids:
            continue
        case_diagnostics.append(
            _diagnostic(
                test_case,
                status="unsupported",
                reason="No automation worker represented this test case.",
            )
        )
        represented_case_ids.add(test_case.id)

    files = list(files_by_path)
    notes = "\n\n".join(f"# === FILE: {path} ===\n{content.rstrip()}" for path, content in files_by_path.items())
    generated_count = sum(1 for diagnostic in case_diagnostics if diagnostic.status in {"generated", "fallback"})
    manual_count = sum(1 for diagnostic in case_diagnostics if diagnostic.status == "manual")
    unsupported_count = sum(1 for diagnostic in case_diagnostics if diagnostic.status == "unsupported")
    failed_shards = sum(1 for result in shard_results if result.failed)
    fallback_shards = sum(1 for result in shard_results if result.used_fallback)
    has_test_fragments = any(path.startswith("tests/generated/") for path in files_by_path)

    return AutomationResponse(
        status="generated" if has_test_fragments else "skipped",
        files=files,
        notes=notes,
        diagnostics={
            "shard_count": len(shard_results),
            "worker_count": worker_count,
            "parallel_enabled": parallel_enabled,
            "failed_shard_count": failed_shards,
            "fallback_shard_count": fallback_shards,
            "represented_test_case_count": len(represented_case_ids),
            "generated_case_count": generated_count,
            "manual_case_count": manual_count,
            "unsupported_case_count": unsupported_count,
            "merge_warnings": merge_warnings,
        },
        case_diagnostics=case_diagnostics,
    )


def _run_automation_coordinator(
    payload: AutomationInput,
    *,
    generation_settings: GenerationSettings,
    model_settings: Any | None,
) -> AutomationResponse:
    base_url = str(payload.target_base_url) if payload.target_base_url else "https://example.com"
    shards = _plan_automation_shards(payload.test_cases)
    parallel_enabled = _should_use_parallel_automation_generation(payload, generation_settings)
    worker_count = min(generation_settings.parallel_automation_max_workers, len(shards)) if parallel_enabled and shards else 1

    result_by_index: Dict[int, _AutomationFragmentResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
        future_by_shard = {
            executor.submit(
                _run_automation_shard_with_fallback,
                shard=shard,
                base_url=base_url,
                model_settings=model_settings,
            ): shard
            for shard in shards
        }
        for future in as_completed(future_by_shard):
            shard = future_by_shard[future]
            try:
                result_by_index[shard.index] = future.result()
            except Exception as exc:
                result_by_index[shard.index] = _build_deterministic_fragment(
                    shard,
                    used_fallback=True,
                    failed=True,
                    warning=f"Automation shard {shard.shard_id} failed and used deterministic fallback fragments: {exc}",
                )

    shard_results = [result_by_index[shard.index] for shard in shards]
    return _assemble_response(
        payload,
        shard_results,
        base_url=base_url,
        parallel_enabled=parallel_enabled,
        worker_count=worker_count,
    )


def _generate_small_model_pom(payload: AutomationInput, *, model_settings: Any) -> AutomationResponse:
    client = genai.Client(api_key=model_settings.gemini_api_key)
    response = client.models.generate_content(
        model=model_settings.model_name,
        contents=_build_pom_prompt(payload),
        config=genai.types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=8192,
        ),
    )
    pom_code = _strip_markdown_fences(extract_response_text(response))
    files = _extract_file_headers(pom_code) or [
        "tests/conftest.py",
        "tests/pages/base_page.py",
        "tests/pages/docs_page.py",
        "tests/test_playwright_docs.py",
    ]
    return AutomationResponse(
        status="generated",
        files=files,
        notes=pom_code,
        diagnostics={
            "shard_count": 1,
            "worker_count": 1,
            "parallel_enabled": False,
            "failed_shard_count": 0,
            "fallback_shard_count": 0,
            "represented_test_case_count": len(payload.test_cases),
            "generated_case_count": len(payload.test_cases),
            "manual_case_count": 0,
            "unsupported_case_count": 0,
            "merge_warnings": [],
        },
        case_diagnostics=[
            _diagnostic(
                test_case,
                status="generated",
                reason="Generated by the single-suite automation model path.",
                shard_id="automation-shard-01",
            )
            for test_case in payload.test_cases
        ],
    )


def generate_playwright_pom(payload: AutomationInput) -> AutomationResponse:
    if not payload.test_cases:
        return AutomationResponse(
            status="skipped",
            files=[],
            notes="No test cases provided; POM generation skipped.",
            diagnostics={
                "shard_count": 0,
                "worker_count": 0,
                "parallel_enabled": False,
                "failed_shard_count": 0,
                "fallback_shard_count": 0,
                "represented_test_case_count": 0,
                "generated_case_count": 0,
                "manual_case_count": 0,
                "unsupported_case_count": 0,
                "merge_warnings": [],
            },
        )

    generation_settings = get_generation_settings()
    model_settings = _get_model_settings_or_none()

    if model_settings is None:
        logging.warning("[AutomationAgent] POM generation is using deterministic fragments because GEMINI_API_KEY is unavailable")
        return _run_automation_coordinator(
            payload,
            generation_settings=generation_settings,
            model_settings=None,
        )

    if _should_use_parallel_automation_generation(payload, generation_settings):
        return _run_automation_coordinator(
            payload,
            generation_settings=generation_settings,
            model_settings=model_settings,
        )

    try:
        return _generate_small_model_pom(payload, model_settings=model_settings)
    except Exception as exc:
        logging.warning("[AutomationAgent] POM generation fell back to deterministic fragments: %s", exc)
        return _run_automation_coordinator(
            payload,
            generation_settings=generation_settings,
            model_settings=None,
        )
