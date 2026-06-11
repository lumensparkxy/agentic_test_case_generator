from pathlib import Path
import os
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import ExecutionSettings
from app.models import TestCase, TestStep
from app.services.execution_service import preview_execution, run_execution
from plain_english_test_framework.local_runner import _node_resolution_env


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

    def test_run_compiles_and_invokes_local_runner_once_per_executable_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            fake_run = SimpleNamespace(
                returncode=0,
                stdout="passed",
                stderr="",
                generated_spec_path=root / "generated" / "tc_001.spec.ts",
                paths=SimpleNamespace(artifacts_dir=root / "artifacts" / "run"),
            )

            with patch("app.services.execution_service.run_local_playwright", return_value=fake_run) as runner:
                response = run_execution([_browser_case()], settings=settings)

        self.assertEqual(response.status, "passed")
        self.assertEqual(response.summary.passed, 1)
        self.assertEqual(response.results[0].status, "passed")
        self.assertTrue(response.results[0].ir_path.endswith("tc_001.ir.json"))
        runner.assert_called_once()

    def test_run_compiles_documentation_url_assertion_before_invoking_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            fake_run = SimpleNamespace(
                returncode=0,
                stdout="passed",
                stderr="",
                generated_spec_path=root / "generated" / "tc_doc.spec.ts",
                paths=SimpleNamespace(artifacts_dir=root / "artifacts" / "run"),
            )

            with patch("app.services.execution_service.run_local_playwright", return_value=fake_run) as runner:
                response = run_execution(
                    [_documentation_case()],
                    target_base_url="https://playwright.dev/python/",
                    settings=settings,
                )

        self.assertEqual(response.status, "passed")
        self.assertEqual(response.summary.passed, 1)
        self.assertTrue(response.results[0].ir_path.endswith("tc_doc.ir.json"))
        runner.assert_called_once()

    def test_run_returns_failed_when_one_playwright_candidate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            fake_run = SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="expect failed",
                generated_spec_path=root / "generated" / "tc_001.spec.ts",
                paths=SimpleNamespace(artifacts_dir=root / "artifacts" / "run"),
            )

            with patch("app.services.execution_service.run_local_playwright", return_value=fake_run):
                response = run_execution([_browser_case()], settings=settings)

        self.assertEqual(response.status, "failed")
        self.assertEqual(response.summary.failed, 1)
        self.assertEqual(response.results[0].status, "failed")
        self.assertEqual(response.results[0].returncode, 1)


if __name__ == "__main__":
    unittest.main()
