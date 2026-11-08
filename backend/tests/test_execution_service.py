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
from app.models import ExecutionCandidate, ExecutionPreviewResponse, TestCase, TestStep
from app.services.execution_service import preview_execution, run_execution
from plain_english_test_framework.compiler import CompilerError, ResolvedEnvironment, compile_parsed_spec, compile_spec_file
from plain_english_test_framework.ir_validator import validate_ir_document
from plain_english_test_framework.local_runner import LocalPlaywrightRunnerError, _node_resolution_env
from plain_english_test_framework.playwright_generator import generate_playwright_spec
from plain_english_test_framework.spec_parser import parse_spec_document
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
                expected='"Playwright Test" is visible',
            ),
            TestStep(
                step=2,
                action="Verify the URL is exactly 'https://playwright.dev/python/'.",
                expected="",
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

    def test_preview_summary_matches_every_classified_bucket(self) -> None:
        manual_case = _browser_case("TC-MANUAL")
        manual_case.automation_status = "Manual"
        unsupported_case = TestCase(
            id="TC-UNSUPPORTED",
            title="Reset SAP user",
            automation_status="Automated",
            steps=[TestStep(step=1, action="Execute SAP GUI transaction SU01", expected="User maintenance screen opens")],
        )
        invalid_case = TestCase(
            id="TC-INVALID",
            title="No browser steps",
            automation_status="Automated",
            steps=[],
        )
        fixtures = {
            "empty": ([], {"executable": 0, "manual": 0, "unsupported": 0, "invalid": 0}),
            "executable": ([_browser_case()], {"executable": 1, "manual": 0, "unsupported": 0, "invalid": 0}),
            "mixed": (
                [_browser_case(), manual_case, unsupported_case, invalid_case],
                {"executable": 1, "manual": 1, "unsupported": 1, "invalid": 1},
            ),
            "unsupported": ([unsupported_case], {"executable": 0, "manual": 0, "unsupported": 1, "invalid": 0}),
            "invalid": ([invalid_case], {"executable": 0, "manual": 0, "unsupported": 0, "invalid": 1}),
        }

        for fixture_name, (cases, expected_counts) in fixtures.items():
            with self.subTest(fixture=fixture_name):
                response = preview_execution(cases, target_base_url="https://example.test")
                actual_counts = {
                    "executable": len(response.executable),
                    "manual": len(response.manual),
                    "unsupported": len(response.unsupported),
                    "invalid": len(response.invalid),
                }
                self.assertEqual(actual_counts, expected_counts)
                self.assertEqual(response.summary.model_dump(), actual_counts)

    def test_preview_contract_normalizes_mismatched_summary_to_candidate_lists(self) -> None:
        candidate = ExecutionCandidate(
            id="tc_001",
            source_test_case_id="TC-001",
            title="Sign in",
            status="executable",
        )

        response = ExecutionPreviewResponse(
            executable=[candidate],
            summary={"executable": 20, "manual": 4, "unsupported": 3, "invalid": 2},
        )

        self.assertEqual(
            response.summary.model_dump(),
            {"executable": 1, "manual": 0, "unsupported": 0, "invalid": 0},
        )

    def test_preview_assigns_unique_candidate_and_spec_ids_for_collisions(self) -> None:
        response = preview_execution(
            [_browser_case("TC-1"), _browser_case("TC 1"), _browser_case("TC-1")],
            target_base_url="https://example.test",
        )

        candidate_ids = [candidate.id for candidate in response.executable]
        self.assertEqual(candidate_ids, ["tc_1", "tc_1_2", "tc_1_3"])
        self.assertEqual([candidate.spec["id"] for candidate in response.executable], candidate_ids)
        self.assertEqual(
            response.warnings,
            ["Duplicate or colliding test case IDs were assigned unique execution candidate IDs."],
        )
        self.assertEqual(response.summary.executable, 3)

    def test_run_selects_one_colliding_source_case_by_unique_candidate_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = _settings(Path(temp_dir))
            with patch(
                "app.services.execution_service.run_local_playwright_specs",
                side_effect=_fake_batch_run({"tc_1_3": "passed"}),
            ) as runner:
                response = run_execution(
                    [_browser_case("TC-1"), _browser_case("TC 1"), _browser_case("TC-1")],
                    selected_test_case_ids=["tc_1_3"],
                    settings=settings,
                )

        self.assertEqual(response.status, "passed")
        self.assertEqual(response.summary.passed, 1)
        self.assertEqual([item.id for item in response.results], ["tc_1_3"])
        self.assertEqual(response.results[0].source_test_case_id, "TC-1")
        runner.assert_called_once()
        self.assertEqual(len(runner.call_args.args[0]), 1)

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
        self.assertIn('When I enter "mira@example.com" into field "Email"', candidate.spec["steps"])
        self.assertIn('And field "Email" value should equal "mira@example.com"', candidate.spec["steps"])
        self.assertIn('And I click "Sign in"', candidate.spec["steps"])

    def test_preview_converts_documentation_checks_with_implicit_navigation(self) -> None:
        response = preview_execution([_documentation_case()], target_base_url="https://playwright.dev/python/")

        self.assertEqual(response.summary.executable, 1)
        self.assertEqual(response.summary.unsupported, 0)
        candidate = response.executable[0]
        self.assertEqual(candidate.status, "executable")
        self.assertEqual(candidate.review_reasons, ["The configured target supplies the initial navigation step."])
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

    def test_preview_keeps_url_punctuation_only_inside_delimiters(self) -> None:
        bare = "https://example.test/directory?view=all#results"
        literal = "https://example.test/a(b)."
        for action, expected, url in (
            (f"Navigate to {bare}.", f"The URL should be {bare}.", bare),
            (f'Open "{literal}".', f'URL should be "{literal}".', literal),
            (f'Open "{literal}".', f'The directory page loads successfully at "{literal}".', literal),
            (f"Open <{literal}>.", f"URL should be <{literal}>.", literal),
            (f"Open `{literal}`.", f"URL should be `{literal}`.", literal),
        ):
            with self.subTest(action=action):
                case = TestCase(id="TC-URL", title="Verify route", automation_status="Automated", steps=[TestStep(step=1, action=action, expected=expected)])
                preview = preview_execution([case], target_base_url="https://example.test")
                self.assertEqual(preview.executable[0].spec["steps"], [f'Given I open "{url}"', f'Then URL should be "{url}"'])

    def test_exact_visibility_intent_survives_preview_and_compilation(self) -> None:
        case = TestCase(
            id="TC-EXACT",
            title="Verify exact copy and a legacy partial match",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test/sign-in.", expected='exact heading "Sign in" is visible'),
                TestStep(step=2, action="Inspect the status message", expected='The exact visible text "No results" is displayed'),
                TestStep(step=3, action="Inspect the supporting heading", expected='heading "Help" is visible'),
                TestStep(step=4, action="Inspect the form heading", expected='exact heading "Account" is visible'),
                TestStep(step=5, action="Inspect confirmation", expected='exact text "You\'re signed in." is visible'),
                TestStep(step=6, action="Inspect notice", expected="""exact text 'Select "Search".' is visible"""),
            ],
        )
        preview = preview_execution([case], target_base_url="https://example.test")
        candidate = preview.executable[0]
        self.assertFalse(candidate.unsupported_steps)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "case.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(yaml.safe_dump(candidate.spec), encoding="utf-8")
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://example.test"}), encoding="utf-8")
            ir = compile_spec_file(spec_path, environment_path=env_path, environment_name="web")
        generated = generate_playwright_spec(ir).contents
        self.assertIn('page.getByRole("heading", { name: "Sign in", exact: true })', generated)
        self.assertIn('page.getByText("No results", { exact: true })', generated)
        self.assertIn('page.getByRole("heading", { name: "Help" })', generated)
        self.assertIn('page.getByRole("heading", { name: "Account", exact: true })', generated)
        self.assertIn('page.getByText("You\'re signed in.", { exact: true })', generated)
        self.assertIn(r'page.getByText("Select \"Search\".", { exact: true })', generated)

    def test_exact_click_preserves_button_and_link_names_alongside_legacy_clicks(self) -> None:
        url = "https://example.test"
        case = TestCase(
            id="TC-CLICK",
            title="Open the request and visit the receipt",
            automation_status="To Be Automated",
            steps=[
                TestStep(step=1, action=f'Open "{url}"', expected=""),
                TestStep(step=2, action='Click exact button "Open database"', expected=""),
                TestStep(step=3, action=r'Click exact link "Go to \"Receipt\""', expected=""),
                TestStep(step=4, action='Click the "Done" button', expected='exact text "Saved" is visible'),
            ],
        )
        preview = preview_execution([case], target_base_url=url)
        self.assertEqual(preview.summary.executable, 1)
        self.assertFalse(preview.executable[0].unsupported_steps)
        ir = compile_parsed_spec(parse_spec_document(preview.executable[0].spec), environment=ResolvedEnvironment("web", {"baseUrl": url}, url, None))
        clicks = [step["locator"] for step in ir.raw["cases"][0]["steps"] if step["action"] == "click"]
        self.assertEqual(
            clicks,
            [
                {"strategy": "role", "role": "button", "value": "Open database", "exact": True},
                {"strategy": "role", "role": "link", "value": 'Go to "Receipt"', "exact": True},
                {"strategy": "role", "role": "button", "value": "Done"},
            ],
        )
        generated = generate_playwright_spec(ir).contents
        self.assertIn('page.getByRole("button", { name: "Open database", exact: true }).click()', generated)
        self.assertIn(r'page.getByRole("link", { name: "Go to \"Receipt\"", exact: true }).click()', generated)
        self.assertIn('page.getByRole("button", { name: "Done" }).click()', generated)

    def test_exact_negative_visibility_preserves_role_and_text_without_reopening_page(self) -> None:
        url = "https://example.test/receipt"
        case = TestCase(
            id="TC-HIDDEN",
            title="Keep the receipt link out of view before saving",
            automation_status="To Be Automated",
            steps=[
                TestStep(step=1, action=f'Open "{url}"', expected=""),
                TestStep(step=2, action='Inspect the "Open receipt" link', expected='exact link "Open receipt" is not visible'),
                TestStep(step=3, action=r'exact text "Receipt \"saved\"" should not be visible', expected=""),
            ],
        )
        preview = preview_execution([case], target_base_url=url)
        self.assertEqual(preview.summary.executable, 1)
        self.assertFalse(preview.executable[0].unsupported_steps)
        ir = compile_parsed_spec(parse_spec_document(preview.executable[0].spec), environment=ResolvedEnvironment("web", {"baseUrl": url}, url, None))
        steps = ir.raw["cases"][0]["steps"]
        self.assertEqual([step["action"] for step in steps], ["navigate", "assert_hidden", "assert_hidden"])
        self.assertEqual(steps[1]["locator"], {"strategy": "role", "role": "link", "value": "Open receipt", "exact": True})
        self.assertEqual(steps[2]["locator"], {"strategy": "text", "value": 'Receipt "saved"', "exact": True})
        generated = generate_playwright_spec(ir).contents
        self.assertIn('page.getByRole("link", { name: "Open receipt", exact: true, includeHidden: true }).filter({ visible: true })).toHaveCount(0)', generated)
        self.assertIn(r'page.getByText("Receipt \"saved\"", { exact: true }).filter({ visible: true })).toHaveCount(0)', generated)

    def test_exact_checkbox_states_preserve_the_native_action_and_expectations(self) -> None:
        url = "https://example.test/preferences"
        case = TestCase(
            id="TC-PREF",
            title="Persist the receipt preference",
            automation_status="To Be Automated",
            steps=[
                TestStep(step=1, action=f'Open "{url}"', expected=""),
                TestStep(step=2, action=r'Check exact checkbox "Email \"receipt\""', expected=r'exact checkbox "Email \"receipt\"" is checked'),
                TestStep(step=3, action=r'Uncheck exact checkbox "Email \"receipt\""', expected=r'exact checkbox "Email \"receipt\"" should not be checked'),
                TestStep(step=4, action="Inspect the saved choice", expected='css "#email" should not be checked'),
            ],
        )
        preview = preview_execution([case], target_base_url=url)
        self.assertEqual(preview.summary.executable, 1)
        ir = compile_parsed_spec(parse_spec_document(preview.executable[0].spec), environment=ResolvedEnvironment("web", {"baseUrl": url}, url, None))
        steps = ir.raw["cases"][0]["steps"]
        self.assertEqual([step["action"] for step in steps], ["navigate", "set_checked", "assert_checked", "set_checked", "assert_checked", "assert_checked"])
        self.assertEqual([steps[1]["value"], steps[2]["expected"], steps[3]["value"], steps[4]["expected"]], [True, True, False, False])
        self.assertEqual(steps[1]["locator"], {"strategy": "label", "value": 'Email "receipt"', "exact": True})
        generated = generate_playwright_spec(ir).contents
        self.assertIn("checkbox.setChecked(true)", generated)
        self.assertIn("checkbox.setChecked(false)", generated)
        self.assertIn('input[type="checkbox"]', generated)
        self.assertIn('input[type="checkbox"]:not(:indeterminate)', generated)
        self.assertIn("toBeChecked({ checked: false })", generated)
        self.assertIn('page.locator("#email")).not.toBeChecked()', generated)

        for malformed in (
            'Check exact checkbox "Email receipt" and save preferences',
            'Uncheck exact checkbox ""',
            'Uncheck exact radio "Open preferences"',
            'exact checkbox "Open preferences" is not checked after saving',
        ):
            with self.subTest(action=malformed):
                case.steps[1].action = malformed
                invalid = preview_execution([case], target_base_url=url)
                self.assertEqual(invalid.summary.executable, 0)
                self.assertEqual(invalid.unsupported[0].unsupported_steps[0].action, malformed)

    def test_exact_dropdown_selection_compiles_as_a_native_option_action(self) -> None:
        url = "https://example.test/orders"
        case = TestCase(
            id="TC-ORDER",
            title="Filter orders",
            automation_status="To Be Automated",
            steps=[
                TestStep(step=1, action=f'Open "{url}"', expected=""),
                TestStep(step=2, action=r'Select exact option "Closed \"today\"" in field "Order status"', expected=""),
                TestStep(step=3, action='Click exact button "Apply filters"', expected='exact text "Closed order 0142" is visible'),
            ],
        )
        preview = preview_execution([case], target_base_url=url)
        self.assertEqual(preview.summary.executable, 1)
        ir = compile_parsed_spec(parse_spec_document(preview.executable[0].spec), environment=ResolvedEnvironment("web", {"baseUrl": url}, url, None))
        step = ir.raw["cases"][0]["steps"][1]
        self.assertEqual(step["action"], "select_option")
        self.assertEqual(step["value"], 'Closed "today"')
        self.assertEqual(step["locator"], {"strategy": "label", "value": "Order status", "exact": True})
        generated = generate_playwright_spec(ir).contents
        self.assertIn('getByLabel("Order status", { exact: true }).and(page.locator("select:not([multiple])"))', generated)
        self.assertIn(r'select.selectOption({ label: "Closed \"today\"" })', generated)
        self.assertIn("toEqual([true])", generated)
        self.assertIn('getByText("Closed order 0142", { exact: true })).toBeVisible()', generated)

        for malformed in (
            'Select exact option "Closed" in field "Order status" and click Apply filters',
            'Select exact option "Closed"',
            'Select exact option "" in field "Order status"',
        ):
            with self.subTest(action=malformed):
                case.steps[1].action = malformed
                invalid = preview_execution([case], target_base_url=url)
                self.assertEqual(invalid.summary.executable, 0)
                self.assertEqual(invalid.unsupported[0].unsupported_steps[0].action, malformed)

    def test_negative_visibility_rejects_incomplete_and_different_expectations(self) -> None:
        for expectation in (
            'exact link "Open receipt" is not visible and the audit log is unchanged',
            'exact link "Open receipt" is not visible before saving',
            'exact link "Open receipt" is not present in the DOM',
            'link "Open receipt" is not visible',
            'exact text "href" is not visible',
        ):
            with self.subTest(expectation=expectation):
                for action, expected in (("Inspect the receipt", expectation), (expectation, "")):
                    case = _browser_case()
                    case.steps[2] = TestStep(step=3, action=action, expected=expected)
                    preview = preview_execution([case], target_base_url="https://example.test")
                    self.assertEqual(preview.summary.executable, 0)
                    self.assertEqual(preview.unsupported[0].unsupported_steps[0].expected, expected)

    def test_malformed_exact_click_does_not_fall_back_to_partial_action(self) -> None:
        for action in (
            'Click exact button "Sign in" and confirm the user has access',
            'Click exact button "Sign in" before submitting another request',
            'Click exact button "Sign in" is visible',
            'Click exact checkbox "Sign in"',
            'Click exact button ""',
        ):
            with self.subTest(action=action):
                case = _browser_case()
                case.steps[2].action = action
                preview = preview_execution([case], target_base_url="https://example.test")
                self.assertEqual(preview.summary.executable, 0)
                self.assertEqual(preview.unsupported[0].unsupported_steps[0].action, action)

    def test_preview_compiler_and_generator_support_explicit_browser_assertion_dsl(self) -> None:
        semantic_case = TestCase(
            id="TC-SEMANTIC-DSL",
            title="Assert supported browser semantics",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test/settings", expected='page title should be "Settings"'),
                TestStep(step=2, action='Verify css "#subscribe-checkbox" should be checked', expected=""),
                TestStep(step=3, action='Verify css "#next-link" should be disabled', expected=""),
                TestStep(step=4, action='Verify css "a.docs" attribute "href" should equal "/docs"', expected=""),
                TestStep(step=5, action='Verify css ".result" count should be 3', expected=""),
                TestStep(step=6, action='Verify test id "email-input" should be visible', expected=""),
                TestStep(step=7, action='Verify link "Docs" is visible', expected=""),
            ],
        )

        response = preview_execution([semantic_case], target_base_url="https://example.test")

        self.assertEqual(response.summary.executable, 1)
        candidate = response.executable[0]
        self.assertIn('Then page title should be "Settings"', candidate.spec["steps"])
        self.assertIn('And css "#subscribe-checkbox" should be checked', candidate.spec["steps"])
        self.assertIn('And css "#next-link" should be disabled', candidate.spec["steps"])
        self.assertIn('And css "a.docs" attribute "href" should equal "/docs"', candidate.spec["steps"])
        self.assertIn('And css ".result" count should be 3', candidate.spec["steps"])
        self.assertIn('And test id "email-input" should be visible', candidate.spec["steps"])
        self.assertIn('And link "Docs" should be visible', candidate.spec["steps"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "semantic_dsl.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(yaml.safe_dump(candidate.spec, sort_keys=False), encoding="utf-8")
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://example.test"}), encoding="utf-8")
            ir = compile_spec_file(spec_path, environment_path=env_path, environment_name="web")

        actions = [step["action"] for step in ir.raw["cases"][0]["steps"]]
        self.assertIn("assert_title", actions)
        self.assertIn("assert_checked", actions)
        self.assertIn("assert_enabled", actions)
        self.assertIn("assert_attribute_equals", actions)
        self.assertIn("assert_count", actions)

        generated = generate_playwright_spec(ir).contents
        self.assertIn('await expect(page).toHaveTitle("Settings");', generated)
        self.assertIn('await expect(page.locator("#subscribe-checkbox")).toBeChecked();', generated)
        self.assertIn('await expect(page.locator("#next-link")).toBeDisabled();', generated)
        self.assertIn('await expect(page.locator("a.docs")).toHaveAttribute("href", "/docs");', generated)
        self.assertIn('await expect(page.locator(".result")).toHaveCount(3);', generated)
        self.assertIn('await expect(page.getByTestId("email-input")).toBeVisible();', generated)
        self.assertIn('await expect(page.getByRole("link", { name: "Docs" })).toBeVisible();', generated)

    def test_field_value_assertions_survive_preview_compiler_and_codegen(self) -> None:
        case = TestCase(
            id="TC-FIELD-VALUE",
            title="Check an editable value and ordinary text",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test/directory", expected='"Summary" should equal "Ready"'),
                TestStep(
                    step=2,
                    action='Enter "Alex Rivera" into the "Employee name" field',
                    expected='Verify that "Employee name" should equal "Alex Rivera".',
                ),
                TestStep(step=3, action='Verify the "Employee name" field should equal "Alex Rivera"', expected=""),
                TestStep(step=4, action='Verify test id "empty-input" value should equal ""', expected=""),
                TestStep(step=5, action=r'Verify css "#backup-notes" value should equal "  Keep  spacing and \"quotes\"  "', expected=""),
                TestStep(step=6, action='Enter "Taylor" into the "Employee name" field', expected='"Summary" should equal "Ready"'),
                TestStep(
                    step=7,
                    action='Enter "A.+ surname" into the "Employee name" field.',
                    expected='The "Employee name" field contains "A.+".',
                ),
            ],
        )

        response = preview_execution([case], target_base_url="https://example.test")
        self.assertEqual(response.summary.executable, 1)
        spec = response.executable[0].spec
        self.assertEqual(
            spec["steps"],
            [
                'Given I open "https://example.test/directory"',
                'Then "Summary" should equal "Ready"',
                'When I enter "Alex Rivera" into field "Employee name"',
                'And field "Employee name" value should equal "Alex Rivera"',
                'And field "Employee name" value should equal "Alex Rivera"',
                'And test id "empty-input" value should equal ""',
                r'And css "#backup-notes" value should equal "  Keep  spacing and \"quotes\"  "',
                'And I enter "Taylor" into "Employee name"',
                'And "Summary" should equal "Ready"',
                'And I enter "A.+ surname" into field "Employee name"',
                'And field "Employee name" value should contain "A.+"',
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spec_path = root / "field_value.yaml"
            env_path = root / "environment.yaml"
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            env_path.write_text(yaml.safe_dump({"baseUrl": "https://example.test"}), encoding="utf-8")
            ir = compile_spec_file(spec_path, environment_path=env_path, environment_name="web")

        steps = ir.raw["cases"][0]["steps"]
        value_steps = [step for step in steps if step["action"] == "assert_value_equals"]
        self.assertEqual(len(value_steps), 4)
        self.assertEqual(value_steps[-1]["expected"], '  Keep  spacing and "quotes"  ')
        generated = generate_playwright_spec(ir).contents
        self.assertEqual(generated.count('await expect(page.getByLabel("Summary")).toHaveText("Ready");'), 2)
        self.assertIn('await page.getByLabel("Employee name", { exact: true }).fill("Alex Rivera");', generated)
        self.assertIn('await page.getByLabel("Employee name").fill("Taylor");', generated)
        self.assertIn('await expect(page.getByLabel("Employee name", { exact: true })).toHaveValue("Alex Rivera");', generated)
        self.assertIn('await expect(page.getByTestId("empty-input")).toHaveValue("");', generated)
        self.assertIn(r'await expect(page.locator("#backup-notes")).toHaveValue("  Keep  spacing and \"quotes\"  ");', generated)
        self.assertIn(r'await expect(page.getByLabel("Employee name", { exact: true })).toHaveValue(new RegExp("A\\.\\+"));', generated)

        self.assertEqual(steps[-1]["action"], "assert_value_contains")
        steps[-1]["expected"] = ""
        self.assertTrue(validate_ir_document(ir.raw))
        steps[-1]["expected"] = "A.+"
        value_steps[-1].pop("expected")
        self.assertTrue(validate_ir_document(ir.raw))

    def test_unrepresentable_field_value_assertion_blocks_entire_candidate(self) -> None:
        case = TestCase(
            id="TC-FIELD-VALUE-UNSUPPORTED",
            title="Cannot verify editable value with an unsupported operator",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test/directory", expected='heading "Directory" is visible'),
                TestStep(
                    step=2,
                    action='Enter "Alex Rivera" into the "Employee name" field',
                    expected='Verify field "Employee name" value starts with "Alex"',
                ),
            ],
        )

        for statement in (
            'Verify field "Employee name" value starts with "Alex"',
            'The Employee name field should contain "Alex"',
            'Verify field "Employee name" value should contain ""',
        ):
            with self.subTest(statement=statement):
                case.steps[1].expected = statement
                response = preview_execution([case], target_base_url="https://example.test")

                self.assertEqual(response.summary.executable, 0)
                self.assertEqual(response.summary.unsupported, 1)
                candidate = response.unsupported[0]
                self.assertIsNone(candidate.spec)
                self.assertEqual(candidate.unsupported_steps[0].reason_code, "unsupported_field_value_assertion")
                self.assertIn('field "Employee name" value should equal "Alex"', candidate.unsupported_steps[0].suggested_next_action)

    def test_preview_marks_runtime_and_title_visible_assertions_unsupported(self) -> None:
        unsupported_case = TestCase(
            id="TC-UNSUPPORTED-BROWSER-ASSERTIONS",
            title="Do not convert runtime or title mismatch text into page text",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test", expected='"Welcome" is visible'),
                TestStep(step=2, action='Verify "ScopeMismatch: locator failed" is visible in stderr', expected=""),
                TestStep(step=3, action='"Incorrect Page Title" should be visible', expected=""),
            ],
        )

        response = preview_execution([unsupported_case], target_base_url="https://example.test")

        self.assertEqual(response.summary.executable, 0)
        candidate = response.unsupported[0]
        self.assertEqual(
            [step.reason_code for step in candidate.unsupported_steps],
            ["unsupported_browser_assertion", "unsupported_browser_assertion"],
        )
        self.assertEqual([step.step for step in candidate.unsupported_steps], [2, 3])
        self.assertIsNone(candidate.spec)

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
        self.assertIn("exact accessible name", candidate.unsupported_steps[0].suggested_next_action)

    def test_preview_blocks_cases_with_ambiguous_semantic_assertions(self) -> None:
        mixed_case = TestCase(
            id="TC-MIXED-SEMANTIC",
            title="An exact check cannot cover a role-only assertion",
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

        self.assertEqual(response.summary.executable, 0)
        candidate = response.unsupported[0]
        self.assertIn("ambiguous_semantic_assertion", {step.reason_code for step in candidate.unsupported_steps})
        self.assertEqual(candidate.unsupported_steps[0].action, mixed_case.steps[1].action)
        self.assertIsNone(candidate.spec)

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

        self.assertEqual(response.summary.executable, 0)
        blocked = response.unsupported[0]
        self.assertIsNone(blocked.spec)
        self.assertEqual(blocked.unsupported_steps[0].reason_code, "unsupported_browser_assertion")
        self.assertEqual(blocked.unsupported_steps[0].expected, command_case.steps[1].expected)

    def test_preview_marks_manual_cases_as_manual_by_default(self) -> None:
        manual_case = _browser_case("TC-MANUAL")
        manual_case.automation_status = "Manual"

        response = preview_execution([manual_case])

        self.assertEqual(response.summary.manual, 1)
        self.assertEqual(response.manual[0].status, "manual")
        self.assertIn("Automation Status is Manual", response.manual[0].review_reasons[0])

    def test_required_expectation_cannot_be_omitted_after_convertible_action(self) -> None:
        case = TestCase(
            id="TC-AUDIT",
            title="Verify the audit record",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test", expected='"Summary" should equal "Ready"'),
                TestStep(step=2, action='Click the "Submit" button', expected=""),
            ],
        )
        for expectation in (
            "An immutable audit record is created in the database for this submission.",
            'heading "Submitted" is visible and an immutable audit record is created.',
            'heading "Submitted" is visible with exact text "Submitted".',
            'The "Payment declined" message is not visible.',
            'URL should be "https://example.test/confirmation" and must not redirect.',
            '"Submitted" and "Receipt sent" are visible.',
        ):
            with self.subTest(expectation=expectation):
                case.steps[-1].expected = expectation
                response = preview_execution([case], target_base_url="https://example.test")

                self.assertEqual(response.summary.executable, 0)
                self.assertEqual(response.summary.unsupported, 1)
                blocked = response.unsupported[0]
                self.assertIsNone(blocked.spec)
                self.assertEqual(len(blocked.unsupported_steps), 1)
                gap = blocked.unsupported_steps[0]
                self.assertEqual(gap.step, 2)
                self.assertEqual(gap.expected, expectation)
                self.assertEqual(gap.reason_code, "unsupported_expected_result")

    def test_unmapped_assertion_in_action_cannot_turn_into_navigation_or_click(self) -> None:
        case = _browser_case()
        for action in (
            'Verify URL should be "https://example.test/confirmation" and must not redirect.',
            'Click the "Submit" button and verify the audit record.',
        ):
            with self.subTest(action=action):
                case.steps[-1] = TestStep(step=3, action=action, expected="")
                response = preview_execution([case], target_base_url="https://example.test")

                self.assertEqual(response.summary.executable, 0)
                self.assertEqual(response.unsupported[0].unsupported_steps[0].action, action)
                self.assertIsNone(response.unsupported[0].spec)

    def test_valid_action_only_steps_and_whole_assertions_remain_executable(self) -> None:
        case = TestCase(
            id="TC-SUBMIT",
            title="Verify submission is shown in the browser",
            automation_status="Automated",
            steps=[
                TestStep(step=1, action="Open https://example.test", expected=" "),
                TestStep(step=2, action='Click the "Submit" button', expected=""),
                TestStep(step=3, action="Inspect the status output", expected='"Summary" should equal "Submitted"'),
            ],
        )
        response = preview_execution([case], target_base_url="https://example.test")

        self.assertEqual(response.summary.executable, 1)
        candidate = response.executable[0]
        self.assertFalse(candidate.unsupported_steps)
        self.assertEqual(candidate.spec["steps"], ['Given I open "https://example.test"', 'When I click "Submit"', 'Then "Summary" should equal "Submitted"'])

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

    def test_run_with_only_unmapped_expectations_cannot_report_passed(self) -> None:
        case = _browser_case()
        case.steps[-1].expected = "The login is recorded in the audit database."
        with tempfile.TemporaryDirectory() as temp_dir, patch("app.services.execution_service.run_local_playwright_specs") as runner:
            response = run_execution([case], selected_test_case_ids=[case.id], settings=_settings(Path(temp_dir)))

        runner.assert_not_called()
        self.assertEqual(response.status, "failed")
        self.assertEqual(response.summary.passed, 0)
        self.assertEqual(response.summary.skipped, 1)
        self.assertEqual(response.results[0].issues[0].code, "execution.unsupported")

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
