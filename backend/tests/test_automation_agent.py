from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents.automation_agent import generate_playwright_pom, _build_pom_prompt
from app.config import GenerationSettings
from app.models import AutomationInput
from app.services.automation_validation import validate_artifacts, test_function_name


def payload_fixture(count=1, *, evidence=True):
    cases = [
        {
            "id": f"TC-{index:03d}",
            "title": f"Observed page {index}",
            "component": f"Page {index}",
            "automation_status": "To Be Automated",
            "steps": [{"step": 1, "action": "Open supplied target", "expected": "The page title is exactly Execution QA."}],
        }
        for index in range(1, count + 1)
    ]
    return AutomationInput(
        test_cases=cases,
        target_base_url="https://qa.example.test/",
        interface_evidence=[
            {
                "test_case_id": case["id"],
                "source_reference": "Synthetic page title observed by fixture owner",
                "assertions": [{"step": 1, "method": "to_have_title", "expected": "Execution QA"}],
            }
            for case in cases
        ]
        if evidence
        else [],
    )


def code_for(cases):
    return "from playwright.sync_api import Page, expect\n\n" + "\n".join(
        f'def {test_function_name(case.id)}(page: Page):\n    page.goto("https://qa.example.test/")\n    expect(page).to_have_title("Execution QA")\n'
        for case in cases
    )


def model_response(code):
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=code)]))], usage_metadata=None)


class AutomationAgentTests(unittest.TestCase):
    def model_patches(self, source, settings=None):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch("app.agents.automation_agent.get_settings", return_value=SimpleNamespace(model_name="test-model", gemini_api_key="test-key")))
        stack.enter_context(patch("app.agents.automation_agent.get_generation_settings", return_value=settings or GenerationSettings()))
        client = stack.enter_context(patch("app.agents.automation_agent.genai.Client"))
        client.return_value.models.generate_content.side_effect = lambda **kwargs: model_response(source(kwargs["contents"]) if callable(source) else source)
        return stack, client

    def test_missing_evidence_and_manual_cases_make_no_model_calls_or_executable_smoke(self):
        for count in (1, 35):
            payload = payload_fixture(count, evidence=False)
            with patch("app.agents.automation_agent._get_model_settings_or_none") as model:
                response = generate_playwright_pom(payload)
            model.assert_not_called()
            self.assertEqual(response.status, "skipped")
            self.assertEqual(response.files, [])
            self.assertEqual(response.diagnostics["generated_case_count"], 0)
            self.assertEqual(response.diagnostics["manual_case_count"], count)
            self.assertEqual(response.diagnostics["represented_test_case_count"], count)
            self.assertEqual(response.diagnostics["execution_status"], "not_executed")
            self.assertEqual(response.case_diagnostics[0].source_expected_results, [payload.test_cases[0].steps[0].expected])

    def test_target_and_assertion_mapping_are_required(self):
        for variant in ("target", "assertion", "manual"):
            payload = payload_fixture()
            if variant == "target":
                payload.target_base_url = None
            if variant == "assertion":
                payload.interface_evidence[0].assertions[0].step = 2
            if variant == "manual":
                payload.test_cases[0].automation_status = "Manual"
            with patch("app.agents.automation_agent.genai.Client") as model:
                response = generate_playwright_pom(payload)
            model.assert_not_called()
            self.assertEqual(response.diagnostics["generated_case_count"], 0)

    def test_missing_credentials_does_not_manufacture_business_coverage(self):
        payload = payload_fixture(35)
        with patch("app.agents.automation_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            response = generate_playwright_pom(payload)
        self.assertEqual(response.status, "skipped")
        self.assertEqual(response.files, [])
        self.assertEqual(response.diagnostics["unsupported_case_count"], 35)
        self.assertEqual(response.diagnostics["generated_case_count"], 0)

    def test_small_valid_artifact_preserves_full_expectations_and_counts(self):
        payload = payload_fixture()
        payload.test_cases[0].expected_result = payload.test_cases[0].steps[0].expected
        source = "# === FILE: tests/test_cases.py ===\n" + code_for(payload.test_cases)
        stack, client = self.model_patches(source)
        with stack:
            response = generate_playwright_pom(payload)
        self.assertEqual(response.status, "generated")
        self.assertEqual(response.diagnostics["artifact_validation"], "passed")
        self.assertEqual(response.diagnostics["generated_case_count"], 1)
        self.assertEqual(response.case_diagnostics[0].source_expected_results[-1], payload.test_cases[0].expected_result)
        self.assertIn(payload.test_cases[0].expected_result, client.return_value.models.generate_content.call_args.kwargs["contents"])
        self.assertEqual(response.diagnostics["execution_status"], "not_executed")

    def test_invalid_small_artifacts_fail_explicitly_without_second_model_call(self):
        payload = payload_fixture()
        valid = code_for(payload.test_cases)
        invalid = [
            "def test_tc_001(",
            "# Empty file",
            valid.replace('to_have_title("Execution QA")', 'to_have_title("Invented")'),
            valid.replace('expect(page).to_have_title("Execution QA")', "assert page is not None"),
            valid.replace("https://qa.example.test/", "https://invented.test/"),
            valid.replace("    expect(page)", "    return\n    expect(page)"),
            valid + '\nignore = {"ignore_https_errors": True}\n',
            valid + "\nimport time\ntime.sleep(5)\n",
            valid.replace("    page.goto", '    page.locator("#invented").click()\n    page.goto'),
            valid.replace("    page.goto", '    page.get_by_label("Password").fill("invented-secret")\n    page.goto'),
            valid.replace('    expect(page).to_have_title("Execution QA")', "    try:\n        assert False\n    except Exception:\n        pass"),
            valid.replace("def test_tc_001", "@pytest.mark.skip\ndef test_tc_001"),
        ]
        for source in invalid:
            with self.subTest(source=source[:30]):
                stack, client = self.model_patches("# === FILE: tests/test_cases.py ===\n" + source)
                with stack:
                    response = generate_playwright_pom(payload)
                self.assertEqual(response.status, "skipped")
                self.assertEqual(response.files, [])
                self.assertEqual(response.diagnostics["generated_case_count"], 0)
                self.assertEqual(response.diagnostics["artifact_validation"], "failed")
                client.return_value.models.generate_content.assert_called_once()

    def test_parallel_validation_keeps_valid_sibling_and_truthful_case_diagnostics(self):
        payload = payload_fixture(3)
        settings = GenerationSettings(parallel_automation_min_cases=1, parallel_automation_max_workers=3)

        def source(prompt):
            identity = next(case.id for case in payload.test_cases if f'"id": "{case.id}"' in prompt)
            case = next(case for case in payload.test_cases if case.id == identity)
            code = "def broken(" if identity == "TC-002" else code_for([case])
            return json.dumps(
                {
                    "files": [{"path": "test_duplicate.py", "content": code}],
                    "case_diagnostics": [{"test_case_id": identity, "status": "manual", "reason": "Incorrect model self-label"}],
                }
            )

        stack, client = self.model_patches(source, settings)
        with stack:
            response = generate_playwright_pom(payload)
        self.assertEqual(response.status, "partial")
        self.assertEqual(response.diagnostics["generated_case_count"], 2)
        self.assertEqual(response.diagnostics["unsupported_case_count"], 1)
        self.assertEqual(response.diagnostics["failed_shard_count"], 1)
        self.assertEqual([item.status for item in response.case_diagnostics], ["generated", "unsupported", "generated"])
        self.assertEqual(len(set(response.files)), len(response.files))
        self.assertNotIn("def broken", response.notes)

    def test_evidenced_locator_and_input_are_accepted_only_with_matching_assertion(self):
        payload = payload_fixture()
        from app.contracts.automation import AutomationCaseEvidence

        payload.interface_evidence = [
            AutomationCaseEvidence.model_validate(
                {
                    "test_case_id": "TC-001",
                    "source_reference": "Synthetic DOM",
                    "locators": [{"method": "get_by_label", "value": "Code"}],
                    "input_values": ["supplied-value"],
                    "assertions": [{"step": 1, "method": "to_have_text", "expected": "Saved", "locator": {"method": "get_by_role", "value": "status"}}],
                }
            )
        ]
        code = 'from playwright.sync_api import Page, expect\ndef test_tc_001(page: Page):\n    page.goto("https://qa.example.test/")\n    page.get_by_label("Code").fill("supplied-value")\n    expect(page.get_by_role("status")).to_have_text("Saved")\n'
        self.assertEqual(validate_artifacts({"test_cases.py": code}, payload), [])
        self.assertTrue(validate_artifacts({"test_cases.py": code.replace("supplied-value", "invented-value")}, payload))
        self.assertTrue(validate_artifacts({"test_cases.py": code.replace('to_have_text("Saved")', "to_be_visible()")}, payload))
        self.assertTrue(validate_artifacts({"test_cases.py": code.replace('get_by_role("status")', 'get_by_text("Saved")')}, payload))
        self.assertTrue(validate_artifacts({"../test.py": code}, payload))

    def test_overall_result_and_colliding_function_ids_cannot_be_silently_dropped(self):
        payload = payload_fixture()
        payload.test_cases[0].expected_result = "A different final outcome."
        with patch("app.agents.automation_agent.genai.Client") as model:
            response = generate_playwright_pom(payload)
        model.assert_not_called()
        self.assertEqual(response.case_diagnostics[0].status, "manual")
        payload = payload_fixture(2)
        payload.test_cases[1].id = "TC_001"
        with patch("app.agents.automation_agent.genai.Client") as model:
            response = generate_playwright_pom(payload)
        model.assert_not_called()
        self.assertEqual(response.diagnostics["generated_case_count"], 0)

    def test_prompts_do_not_truncate_source_steps_or_expectations(self):
        payload = payload_fixture()
        from app.models import TestStep

        payload.test_cases[0].steps = [
            TestStep(step=index, action=f"Action {index}", expected=f"Expected {index}", test_data=f"Value {index}") for index in range(1, 13)
        ]
        prompt = _build_pom_prompt(payload)
        self.assertIn("Expected 12", prompt)
        self.assertIn("Value 12", prompt)
        self.assertNotIn("https://example.com", prompt)
