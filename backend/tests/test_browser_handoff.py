"""Keep generated browser intent tied to evidence and the actual preview."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.test_case_agent import _build_response, _build_test_case_generator_agent, _build_review_loop
from app.contracts.grounding import EnrichInput
from app.contracts.requirements import Requirement
from app.contracts.test_cases import TestCase, TestStep
from app.services.browser_handoff import BROWSER_AUTHORING_GUIDE, validate_browser_handoff
from app.services.execution_service import preview_execution


TARGET = "https://example.test/receipt"
REQUIREMENT = Requirement(id="R1", text='Saving a valid reference displays "Request saved".')


def context():
    return EnrichInput.model_validate(
        {
            "app_link": TARGET,
            "requirements": [REQUIREMENT],
            "grounded_context": {
                "artifact_sources": [
                    {"id": "APP", "source_type": "app", "url": TARGET, "label": "Receipt", "status": "Analyzed"},
                    {"id": "MOCK", "source_type": "prototype", "url": "https://design.test", "label": "Mock", "status": "Analyzed"},
                ],
                "ui_elements": [
                    {"id": "FIELD", "name": "Reference", "element_type": "Field", "description": "Observed label", "source_id": "APP"},
                    {"id": "ID", "name": "ref", "element_type": "Field", "description": "Field identifier extracted from artifact: ref", "source_id": "APP"},
                    {"id": "SAVE", "name": "Save request", "element_type": "Button", "description": "Observed button", "source_id": "APP"},
                    {"id": "MOCK-BTN", "name": "Submit mock", "element_type": "Button", "description": "Prototype button", "source_id": "MOCK"},
                ],
            },
        }
    )


def candidate():
    return TestCase(
        id="TC-SAVE",
        title="Save a valid request",
        description="Confirm the request was saved after supplying a valid reference.",
        automation_status="Automated",
        linked_requirement_ids=["R1"],
        steps=[
            TestStep(step=1, action=f'Open "{TARGET}"', expected=""),
            TestStep(step=2, action='Enter "Q-17" into the "Reference" field', expected='field "Reference" value should equal "Q-17"'),
            TestStep(step=3, action='Click the "Save request" button', expected='exact text "Request saved" is visible'),
        ],
    )


class BrowserHandoffTests(unittest.TestCase):
    def test_compiled_steps_preserve_controlled_input_and_exact_copy_without_execution(self):
        case = candidate()
        original = case.model_copy(deep=True)
        with patch("app.services.execution_service.run_execution") as run:
            self.assertEqual(validate_browser_handoff([case], context(), [REQUIREMENT]), [])
        run.assert_not_called()
        self.assertEqual(case, original.model_copy(update={"automation_status": "To Be Automated"}))
        self.assertEqual(preview_execution([case], target_base_url=TARGET).summary.executable, 1)

    def test_blank_initial_value_is_checked_without_an_empty_fill(self):
        case = candidate()
        case.steps[1] = TestStep(step=2, action='Inspect the "Reference" field', expected='field "Reference" value should equal ""')
        case.steps[2].expected = 'exact text "Reference is required" is visible'
        requirement = Requirement(id="R1", text='Submitting the initial blank value displays "Reference is required".')
        self.assertEqual(validate_browser_handoff([case], context(), [requirement]), [])
        self.assertEqual(case.automation_status, "To Be Automated")

    def test_unsupported_expectations_and_unverified_evidence_retain_original_case(self):
        for index, changes, reason in (
            (0, {"action": 'Open "https://example.test/invented"'}, "supplied application URL"),
            (1, {"action": 'Enter "Q-17" into the "ref" field'}, "exact interface evidence"),
            (1, {"action": f'Open "{TARGET}" as an auditor'}, "each browser navigation"),
            (2, {"action": 'Click the "Submit mock" button'}, "exact interface evidence"),
            (2, {"expected": 'exact text "Invented copy" is visible'}, "exact interface evidence"),
            (2, {"expected": 'exact text "Q-17" is visible'}, "exact interface evidence"),
            (2, {"expected": 'exact text "Request saved" is visible and the record is archived'}, "Source step 3"),
            (2, {"expected": 'exact text "{data.missing}" is visible'}, "compiled into a valid browser specification"),
        ):
            with self.subTest(changes=changes):
                case = candidate()
                case.steps[index] = case.steps[index].model_copy(update=changes)
                original = case.model_copy(deep=True)
                warnings = validate_browser_handoff([case], context(), [REQUIREMENT])
                self.assertEqual(case, original.model_copy(update={"automation_status": "Manual"}))
                self.assertIn(reason.lower(), warnings[1].lower())
                self.assertNotIn("Invented copy", " ".join(warnings))

    def test_explicit_notes_supply_exact_ui_evidence_but_require_a_target(self):
        source = EnrichInput(
            requirements=[REQUIREMENT],
            app_link=TARGET,
            notes='The field "Reference" and the "Save request" button are observed controls.',
        )
        case = candidate()
        self.assertEqual(validate_browser_handoff([case], source, [REQUIREMENT]), [])
        warnings = validate_browser_handoff([case], source.model_copy(update={"app_link": None}), [REQUIREMENT])
        self.assertIn("No supplied application target", warnings[1])
        self.assertEqual(case.automation_status, "Manual")

    def test_manual_external_case_is_unchanged_and_duplicate_case_id_is_blocked(self):
        browser = candidate()
        external = TestCase(
            id=browser.id,
            title="Verify the immutable audit entry",
            steps=[TestStep(step=1, action="Open the audit store as the authorized auditor.", expected="The audit entry is immutable.")],
            linked_requirement_ids=["R2"],
        )
        original = external.model_copy(deep=True)
        warnings = validate_browser_handoff([browser, external], context(), [REQUIREMENT])
        self.assertIn("ambiguous", warnings[1])
        self.assertEqual(browser.automation_status, "Manual")
        self.assertEqual(external, original)

    def test_final_delivery_preserves_business_review_and_reports_manual_handoff(self):
        case = candidate()
        case.steps[2].expected = "The operator sees that the request has been saved."
        workflow = {"approved": True, "review": {"approved": True}, "workflow_diagnostics": {"warnings": ["Existing warning"]}}
        with patch("app.agents.test_case_agent.separate_generation_work", return_value=([case], [])):
            result = _build_response([case], workflow, [REQUIREMENT], context())
        self.assertTrue(result["approved"])
        self.assertEqual(result["test_cases"][0].automation_status, "Manual")
        self.assertEqual(result["test_cases"][0].steps[2].expected, "The operator sees that the request has been saved.")
        warnings = result["workflow_diagnostics"]["warnings"]
        self.assertEqual(warnings[0], "Existing warning")
        self.assertTrue(any("Browser handoff for TC-SAVE" in warning for warning in warnings))

    def test_authoring_grammar_is_shared_by_generation_and_review(self):
        generator = _build_test_case_generator_agent(model="test-model", requirements_text="", context_text="", template_text="")
        review = _build_review_loop(model="test-model", threshold=90, max_iterations=2, requirements_text="")
        self.assertIn(BROWSER_AUTHORING_GUIDE, generator.instruction)
        for agent in review.sub_agents[:2]:
            self.assertIn(BROWSER_AUTHORING_GUIDE, agent.instruction)
