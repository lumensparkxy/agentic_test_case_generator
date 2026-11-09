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
            TestStep(step=3, action='Click exact button "Save request"', expected='exact text "Request saved" is visible'),
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

    def test_count_requires_the_exact_declared_selector(self):
        selector = """css '[data-order-id="  café  "]'"""
        grounded = context().model_copy(update={"notes": f"Observed {selector}."})
        case = candidate()
        case.steps = [
            TestStep(step=1, action=f'Open "{TARGET}"', expected=""),
            TestStep(step=2, action="Inspect saved orders", expected=f"{selector} count should be 1"),
        ]
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])
        self.assertEqual(case.automation_status, "To Be Automated")

        invented = case.model_copy(deep=True)
        invented.steps[1].expected = invented.steps[1].expected.replace("  café  ", "café")
        warnings = validate_browser_handoff([invented], grounded, [REQUIREMENT])
        self.assertEqual(invented.automation_status, "Manual")
        self.assertTrue(any("lacks exact interface evidence" in warning for warning in warnings))

    def test_populated_field_can_be_cleared_with_grounded_exact_input(self):
        case = candidate()
        case.steps[1].action = 'Enter "  Q  into  17  " into field "Reference"'
        case.steps[1].expected = 'field "Reference" value should equal "  Q  into  17  "'
        case.steps.insert(2, TestStep(step=3, action='Enter "" into field "Reference"', expected='field "Reference" value should equal ""'))
        case.steps[-1].step = 4
        self.assertEqual(validate_browser_handoff([case], context(), [REQUIREMENT]), [])
        self.assertEqual(case.automation_status, "To Be Automated")

        case.steps[2].action = 'Enter "" into field "Unobserved field"'
        case.steps[2].expected = 'field "Unobserved field" value should equal ""'
        self.assertIn("lacks exact interface evidence", validate_browser_handoff([case], context(), [REQUIREMENT])[1])
        self.assertEqual(case.automation_status, "Manual")

    def test_multiline_step_data_keeps_exact_grounded_fill_and_saved_readback(self):
        case = candidate()
        value = '{\n  "template": "{env.example}",\n  "reference": "[[env:PETF_TEST_EXAMPLE]]"\n}'
        case.steps[1] = TestStep(
            step=2, action='Enter step data into field "Reference"', expected='field "Reference" value should equal step data', test_data=value
        )
        case.steps.append(TestStep(step=4, action="Inspect the saved reference", expected='css "#saved-ref" text should equal step data', test_data=value))
        grounded = context()
        grounded.notes = f'Browser result css "#saved-ref" on "{TARGET}" shows field "Reference" after button "Save request"'
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])
        self.assertEqual(case.automation_status, "To Be Automated")

        case.steps[-1].test_data = value.replace("  ", " ")
        self.assertIn("does not match the exact submitted input", validate_browser_handoff([case], grounded, [REQUIREMENT])[1])
        self.assertEqual(case.automation_status, "Manual")

    def test_scoped_text_handoff_requires_grounded_locator_and_exact_value(self):
        for locator in ('css "#result"', 'test id "result"'):
            with self.subTest(locator=locator):
                case = candidate()
                case.steps[2].expected = f'{locator} text should equal "Request saved"'
                ungrounded = case.model_copy(deep=True)
                self.assertIn("lacks exact interface evidence", validate_browser_handoff([ungrounded], context(), [REQUIREMENT])[1])
                self.assertEqual(ungrounded.automation_status, "Manual")

                grounded = context()
                grounded.notes = f"Observed locator {locator}."
                self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])
                self.assertEqual(case.automation_status, "To Be Automated")
                case.steps[2].expected = f'{locator} text should equal "Invented status"'
                self.assertIn("value lacks exact source", validate_browser_handoff([case], grounded, [REQUIREMENT])[1])
                self.assertEqual(case.automation_status, "Manual")

    def test_saved_result_requires_matching_input_submit_and_page(self):
        other_page = "https://example.test/other"
        binding = f'Browser result css "#saved-ref" on "{TARGET}" shows field "Reference" after button "Save request"'
        for change, message in (
            ("none", None),
            ("different value", "does not match the exact submitted input"),
            ("before submit", "lacks exact source or controlled input evidence"),
            ("other button", "lacks exact source or controlled input evidence"),
            ("other field", "lacks exact source or controlled input evidence"),
            ("other page", "lacks exact interface evidence"),
            ("cross origin", "lacks exact interface evidence"),
            ("conflict", "lacks exact interface evidence"),
            ("secret reference", None),
        ):
            with self.subTest(change=change):
                case = candidate()
                grounded = context()
                declaration = binding
                assertion = 'css "#saved-ref" text should equal "Q-17"'
                extra_notes = [f'Browser page "{other_page}"', 'Observed field "Unrelated" and button "Review request".']
                if change == "different value":
                    assertion = 'css "#saved-ref" text should equal "Request saved"'
                elif change == "other button":
                    case.steps[2].action = 'Click exact button "Review request"'
                elif change == "other field":
                    declaration = binding.replace('field "Reference"', 'field "Unrelated"')
                elif change == "other page":
                    case.steps.append(TestStep(step=4, action=f'Open "{other_page}"', expected=""))
                elif change == "cross origin":
                    declaration = binding.replace(TARGET, "https://other.test/receipt")
                elif change == "conflict":
                    extra_notes.append(binding.replace('field "Reference"', 'field "Unrelated"'))
                elif change == "secret reference":
                    marker = "[[env:PETF_TEST_REFERENCE]]"
                    case.steps[1].action = case.steps[1].action.replace("Q-17", marker)
                    case.steps[1].expected = case.steps[1].expected.replace("Q-17", marker)
                    assertion = assertion.replace("Q-17", marker)
                grounded.notes = "\n".join([*extra_notes, declaration])
                observation = TestStep(step=len(case.steps) + 1, action="Inspect the saved reference", expected=assertion)
                if change == "before submit":
                    case.steps.insert(2, observation.model_copy(update={"step": 3}))
                    case.steps[-1].step = 4
                else:
                    case.steps.append(observation)

                warnings = validate_browser_handoff([case], grounded, [REQUIREMENT])
                if change == "none":
                    self.assertEqual(warnings, [])
                    self.assertEqual(case.automation_status, "To Be Automated")
                else:
                    self.assertEqual(case.automation_status, "Manual")
                    if message:
                        self.assertIn(message, warnings[1])

    def test_saved_result_uses_selected_label_and_not_dropdown_dom_value(self):
        grounded = context()
        grounded.notes = (
            f'Browser dropdown "Category" on "{TARGET}" options ["Selected option"]\n'
            f'Browser result test id "saved-category" on "{TARGET}" shows field "Category" after button "Save request"\n'
            'field "Category" DOM value is "option-id"'
        )
        case = candidate()
        case.steps[1] = TestStep(step=2, action='Select exact option "Selected option" in field "Category"', expected="")
        case.steps.append(TestStep(step=4, action="Inspect the saved category", expected='test id "saved-category" text should equal "Selected option"'))
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])
        case.steps[-1].expected = 'test id "saved-category" text should equal "option-id"'
        self.assertIn("does not match the exact submitted input", validate_browser_handoff([case], grounded, [REQUIREMENT])[1])

    def test_checkbox_handoff_requires_native_availability_for_interaction(self):
        grounded = context()
        field = grounded.grounded_context.ui_elements[0]
        field.checkbox_available = True
        case = candidate()
        case.steps[1] = TestStep(step=2, action='Uncheck exact checkbox "Reference"', expected='exact checkbox "Reference" is not checked')
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])

        for availability, reason in ((False, "unavailable for interaction"), (None, "exact native checkbox")):
            with self.subTest(availability=availability):
                field.checkbox_available = availability
                draft = case.model_copy(deep=True)
                self.assertIn(reason, validate_browser_handoff([draft], grounded, [REQUIREMENT])[1])
                self.assertEqual(draft.automation_status, "Manual")

        field.checkbox_available = False
        observed = case.model_copy(deep=True)
        observed.steps[1].action = 'Inspect the "Reference" checkbox'
        self.assertEqual(validate_browser_handoff([observed], grounded, [REQUIREMENT]), [])

    def test_checkbox_text_fill_and_dom_value_check_stay_manual(self):
        grounded = context()
        grounded.grounded_context.ui_elements[0].checkbox_available = True
        for action in ('Enter "Q-17" into the "Reference" field', 'Inspect the "Reference" checkbox'):
            with self.subTest(action=action):
                case = candidate()
                case.steps[1].action = action
                warnings = validate_browser_handoff([case], grounded, [REQUIREMENT])
                self.assertIn("known checkbox needs a checked or unchecked", warnings[1])
                self.assertEqual(case.automation_status, "Manual")

    def test_radio_handoff_requires_unambiguous_available_native_option(self):
        grounded = context()
        field = grounded.grounded_context.ui_elements[0]
        field.radio_available = True
        case = candidate()
        case.steps[1] = TestStep(step=2, action='Select exact radio "Reference"', expected='exact radio "Reference" is selected')
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])

        for availability, reason in ((False, "unavailable for selection"), (None, "unambiguous native radio")):
            with self.subTest(availability=availability):
                field.radio_available = availability
                draft = case.model_copy(deep=True)
                self.assertIn(reason, validate_browser_handoff([draft], grounded, [REQUIREMENT])[1])
                self.assertEqual(draft.automation_status, "Manual")

        field.radio_available = False
        observed = case.model_copy(deep=True)
        observed.steps[1].action = 'Inspect the "Reference" option'
        observed.steps[1].expected = 'exact radio "Reference" is not selected'
        self.assertEqual(validate_browser_handoff([observed], grounded, [REQUIREMENT]), [])
        grounded.grounded_context.ui_elements.append(field.model_copy(update={"id": "DUPLICATE"}))
        self.assertIn("unambiguous native radio", validate_browser_handoff([observed], grounded, [REQUIREMENT])[1])

    def test_dropdown_handoff_requires_the_exact_option_on_its_grounded_page(self):
        grounded = context()
        field = grounded.grounded_context.ui_elements[0]
        field.select_options = ["Q-17"]
        case = candidate()
        case.steps[1] = TestStep(step=2, action='Select exact option "Q-17" in field "Reference"', expected="")
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])
        self.assertEqual(case.automation_status, "To Be Automated")

        fill = candidate()
        warnings = validate_browser_handoff([fill], grounded, [REQUIREMENT])
        self.assertEqual(fill.automation_status, "Manual")
        self.assertIn("known dropdown", warnings[1])

        for options, action in (
            (None, case.steps[1].action),  # Older saved evidence supplies the label only.
            ([], case.steps[1].action),
            (["Q-17"], 'Select exact option "q-17" in field "Reference"'),
        ):
            with self.subTest(options=options, action=action):
                field.select_options = options
                selection = case.model_copy(deep=True)
                selection.steps[1].action = action
                warnings = validate_browser_handoff([selection], grounded, [REQUIREMENT])
                self.assertEqual(selection.automation_status, "Manual")
                self.assertIn("exact selectable option", warnings[1])

        field.select_options = ["Q-17"]
        second_url = "https://example.test/other"
        artifact = grounded.grounded_context.artifact_sources[0]
        grounded.grounded_context.artifact_sources.append(artifact.model_copy(update={"id": "SECOND", "url": second_url}))
        moved = case.model_copy(deep=True)
        moved.steps[0].action = f'Open "{second_url}"'
        warnings = validate_browser_handoff([moved], grounded, [REQUIREMENT])
        self.assertIn("exact selectable option", warnings[1])

    def test_dropdown_label_does_not_ground_a_dom_value_expectation(self):
        grounded = context()
        grounded.grounded_context.ui_elements[0].select_options = ["Q-17"]
        grounded.notes = 'The option label is "Q-17".'
        case = candidate()
        case.steps[1].action = 'Select exact option "Q-17" in field "Reference"'
        warnings = validate_browser_handoff([case], grounded, [REQUIREMENT])
        self.assertEqual(case.automation_status, "Manual")
        self.assertIn("explicitly declared DOM value", warnings[1])

        grounded.notes += ' The field "Reference" DOM value is "q17".'
        case.automation_status = "To Be Automated"
        case.steps[1].expected = 'field "Reference" value should equal "q17"'
        self.assertEqual(validate_browser_handoff([case], grounded, [REQUIREMENT]), [])

    def test_explicit_browser_notes_ground_dropdowns_only_on_the_declared_page(self):
        details = "https://example.test/receipt/details"
        observed = 'The field "Reference" and button "Save request" are observed.'
        source = EnrichInput(app_link=TARGET, requirements=[REQUIREMENT], notes=observed)
        case = candidate()
        case.steps[0].action = f'Open "{details}"'
        case.steps[1].action = 'Select exact option "Q-17" in field "Reference"'
        case.steps[1].expected = ""

        self.assertIn("supplied application URL", validate_browser_handoff([case.model_copy(deep=True)], source, [REQUIREMENT])[1])
        on_target = case.model_copy(deep=True)
        on_target.steps[0].action = f'Open "{TARGET}"'
        self.assertIn("exact selectable option", validate_browser_handoff([on_target.model_copy(deep=True)], source, [REQUIREMENT])[1])

        route = f'Browser page "{details}"'
        dropdown = f'Browser dropdown "Reference" on "{details}" options ["Q-17", "Q-18"]'
        source.notes = "\n".join((observed, route, dropdown))
        self.assertEqual(validate_browser_handoff([case.model_copy(deep=True)], source, [REQUIREMENT]), [])
        self.assertIn("exact selectable option", validate_browser_handoff([on_target], source, [REQUIREMENT])[1])
        wrong_label = case.model_copy(deep=True)
        wrong_label.steps[1].action = 'Select exact option "q-17" in field "Reference"'
        self.assertIn("exact selectable option", validate_browser_handoff([wrong_label], source, [REQUIREMENT])[1])

        for declaration in (
            dropdown.replace('["Q-17", "Q-18"]', '"Q-17"'),
            dropdown.replace('"Q-18"', '"Q-17"'),
            dropdown + "\n" + dropdown.replace('"Q-18"', '"Q-19"'),
            f"Use {dropdown}",
        ):
            with self.subTest(declaration=declaration):
                source.notes = "\n".join((observed, route, declaration))
                self.assertIn("exact selectable option", validate_browser_handoff([case.model_copy(deep=True)], source, [REQUIREMENT])[1])

        foreign = case.model_copy(deep=True)
        foreign.steps[0].action = foreign.steps[0].action.replace("example.test", "elsewhere.test")
        source.notes = "\n".join((observed, route.replace("example.test", "elsewhere.test"), dropdown.replace("example.test", "elsewhere.test")))
        self.assertIn("execution.target_origin_mismatch", validate_browser_handoff([foreign], source, [REQUIREMENT])[1])

    def test_explicit_dropdown_notes_do_not_override_conflicting_html(self):
        source = context()
        field = source.grounded_context.ui_elements[0]
        field.select_options = ["Q-17"]
        source.notes = f'Browser dropdown "Reference" on "{TARGET}" options ["Q-17", "Q-18"]'
        case = candidate()
        case.steps[1].action = 'Select exact option "Q-17" in field "Reference"'
        case.steps[1].expected = ""
        self.assertIn("exact selectable option", validate_browser_handoff([case.model_copy(deep=True)], source, [REQUIREMENT])[1])
        source.notes = f'Browser dropdown "Reference" on "{TARGET}" options ["Q-17"]'
        self.assertEqual(validate_browser_handoff([case], source, [REQUIREMENT]), [])

    def test_unsupported_expectations_and_unverified_evidence_retain_original_case(self):
        for index, changes, reason in (
            (0, {"action": 'Open "https://example.test/invented"'}, "supplied application URL"),
            (1, {"action": 'Enter "Q-17" into the "ref" field'}, "exact interface evidence"),
            (1, {"action": f'Open "{TARGET}" as an auditor'}, "each browser navigation"),
            (2, {"action": 'Click exact button "Submit mock"'}, "exact interface evidence"),
            (2, {"action": 'Click the "Save request" button'}, "exact button or link action"),
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
