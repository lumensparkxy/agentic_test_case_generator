#!/usr/bin/env python3
"""Measure fault detection by authored or live-generated cases on synthetic Chromium targets."""

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from browser_benchmark_fixtures import FIXTURES, Defect, Fixture, LiteralSubmission
from evaluate_guidance import ROOT, canonical_hash, evaluate

from app.config import DEFAULT_MODEL_NAME, ExecutionSettings
from app.contracts.execution import ExecutionRunItem
from app.contracts.grounding import EnrichInput
from app.contracts.quality_feedback import ExecutionPrerequisite
from app.contracts.test_cases import TestCase, TestStep
from app.services.browser_handoff import _compiled_steps, validate_browser_handoff
from app.services.browser_samples import declared_browser_samples, materialize_browser_sample
from app.services.context_grounding import build_grounded_context
from app.services.execution_service import _annotation_description, _iter_playwright_tests, preview_execution, run_execution
from plain_english_test_framework.compiler import CompilerError
from plain_english_test_framework.ir_validator import IrValidationError
from plain_english_test_framework.spec_parser import SpecValidationError


@contextmanager
def loopback_pages(port: int):
    pages: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if (document := pages.get(self.path)) is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(document.encode())

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", pages
        finally:
            server.shutdown()
            worker.join()


def fixture_context(fixture: Fixture, target: str) -> EnrichInput:
    prerequisites = []
    if fixture.manual_requirement:
        prerequisites.append(
            ExecutionPrerequisite(
                name="externalAuditAccess",
                kind="environment",
                description="The external audit store, auditor access and checklist are unavailable in the fixture.",
                requirement_ids=[fixture.manual_requirement],
            )
        )
    context = EnrichInput(
        app_link=target,
        requirements=[{"id": key, "text": text} for key, text in fixture.requirements.items()],
        notes=fixture.notes.replace("{target}", target),
        execution_prerequisites=prerequisites,
    )
    # Supply the fixture's known HTML through the normal analyzer. The real
    # artifact fetcher intentionally rejects loopback URLs.
    context.grounded_context = build_grounded_context(
        context,
        fetcher=lambda url: {"url": url, "status": "Analyzed", "content_type": "text/html", "text": fixture.pages[fixture.route]},
    )
    return context


def authored_cases(fixture: Fixture, context: EnrichInput) -> list[TestCase]:
    cases = []
    samples = declared_browser_samples(context.notes)
    for requirement, steps in fixture.controls:
        is_manual = requirement == fixture.manual_requirement
        if not is_manual:
            steps = ((f'Open "{context.app_link}"', ""), *steps)
        authored_steps = []
        for index, (action, expected) in enumerate(steps, start=1):
            action = action.replace("{target}", str(context.app_link))
            action, expected, value = materialize_browser_sample(action, expected, samples) or (action, expected, None)
            authored_steps.append(TestStep(step=index, action=action, expected=expected, test_data=value))
        cases.append(
            TestCase(
                id=f"CONTROL-{requirement}",
                title=fixture.requirements[requirement],
                automation_status="Manual" if is_manual else "To Be Automated",
                linked_requirement_ids=[requirement],
                execution_prerequisites=context.execution_prerequisites if is_manual else [],
                steps=authored_steps,
            )
        )
    return cases


def failed_checks(item: ExecutionRunItem) -> list[str]:
    if not item.report_json_path or not item.spec_id or item.status != "failed":
        return []
    try:
        document = json.loads(Path(item.report_json_path).read_text())
    except OSError, ValueError:
        return []
    checks = []
    for test in _iter_playwright_tests(document):
        if _annotation_description(test, "caseId") != item.spec_id:
            continue
        attempts = test.get("results", [])
        if not attempts or attempts[-1].get("status") not in {"failed", "timedOut"}:
            continue
        for step in attempts[-1].get("steps", []):
            if step.get("error") and (title := step.get("title")):
                checks.append(title.split(" ", 1)[-1])
    return sorted(set(checks))


def run_cases(cases, selected, target, settings) -> dict[str, dict]:
    if not selected:
        return {}
    run = run_execution(cases, selected_test_case_ids=selected, target_base_url=target, settings=settings)
    return {
        item.id: {
            "case_id": item.source_test_case_id,
            "status": item.status,
            "failing_checks": failed_checks(item),
            "report": item.report_json_path,
            "issues": sorted({issue.code for issue in item.issues}),
        }
        for item in run.results
    }


def defect_status(defect: Defect, eligible: list[str], healthy: dict[str, dict], broken: dict[str, dict]) -> str:
    if not eligible:
        return "no_executable_case"
    qualified = [candidate for candidate in eligible if healthy.get(candidate, {}).get("status") == "passed"]
    if not qualified:
        return "no_healthy_pass"
    outcomes = [broken.get(candidate, {}) for candidate in qualified]
    if any(outcome.get("status") == "failed" and defect.failing_check in outcome.get("failing_checks", []) for outcome in outcomes):
        return "detected"
    if any(outcome.get("status") == "passed" for outcome in outcomes):
        return "survived"
    if any(outcome.get("status") == "failed" and outcome.get("failing_checks") for outcome in outcomes):
        return "wrong_failure"
    return "runtime_unavailable"


def has_exact_literal_submission(steps: list[dict], submission: LiteralSubmission) -> bool:
    field = {"strategy": "label", "value": submission.field, "exact": True}
    button = {"strategy": "role", "role": "button", "value": submission.button, "exact": True}
    filled = None
    submitted = []
    for step in steps:
        locator = step.get("locator", {})
        if step["action"] == "navigate":
            filled = None
        elif step["action"] in {"fill", "fill_literal"} and locator.get("strategy") == "label" and locator.get("value") == submission.field:
            filled = step["value"] if step["action"] == "fill_literal" and locator == field else None
        elif step["action"] == "click" and locator == button:
            submitted.append(filled)
    return submitted == [submission.value]


def _fixture_hash(fixture: Fixture) -> str:
    data = asdict(fixture)
    for defect in data["defects"]:
        if defect["submission"] is None:
            del defect["submission"]
    return canonical_hash(data)


def measure_fixture(fixture, context, cases, pages, settings):
    target = str(context.app_link)
    handoff_warnings = validate_browser_handoff(cases, context, context.requirements)
    preview = preview_execution(cases, target_base_url=target, settings=settings)
    requirements_by_case = {case.id: case.linked_requirement_ids for case in cases}
    pages.clear()
    pages.update(fixture.documents())
    healthy = run_cases(cases, [candidate.id for candidate in preview.executable], target, settings)
    compiled = {}
    if any(defect.submission for defect in fixture.defects):
        for candidate in preview.executable:
            try:
                compiled[candidate.id] = _compiled_steps(candidate, target)
            except CompilerError, IrValidationError, SpecValidationError:
                continue

    defects = []
    for defect in fixture.defects:
        linked = [candidate.id for candidate in preview.executable if defect.requirement in requirements_by_case[candidate.source_test_case_id]]
        eligible = [
            candidate for candidate in linked if defect.submission is None or has_exact_literal_submission(compiled.get(candidate, []), defect.submission)
        ]
        selected = [candidate for candidate in eligible if healthy.get(candidate, {}).get("status") == "passed"]
        pages.update(fixture.documents(defect))
        broken = run_cases(cases, selected, target, settings)
        status = "exact_payload_unverified" if linked and not eligible else defect_status(defect, eligible, healthy, broken)
        defects.append(
            {
                "defect": defect.name,
                "requirement": defect.requirement,
                "intended_failing_check": defect.failing_check,
                "linked_candidates": linked,
                "eligible_candidates": eligible,
                "status": status,
                "broken_outcomes": broken,
            }
        )

    manual = fixture.manual_requirement
    manual_cases = [case.id for case in cases if manual and manual in case.linked_requirement_ids and case.automation_status == "Manual" and case.steps]
    manual_executable = [
        candidate.source_test_case_id for candidate in preview.executable if manual and manual in requirements_by_case[candidate.source_test_case_id]
    ]
    passed = (
        len(healthy) == len(preview.executable)
        and all(row["status"] == "passed" for row in healthy.values())
        and all(probe["status"] == "detected" for probe in defects)
        and (manual is None or (bool(manual_cases) and not manual_executable))
    )
    return {
        "fixture_hash": _fixture_hash(fixture),
        "passed": passed,
        "preview": preview.summary.model_dump(),
        "handoff_warnings": handoff_warnings,
        "blocked_checks": {candidate.source_test_case_id: [gap.reason_code for gap in candidate.unsupported_steps] for candidate in preview.unsupported},
        "cases": [{"case_id": case.id, "requirements": case.linked_requirement_ids, "automation_status": case.automation_status} for case in cases],
        "healthy_outcomes": healthy,
        "defects": defects,
        "manual_requirement": manual,
        "manual_case_ids": manual_cases,
        "manual_executable_case_ids": manual_executable,
    }


def benchmark(*, live: bool, model: str, port: int, output: Path, fixture_names: tuple[str, ...] = ()):
    mode = "live" if live else "authored_controls"
    settings = ExecutionSettings(artifact_root=output.parent / f"{output.stem}-browser-artifacts")
    report = {"version": 1, "mode": mode, "model": model if live else None, "fixtures": {}}
    with loopback_pages(port) as (origin, pages), patch.dict(os.environ, {"PETF_PLAYWRIGHT_BROWSER_CHANNEL": "chromium"}):
        os.environ.pop("PETF_PLAYWRIGHT_WEB_SERVER_COMMAND", None)
        for name in fixture_names or FIXTURES:
            fixture = FIXTURES[name]
            context = fixture_context(fixture, origin + fixture.route)
            inputs = {
                "requirements": [requirement.model_dump(mode="json", exclude_none=True) for requirement in context.requirements],
                "context": context.model_dump(mode="json", exclude={"requirements"}, exclude_none=True),
                "workflow_timeout_seconds": 120,
            }
            if live:
                generated = evaluate(live=True, model=model, stages=("test_cases",), fixtures={name: inputs}, guidance_arms=("baseline",))
                evidence_path = output.parent / f"{output.stem}-{name}-generation.json"
                evidence_path.write_text(json.dumps(generated, indent=2) + "\n")
                row = generated["results"][0]
                delivered = row.get("output", {})
                usage = row.get("usage") or {}
                cases = [TestCase.model_validate(case) for case in delivered.get("test_cases", [])]
                generation = {
                    "status": row["status"],
                    "approved": delivered.get("approved"),
                    "generation_tasks": len(delivered.get("generation_tasks", [])),
                    "case_count": len(cases),
                    "latency_seconds": row.get("latency_seconds"),
                    "model_calls": usage.get("calls", 0),
                    "observed_models": sorted({request["model"] for request in usage.get("requests", []) if request.get("model")}),
                    "failed_model_calls": usage.get("failed_calls", 0),
                    "report": str(evidence_path),
                }
            else:
                cases = authored_cases(fixture, context)
                generation = {"status": "authored", "approved": None, "case_count": len(cases), "model_calls": 0}
            measured = measure_fixture(fixture, context, cases, pages, settings)
            report["fixtures"][name] = {"input_hash": canonical_hash(inputs), "generation": generation, **measured}
            print(f"{name}: " + ", ".join(f"{item['defect']}={item['status']}" for item in measured["defects"]), flush=True)
    report["provider_verified"] = (
        all(
            fixture["generation"]["model_calls"] > 0
            and fixture["generation"]["failed_model_calls"] == 0
            and fixture["generation"]["observed_models"] == [model]
            for fixture in report["fixtures"].values()
        )
        if live
        else None
    )
    report["passed"] = all(fixture["passed"] for fixture in report["fixtures"].values()) and report["provider_verified"] is not False
    output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Generate one new suite per fixture using the configured Gemini provider.")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--port", type=int, default=58946)
    parser.add_argument("--fixture", action="append", choices=FIXTURES, help="Select specific fixtures instead of running the full set.")
    parser.add_argument("--output", type=Path, required=True, help="Report file outside this repository.")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("keep benchmark evidence outside the repository")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = benchmark(live=args.live, model=args.model, port=args.port, output=output, fixture_names=tuple(dict.fromkeys(args.fixture or ())))
    print(f"Report: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
