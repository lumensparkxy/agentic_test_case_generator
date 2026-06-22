from pathlib import Path
import json
import os
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import ExecutionSettings
from app.models import TestCase, TestStep
from app.services.execution_service import preview_execution, run_execution
from plain_english_test_framework.compiler import CompilerError, compile_spec_file
from plain_english_test_framework.local_runner import LocalPlaywrightRunnerError, _node_resolution_env
from plain_english_test_framework.playwright_generator import generate_playwright_spec
from plain_english_test_framework.validation import ValidationIssue


def _browser_case(case_id: str = "TC-001") -> TestCase:
    return TestCase(
        id=case_id,
        title="Sign in with valid credentials",
        description="Checks a deterministic browser login flow.",
        priority="High",
        type="E2E",
        status="Ready",
        automation_status="Automated",
        component="Authentication",
        tags=["smoke"],
        linked_requirement_ids=["REQ-001"],
        scenario_refs=["SCN-001"],
        source_refs=["ctx-login"],
        steps=[
            TestStep(step=1, action="Open https://example.test/login", expected='"Email" is visible'),
            TestStep(step=2, action='Enter "mira@example.com" into the Email field', expected='"Email" should equal "mira@example.com"'),
            TestStep(step=3, action='Click the "Sign in" button', expected='"Dashboard" is displayed'),
        ],
    )


def _documentation_case(case_id: str = "TC-DOC") -> TestCase:
    return TestCase(
        id=case_id,
        title="Verify Playwright documentation section",
        description="Checks a generated documentation-style browser validation case.",
        priority="Medium",
        type="E2E",
        status="Ready",
        automation_status="Automated",
        component="Documentation",
        tags=["docs"],
        linked_requirement_ids=["REQ-DOC"],
        steps=[
            TestStep(
                step=1,
                action="Locate the 'Playwright Test' section or pathway on the landing page.",
                expected="The Playwright Test section is visible and clearly demarcated.",
            ),
            TestStep(
                step=2,
                action="Verify the active URL in the browser address bar.",
                expected="The URL is exactly 'https://playwright.dev/python/' and does not redirect.",
            ),
            TestStep(
                step=3,
                action="Scan the text content and feature list within the Playwright Test section.",
                expected="The text explicitly describes auto-waiting and assertions.",
            ),
        ],
    )


def _link_navigation_case(case_id: str = "TC-LINK") -> TestCase:
    return TestCase(
        id=case_id,
        title="Navigate to exact documentation link",
        description="Checks link-role navigation generated from grounded context.",
        priority="Medium",
        type="E2E",
        status="Ready",
        automation_status="Automated",
        component="Documentation",
        tags=["docs"],
        linked_requirement_ids=["REQ-DOC-LINK"],
        source_refs=["ART-APP-01"],
        steps=[
            TestStep(step=1, action="Open https://playwright.dev/python/", expected='"Get started" is visible'),
            TestStep(
                step=2,
                action='Click the "Get started" link',
                expected='URL is exactly "https://playwright.dev/python/docs/intro"',
            ),
        ],
    )


def _settings(root: Path) -> ExecutionSettings:
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    config = runtime / "playwright.config.ts"
    config.write_text("export default {};\n", encoding="utf-8")
    return ExecutionSettings(
        enabled=True,
        artifact_root=root / "artifacts",
        default_base_url="https://example.test",
        playwright_config_path=config,
        runtime_cwd=runtime,
        max_cases_per_request=20,
    )


def _write_playwright_report(artifacts_dir: Path, statuses_by_case_id: dict[str, str]) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "html-report").mkdir(parents=True, exist_ok=True)
    suites = []
    for case_id, status in statuses_by_case_id.items():
        playwright_status = "expected" if status == "passed" else "unexpected"
        suites.append(
            {
                "title": f"{case_id}.spec.ts",
                "file": f"{case_id}.spec.ts",
                "specs": [
                    {
                        "title": f"{case_id} generated case",
                        "id": f"{case_id}-spec",
                        "file": f"{case_id}.spec.ts",
                        "ok": status == "passed",
                        "tests": [
                            {
                                "annotations": [
                                    {"type": "specId", "description": case_id},
                                    {"type": "caseId", "description": case_id},
                                ],
                                "status": playwright_status,
                                "results": [
                                    {
                                        "status": status,
                                        "stdout": [{"text": f"{case_id} stdout"}],
                                        "stderr": [],
                                        "errors": [] if status == "passed" else [{"message": f"{case_id} assertion failed"}],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    report = {
        "config": {},
        "errors": [],
        "stats": {
            "expected": sum(1 for status in statuses_by_case_id.values() if status == "passed"),
            "unexpected": sum(1 for status in statuses_by_case_id.values() if status != "passed"),
            "flaky": 0,
            "skipped": 0,
        },
        "suites": [{"title": "generated", "file": "", "specs": [], "suites": suites}],
    }
    (artifacts_dir / "results.json").write_text(json.dumps(report), encoding="utf-8")


def _fake_batch_run(statuses_by_case_id: dict[str, str], *, returncode: int = 0):
    def _run(spec_paths, *, generated_dir, artifacts_dir, config_path, cwd):
        artifacts = Path(artifacts_dir)
        _write_playwright_report(artifacts, statuses_by_case_id)
        return SimpleNamespace(
            returncode=returncode,
            stdout="batch stdout",
            stderr="batch stderr",
            generated_spec_path=None,
            generated_spec_paths=tuple(Path(path) for path in spec_paths),
            paths=SimpleNamespace(
                artifacts_dir=artifacts,
                html_report_dir=artifacts / "html-report",
            ),
        )

    return _run


class ExecutionServiceTests(unittest.TestCase):
    def test_local_runner_exposes_runtime_node_modules_to_generated_specs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "runtime"
            (runtime / "node_modules").mkdir(parents=True)
            with patch.dict(os.environ, {"NODE_PATH": "/existing/node_modules"}):
                env = _node_resolution_env(runtime)

        self.assertEqual(
            env["NODE_PATH"].split(os.pathsep),
            [str(runtime / "node_modules"), "/existing/node_modules"],
        )

    def test_default_preview_limit_allows_large_generated_suites(self) -> None:
        settings = ExecutionSettings()
        cases = [_browser_case(f"TC-{index:03d}") for index in range(1, 26)]

        response = preview_execution(cases, settings=settings)

        self.assertEqual(settings.max_cases_per_request, 9999)
        self.assertEqual(response.summary.executable, 25)
        self.assertEqual(response.warnings, [])

    def test_preview_converts_safe_browser_steps_to_candidate_spec(self) -> None:
        response = preview_execution([_browser_case()], target_base_url="https://example.test")

        self.assertEqual(response.summary.executable, 1)
        self.assertEqual(response.summary.manual, 0)
        candidate = response.executable[0]
        self.assertEqual(candidate.id, "tc_001")
        self.assertEqual(candidate.source_test_case_id, "TC-001")
        self.assertEqual(candidate.traceability_ids, ["REQ-001", "SCN-001", "ctx-login"])
        self.assertEqual(candidate.spec["id"], "tc_001")
        self.assertIn('Given I open "https://example.test/login"', candidate.spec["steps"])
        self.assertIn('When I enter "mira@example.com" into "Email"', candidate.spec["steps"])
        self.assertIn('And I click "Sign in"', candidate.spec["steps"])

    def test_preview_converts_documentation_checks_with_implicit_navigation(self) -> None:
        response = preview_execution([_documentation_case()], target_base_url="https://playwright.dev/python/")

        self.assertEqual(response.summary.executable, 1)
        self.assertEqual(response.summary.unsupported, 0)
        candidate = response.executable[0]
        self.assertEqual(candidate.status, "executable")
        self.assertIn("Some source steps were not executable", candidate.review_reasons[0])
        self.assertIn("implicit_navigation", {step.reason_code for step in candidate.unsupported_steps})
        self.assertIn('Given I open "https://playwright.dev/python/"', candidate.spec["steps"])
        self.assertIn('Then "Playwright Test" should be visible', candidate.spec["steps"])
        self.assertIn('And URL should be "https://playwright.dev/python/"', candidate.spec["steps"])

    def test_preview_and_compiler_preserve_link_role_clicks(self) -> None:
        response = preview_execution([_link_navigation_case()], target_base_url="https://playwright.dev/python/")

        self.assertEqual(response.summary.executable, 1)
        candidate = response.executable[0]
        self.assertIn('When I click link "Get started"', candidate.spec["steps"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "link_case.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(yaml.safe_dump(candidate.spec, sort_keys=False), encoding="utf-8")
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://playwright.dev/python/"}), encoding="utf-8")
            ir = compile_spec_file(spec_path, environment_path=env_path, environment_name="web")

        click_step = next(step for step in ir.raw["cases"][0]["steps"] if step["action"] == "click")
        self.assertEqual(click_step["locator"]["role"], "link")
        generated = generate_playwright_spec(ir).contents
        self.assertIn('page.getByRole("link", { name: "Get started" }).click();', generated)

    def test_preview_and_compiler_preserve_heading_role_assertions(self) -> None:
        heading_case = TestCase(
            id="TC-HEADING",
            title="Assert exact documentation heading",
            automation_status="Automated",
            steps=[
                TestStep(
                    step=1,
                    action="Open https://playwright.dev/python/docs/running-tests",
                    expected='heading "Running and debugging tests" is visible',
                )
            ],
        )

        response = preview_execution([heading_case], target_base_url="https://playwright.dev/python/")
        candidate = response.executable[0]
        self.assertIn('Then heading "Running and debugging tests" should be visible', candidate.spec["steps"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "heading_case.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(yaml.safe_dump(candidate.spec, sort_keys=False), encoding="utf-8")
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://playwright.dev/python/"}), encoding="utf-8")
            ir = compile_spec_file(spec_path, environment_path=env_path, environment_name="web")

        assertion_step = next(step for step in ir.raw["cases"][0]["steps"] if step["action"] == "assert_visible")
        self.assertEqual(assertion_step["locator"]["role"], "heading")
        generated = generate_playwright_spec(ir).contents
        self.assertIn('page.getByRole("heading", { name: "Running and debugging tests" })', generated)

    def test_preview_rejects_role_only_visible_assertions(self) -> None:
        role_only_case = TestCase(
            id="TC-ROLE-ONLY",
            title="Reject role-only heading assertion",
            automation_status="Automated",
            steps=[
                TestStep(
                    step=1,
                    action="Open https://playwright.dev/",
                    expected='"heading" should be visible',
                )
            ],
        )

        response = preview_execution([role_only_case], target_base_url="https://playwright.dev/")

        self.assertEqual(response.summary.executable, 0)
        self.assertEqual(response.summary.unsupported, 1)
        candidate = response.unsupported[0]
        reason_codes = {step.reason_code for step in candidate.unsupported_steps}
        self.assertIn("ambiguous_semantic_assertion", reason_codes)
        self.assertIn("missing_assertion", reason_codes)
        self.assertIn("exact accessible name", candidate.unsupported_steps[0].suggested_next_action)

    def test_preview_omits_ambiguous_semantic_assertions_from_executable_spec(self) -> None:
        mixed_case = TestCase(
            id="TC-MIXED-SEMANTIC",
            title="Keep exact copy and omit role-only assertion",
            automation_status="Automated",
            steps=[
                TestStep(
                    step=1,
                    action="Open https://playwright.dev/",
                    expected='"Playwright enables reliable web automation for testing, scripting, and AI agents." is visible',
                ),
                TestStep(step=2, action='"heading" should be visible', expected=""),
            ],
        )

        response = preview_execution([mixed_case], target_base_url="https://playwright.dev/")

        self.assertEqual(response.summary.executable, 1)
        candidate = response.executable[0]
        self.assertIn("ambiguous_semantic_assertion", {step.reason_code for step in candidate.unsupported_steps})
        self.assertNotIn('And "heading" should be visible', candidate.spec["steps"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "mixed_case.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(yaml.safe_dump(candidate.spec, sort_keys=False), encoding="utf-8")
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://playwright.dev/"}), encoding="utf-8")
            ir = compile_spec_file(spec_path, environment_path=env_path, environment_name="web")

        generated = generate_playwright_spec(ir).contents
        self.assertNotIn('getByText("heading")', generated)
        self.assertIn('page.getByText("Playwright enables reliable web automation for testing, scripting, and AI agents")', generated)

    def test_compiler_rejects_ambiguous_semantic_visible_text_specs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "ambiguous_heading.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(
                yaml.safe_dump(
                    {
                        "schemaVersion": "1.0",
                        "id": "ambiguous_heading",
                        "title": "Ambiguous heading assertion",
                        "steps": [
                            'Given I open "https://playwright.dev/"',
                            'Then "heading" should be visible',
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://playwright.dev/"}), encoding="utf-8")

            with self.assertRaises(CompilerError) as context:
                compile_spec_file(spec_path, environment_path=env_path, environment_name="web")

        self.assertEqual(context.exception.issues[0].code, "step.ambiguous_semantic_assertion")

    def test_preview_does_not_treat_launch_command_text_as_navigation(self) -> None:
        command_case = TestCase(
            id="TC-COMMAND",
            title="Verify MCP launch command",
            automation_status="Automated",
            steps=[
                TestStep(
                    step=1,
                    action="Locate the 'Playwright MCP' section or pathway on the landing page.",
                    expected="The Playwright MCP section is visible.",
                ),
                TestStep(
                    step=2,
                    action="Verify the launch command displayed in the MCP section.",
                    expected="A valid command to launch the MCP package is present, fully rendered, and copyable.",
                ),
            ],
        )

        response = preview_execution([command_case], target_base_url="https://playwright.dev/python/")

        self.assertEqual(response.summary.executable, 1)
        generated_steps = response.executable[0].spec["steps"]
        self.assertEqual(
            [step for step in generated_steps if 'I open "https://playwright.dev/python/"' in step],
            ['Given I open "https://playwright.dev/python/"'],
        )

    def test_preview_marks_manual_cases_as_manual_by_default(self) -> None:
        manual_case = _browser_case("TC-MANUAL")
        manual_case.automation_status = "Manual"

        response = preview_execution([manual_case])

        self.assertEqual(response.summary.manual, 1)
        self.assertEqual(response.manual[0].status, "manual")
        self.assertIn("Automation Status is Manual", response.manual[0].review_reasons[0])

    def test_preview_preserves_unsupported_sap_steps_with_reasons(self) -> None:
        sap_case = TestCase(
            id="TC-SAP",
            title="Reset SAP user",
            automation_status="Automated",
            steps=[
                TestStep(
                    step=1,
                    action="Execute SAP GUI transaction SU01",
                    expected="User maintenance screen opens",
                    test_data="SAP user ID",
                )
            ],
        )

        response = preview_execution([sap_case])

        self.assertEqual(response.summary.unsupported, 1)
        unsupported_step = response.unsupported[0].unsupported_steps[0]
        self.assertEqual(unsupported_step.reason_code, "unsupported_non_browser_domain")
        self.assertEqual(unsupported_step.action, "Execute SAP GUI transaction SU01")
        self.assertEqual(unsupported_step.test_data, "SAP user ID")

    def test_run_compiles_candidates_and_invokes_playwright_once_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)

            with patch(
                "app.services.execution_service.run_local_playwright_specs",
                side_effect=_fake_batch_run({"tc_001": "passed", "tc_002": "passed"}),
            ) as runner:
                response = run_execution([_browser_case(), _browser_case("TC-002")], settings=settings)

        self.assertEqual(response.status, "passed")
        self.assertEqual(response.summary.passed, 2)
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].status, "passed")
        self.assertTrue(response.results[0].ir_path.endswith("tc_001.ir.json"))
        self.assertTrue(response.results[1].ir_path.endswith("tc_002.ir.json"))
        self.assertEqual(len(response.playwright_report_paths), 1)
        self.assertTrue(response.playwright_report_paths[0].endswith("artifacts/playwright/run/html-report"))
        self.assertTrue(response.results[0].report_json_path.endswith("artifacts/playwright/run/results.json"))
        self.assertEqual(response.results[0].playwright_report_path, response.playwright_report_paths[0])
        self.assertEqual(response.results[1].playwright_report_path, response.playwright_report_paths[0])
        runner.assert_called_once()
        self.assertEqual(len(runner.call_args.args[0]), 2)

    def test_run_compiles_documentation_url_assertion_before_invoking_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            with patch(
                "app.services.execution_service.run_local_playwright_specs",
                side_effect=_fake_batch_run({"tc_doc": "passed"}),
            ) as runner:
                response = run_execution(
                    [_documentation_case()],
                    target_base_url="https://playwright.dev/python/",
                    settings=settings,
                )

        self.assertEqual(response.status, "passed")
        self.assertEqual(response.summary.passed, 1)
        self.assertTrue(response.results[0].ir_path.endswith("tc_doc.ir.json"))
        runner.assert_called_once()

    def test_run_maps_per_case_status_from_consolidated_playwright_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)

            with patch(
                "app.services.execution_service.run_local_playwright_specs",
                side_effect=_fake_batch_run({"tc_001": "passed", "tc_002": "failed"}, returncode=1),
            ):
                response = run_execution([_browser_case(), _browser_case("TC-002")], settings=settings)

        self.assertEqual(response.status, "failed")
        self.assertEqual(response.summary.failed, 1)
        self.assertEqual(response.summary.passed, 1)
        self.assertEqual([item.status for item in response.results], ["passed", "failed"])
        self.assertEqual(response.results[1].returncode, 1)
        self.assertEqual(response.results[1].stderr, "batch stderr")
        self.assertEqual(response.results[1].issues[0].message, "tc_002 assertion failed")

    def test_run_keeps_selected_non_executable_cases_skipped_outside_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            manual_case = _browser_case("TC-MANUAL")
            manual_case.automation_status = "Manual"

            with patch(
                "app.services.execution_service.run_local_playwright_specs",
                side_effect=_fake_batch_run({"tc_001": "passed"}),
            ) as runner:
                response = run_execution(
                    [_browser_case(), manual_case],
                    selected_test_case_ids=["TC-001", "TC-MANUAL"],
                    settings=settings,
                )

        self.assertEqual(response.status, "passed")
        self.assertEqual(response.summary.passed, 1)
        self.assertEqual(response.summary.skipped, 1)
        self.assertEqual([item.status for item in response.results], ["passed", "skipped"])
        self.assertEqual(response.results[1].source_test_case_id, "TC-MANUAL")
        runner.assert_called_once()

    def test_run_does_not_publish_report_paths_when_runner_fails_before_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            runner_error = LocalPlaywrightRunnerError((ValidationIssue("$", "npx was not found", "runner.npx_missing"),))

            with patch("app.services.execution_service.run_local_playwright_specs", side_effect=runner_error):
                response = run_execution([_browser_case()], settings=settings)

        self.assertEqual(response.status, "failed")
        self.assertEqual(response.summary.invalid, 1)
        self.assertEqual(response.results[0].status, "invalid")
        self.assertIsNone(response.results[0].report_json_path)
        self.assertIsNone(response.results[0].playwright_report_path)
        self.assertEqual(response.playwright_report_paths, [])


if __name__ == "__main__":
    unittest.main()
