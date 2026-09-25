from ..services.guidance_service import guidance_text, submit_with_guidance
from ..services.automation_validation import missing_evidence_reason, test_function_name, validate_artifacts
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
    interface_evidence: tuple = ()

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


def _plan_automation_shards(test_cases: List[TestCase], interface_evidence=()) -> List[_AutomationShard]:
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
            interface_evidence=tuple(item for item in interface_evidence if item.test_case_id in {case.id for case in group_cases}),
        )
        for index, (group_name, group_cases) in enumerate(groups, start=1)
    ]


def _should_use_parallel_automation_generation(payload: AutomationInput, generation_settings: GenerationSettings) -> bool:
    if not generation_settings.parallel_automation_generation_enabled:
        return False
    return len(payload.test_cases) >= generation_settings.parallel_automation_min_cases


def _evidenced_prompt(payload: AutomationInput) -> str:
    return f"""Generate Playwright Python sync API artifacts using only the supplied evidence.
Target URL: {payload.target_base_url}
Source cases (preserve all steps, preconditions, test data and expected results):
{json.dumps([case.model_dump(mode="json") for case in payload.test_cases], indent=2)}
Observed interface evidence and assertion mappings:
{json.dumps([item.model_dump(mode="json") for item in payload.interface_evidence], indent=2)}
Required test function names:
{json.dumps({case.id: test_function_name(case.id) for case in payload.test_cases})}

Rules:
- Do not invent navigation, selectors, input values or credentials. Only supplied target/allowed_urls may be passed to goto; use explicit literal URLs.
- Use from playwright.sync_api import Page, expect. Keep the default TLS verification; no ignore_https_errors, fixed sleeps, skip/xfail, suppressed assertions or empty smoke substitutes.
- Emit exactly one named test function per case. Each evidenced assertion must be a direct expect(page...).METHOD(EXPECTED) expression statement in that function, with the observed locator and value. Do not hide assertions in conditions, helpers or comments. No decorators on test functions.
- Page objects may encapsulate evidenced actions; assertions remain direct in the test for independent validation. Do not substitute body-visible for a business expectation.
- Keep a conventional tests/conftest.py only if needed. Emit valid Python. Never import or run generated code as part of generation.
"""


def _build_pom_prompt(payload: AutomationInput) -> str:
    return (
        _evidenced_prompt(payload)
        + "\nOutput ONLY Python files separated by # === FILE: tests/test_cases.py === headers. No markdown fences or explanatory prose."
    )


def _build_fragment_prompt(shard: _AutomationShard, *, base_url: str) -> str:
    payload = AutomationInput(test_cases=shard.test_cases, target_base_url=base_url, interface_evidence=list(shard.interface_evidence))
    return (
        _evidenced_prompt(payload)
        + "\nReturn ONLY JSON with files: [{path: tests/generated/test_cases.py, content: Python source}] and case_diagnostics: [{test_case_id, status: generated, reason}]. Generate test fragments only; shared base_page.py, generated_page.py and conftest.py are provided."
    )


def _strip_markdown_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))


def _shared_project_files(base_url: str) -> Dict[str, str]:
    return {
        "tests/pages/base_page.py": f'''from playwright.sync_api import Page


class BasePage:
    """Navigate only to the explicitly supplied target."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def navigate(self) -> None:
        self.page.goto({json.dumps(base_url)})
''',
        "tests/pages/generated_page.py": '''from .base_page import BasePage


class GeneratedPage(BasePage):
    """Base for evidenced page actions; no invented smoke assertions."""
''',
        "tests/conftest.py": "# Use the default pytest-playwright fixtures and TLS verification.\n",
    }


def _is_manual_case(test_case: TestCase) -> bool:
    return str(test_case.automation_status or "").strip().lower() == "manual"


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
        source_expected_results=[step.expected for step in test_case.steps] + ([test_case.expected_result] if test_case.expected_result else []),
    )


def _build_deterministic_fragment(shard: _AutomationShard, *, used_fallback: bool, failed: bool, warning: Optional[str] = None) -> _AutomationFragmentResult:
    # Missing or invalid model output is not replaced by unrelated page smoke tests.
    reason = warning or "No validated artifact was produced. Supply interface evidence and retry generation."
    return _AutomationFragmentResult(
        shard=shard,
        files={},
        case_diagnostics=[
            _diagnostic(case, status="manual" if _is_manual_case(case) else "unsupported", reason=reason, shard_id=shard.shard_id) for case in shard.test_cases
        ],
        represented_case_ids={case.id for case in shard.test_cases},
        merge_warnings=[reason],
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
        contents=_build_fragment_prompt(shard, base_url=base_url) + guidance_text(),
        config=genai.types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=12000,
        ),
    )
    result = _parse_worker_fragment(extract_response_text(response), shard)
    payload = AutomationInput(test_cases=shard.test_cases, target_base_url=base_url, interface_evidence=list(shard.interface_evidence))
    issues = validate_artifacts(result.files, payload)
    if issues:
        raise ValueError("; ".join(issues))
    return _AutomationFragmentResult(
        shard=shard,
        files=result.files,
        case_diagnostics=[
            _diagnostic(case, status="generated", reason="Syntax and evidenced assertions validated; not executed.", shard_id=shard.shard_id)
            for case in shard.test_cases
        ],
        represented_case_ids={case.id for case in shard.test_cases},
        merge_warnings=result.merge_warnings,
    )


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
            warning=f"Automation shard {shard.shard_id} used unsupported diagnostics because model credentials are unavailable.",
        )

    try:
        return _run_model_automation_fragment_worker(
            shard=shard,
            base_url=base_url,
            model_name=model_settings.model_name,
            api_key=model_settings.gemini_api_key,
        )
    except Exception as exc:
        logging.warning("[AutomationAgent] shard %s returned no validated artifact: %s", shard.shard_id, exc)
        return _build_deterministic_fragment(
            shard,
            used_fallback=True,
            failed=True,
            warning=f"Automation shard {shard.shard_id} failed and used unsupported diagnostics: {exc}",
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
            files_by_path[merged_path] = source

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

    generated_ids = {item.test_case_id for item in case_diagnostics if item.status == "generated"}
    eligible_payload = payload.model_copy(
        update={
            "test_cases": [case for case in payload.test_cases if case.id in generated_ids],
            "interface_evidence": [item for item in payload.interface_evidence if item.test_case_id in generated_ids],
        }
    )
    issues = validate_artifacts(files_by_path, eligible_payload) if generated_ids else []
    if issues:
        files_by_path = {}
        merge_warnings.extend(issues)
        case_diagnostics = [
            _diagnostic(case, status="unsupported", reason="Assembled artifact validation failed: " + "; ".join(issues)) for case in payload.test_cases
        ]

    files = list(files_by_path)
    notes = "\n\n".join(f"# === FILE: {path} ===\n{content.rstrip()}" for path, content in files_by_path.items())
    generated_count = sum(1 for diagnostic in case_diagnostics if diagnostic.status in {"generated", "fallback"})
    manual_count = sum(1 for diagnostic in case_diagnostics if diagnostic.status == "manual")
    unsupported_count = sum(1 for diagnostic in case_diagnostics if diagnostic.status == "unsupported")
    failed_shards = sum(1 for result in shard_results if result.failed) + bool(issues)
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
    shards = _plan_automation_shards(payload.test_cases, payload.interface_evidence)
    parallel_enabled = _should_use_parallel_automation_generation(payload, generation_settings)
    worker_count = min(generation_settings.parallel_automation_max_workers, len(shards)) if parallel_enabled and shards else 1

    result_by_index: Dict[int, _AutomationFragmentResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
        future_by_shard = {
            submit_with_guidance(
                executor,
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
                    warning=f"Automation shard {shard.shard_id} failed and used unsupported diagnostics: {exc}",
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
        contents=_build_pom_prompt(payload) + guidance_text(),
        config=genai.types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=8192,
        ),
    )
    pom_code = _strip_markdown_fences(extract_response_text(response))
    sections = re.split(r"(?m)^#\s*=== FILE: ([^\n]+?)\s*===\s*$", pom_code)
    files_by_path = {}
    if sections[0].strip():
        raise ValueError("Python output must use explicit file headers.")
    for index in range(1, len(sections), 2):
        path = sections[index].strip()
        if path in files_by_path:
            raise ValueError("Duplicate artifact paths.")
        files_by_path[path] = sections[index + 1]
    issues = validate_artifacts(files_by_path, payload)
    if issues:
        raise ValueError("; ".join(issues))
    return AutomationResponse(
        status="generated",
        files=list(files_by_path),
        notes=pom_code,
        diagnostics={"shard_count": 1, "worker_count": 1, "parallel_enabled": False, "failed_shard_count": 0, "fallback_shard_count": 0, "merge_warnings": []},
        case_diagnostics=[
            _diagnostic(
                case, status="generated", reason="Syntax and supplied interface/assertion evidence validated; not executed.", shard_id="automation-shard-01"
            )
            for case in payload.test_cases
        ],
    )


def _finalize_response(response: AutomationResponse, original: AutomationInput, blocked) -> AutomationResponse:
    by_id = {item.test_case_id: item for item in response.case_diagnostics}
    by_id.update({item.test_case_id: item for item in blocked})
    response.case_diagnostics = [
        by_id.get(case.id) or _diagnostic(case, status="unsupported", reason="No validated artifact represents this case.") for case in original.test_cases
    ]
    for case, diagnostic in zip(original.test_cases, response.case_diagnostics):
        diagnostic.source_expected_results = [step.expected for step in case.steps] + ([case.expected_result] if case.expected_result else [])
    generated = sum(item.status == "generated" for item in response.case_diagnostics)
    response.diagnostics.update(
        {
            "represented_test_case_count": len(response.case_diagnostics),
            "generated_case_count": generated,
            "manual_case_count": sum(item.status == "manual" for item in response.case_diagnostics),
            "unsupported_case_count": sum(item.status == "unsupported" for item in response.case_diagnostics),
            "artifact_validation": "passed" if generated else "failed" if response.diagnostics.get("failed_shard_count") else "not_applicable",
            "target_configured": bool(original.target_base_url),
            "execution_status": "not_executed",
        }
    )
    response.status = "generated" if generated == len(original.test_cases) and generated else "partial" if generated else "skipped"
    if not generated:
        response.files = []
        response.notes = "No validated executable artifact was produced. Review per-case diagnostics and supply missing evidence."
    return response


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

    original = payload
    blocked = []
    eligible = []
    for case in payload.test_cases:
        reason = missing_evidence_reason(case, payload)
        if reason:
            blocked.append(_diagnostic(case, status="unsupported" if not case.steps else "manual", reason=reason))
        else:
            eligible.append(case)
    if not eligible:
        return _finalize_response(
            AutomationResponse(status="skipped", files=[], diagnostics={"shard_count": 0, "worker_count": 0, "parallel_enabled": False}), original, blocked
        )
    eligible_ids = {case.id for case in eligible}
    payload = payload.model_copy(
        update={"test_cases": eligible, "interface_evidence": [item for item in payload.interface_evidence if item.test_case_id in eligible_ids]}
    )
    generation_settings = get_generation_settings()
    model_settings = _get_model_settings_or_none()
    if model_settings is None or _should_use_parallel_automation_generation(payload, generation_settings):
        response = _run_automation_coordinator(payload, generation_settings=generation_settings, model_settings=model_settings)
    else:
        try:
            response = _generate_small_model_pom(payload, model_settings=model_settings)
        except Exception as exc:
            response = AutomationResponse(
                status="skipped",
                files=[],
                diagnostics={"shard_count": 1, "failed_shard_count": 1, "merge_warnings": [str(exc)]},
                case_diagnostics=[
                    _diagnostic(case, status="unsupported", reason=f"Artifact validation/generation failed: {exc}") for case in payload.test_cases
                ],
            )
    return _finalize_response(response, original, blocked)
