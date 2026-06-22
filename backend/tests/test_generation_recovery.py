from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.analysis_agent import fallback_requirement_analysis
from app.agents.test_case_agent import (
    _build_coverage_planner_agent,
    _build_generation_pipeline,
    _build_refinement_pipeline,
    _combined_event_text,
    _coverage_augmentation_warning,
    _coverage_gap_counts,
    _new_workflow_diagnostics,
    _prepare_workflow_inputs,
    _record_parser_recovery,
    generate_test_cases,
)
from app.agents.test_case_coverage import _fallback_coverage_plan
from app.models import EnrichInput, GenerateTestCasesInput, GroundedContext, GroundedUIElement, Requirement, TestCaseTemplate
from app.utils.llm_json import parse_test_cases_json_detailed


class TestCaseGenerationRecoveryTests(unittest.TestCase):
    def test_coverage_planner_uses_raw_json_output_for_parser_recovery(self) -> None:
        agent = _build_coverage_planner_agent(
            "test-model",
            "REQ-001: The system shall allow users to sign in.",
            "No additional context provided.",
        )

        self.assertIsNone(getattr(agent, "output_schema", None))
        self.assertEqual(getattr(agent, "output_key", None), "coverage_plan")
        self.assertGreaterEqual(agent.generate_content_config.max_output_tokens, 24000)
        self.assertEqual(agent.generate_content_config.response_mime_type, "application/json")

    def test_test_case_agents_use_raw_json_output_for_parser_recovery(self) -> None:
        generation_pipeline = _build_generation_pipeline(
            "test-model",
            "REQ-001: The system shall allow users to sign in.",
            "No additional context provided.",
            "Name: default, Format: table, Fields: id, title, steps, tags",
            threshold=90,
            max_iterations=1,
        )
        generation_agent = next(agent for agent in generation_pipeline.sub_agents if agent.name == "TestCaseGeneratorAgent")

        refinement_pipeline = _build_refinement_pipeline(
            "test-model",
            "REQ-001: The system shall allow users to sign in.",
            "No additional context provided.",
            "Name: default, Format: table, Fields: id, title, steps, tags",
            threshold=90,
            max_iterations=1,
            human_feedback="Tighten assertions.",
        )
        refinement_agent = next(agent for agent in refinement_pipeline.sub_agents if agent.name == "TestCaseRefinementAgent")

        self.assertIsNone(getattr(generation_agent, "output_schema", None))
        self.assertEqual(generation_agent.output_key, "current_test_cases")
        self.assertEqual(generation_agent.generate_content_config.response_mime_type, "application/json")
        self.assertIsNone(getattr(refinement_agent, "output_schema", None))
        self.assertEqual(refinement_agent.output_key, "current_test_cases")
        self.assertEqual(refinement_agent.generate_content_config.response_mime_type, "application/json")

    def test_test_case_prompts_include_browser_assertion_negative_examples(self) -> None:
        generation_pipeline = _build_generation_pipeline(
            "test-model",
            "REQ-001: The docs page shall show installation guidance.",
            'Grounded UI elements: Heading: exact text "Install Playwright"',
            "Name: default, Format: table, Fields: id, title, steps, tags",
            threshold=90,
            max_iterations=1,
        )
        generation_agent = next(agent for agent in generation_pipeline.sub_agents if agent.name == "TestCaseGeneratorAgent")
        review_loop = next(agent for agent in generation_pipeline.sub_agents if agent.name == "ValidationLoop")
        validator_agent = next(agent for agent in review_loop.sub_agents if agent.name == "TestCaseValidatorAgent")
        refinement_pipeline = _build_refinement_pipeline(
            "test-model",
            "REQ-001: The docs page shall show installation guidance.",
            'Grounded UI elements: Heading: exact text "Install Playwright"',
            "Name: default, Format: table, Fields: id, title, steps, tags",
            threshold=90,
            max_iterations=1,
            human_feedback="Tighten browser assertions.",
        )
        refinement_agent = next(agent for agent in refinement_pipeline.sub_agents if agent.name == "TestCaseRefinementAgent")

        for instruction in (generation_agent.instruction, validator_agent.instruction, refinement_agent.instruction):
            self.assertIn('"heading" should be visible', instruction)
            self.assertIn('heading "Exact accessible name" is visible', instruction)
            self.assertIn('"#email-input" should be visible', instruction)

    def test_grounded_context_prompt_preserves_element_type_and_exact_accessible_name(self) -> None:
        requirements = [Requirement(id="REQ-001", text="The docs page shall show installation guidance.")]
        context = EnrichInput(
            requirements=requirements,
            grounded_context=GroundedContext(
                ui_elements=[
                    GroundedUIElement(
                        id="ART-APP-01-UI-H-01",
                        source_id="ART-APP-01",
                        name="Install Playwright",
                        element_type="Heading",
                        description="Documentation installation heading.",
                        href="/docs/intro",
                    )
                ]
            ),
        )

        _, context_text, _ = _prepare_workflow_inputs(
            requirements,
            context,
            TestCaseTemplate(name="default", format="table", fields=["id", "title", "steps", "tags"]),
        )

        self.assertIn('Heading: exact text "Install Playwright" -> /docs/intro', context_text)

    def test_parser_recovery_diagnostics_do_not_populate_failures(self) -> None:
        diagnostics = _new_workflow_diagnostics()

        _record_parser_recovery(
            diagnostics,
            "TestCaseGeneratorAgent",
            "invalid JSON payload: EOF while parsing a string; recovered 2 complete test_cases entries",
            '{"test_cases": [{"id": "TC-001"}',
            artifact_label="test-case",
        )

        self.assertEqual(diagnostics["status"], "partial")
        self.assertIsNone(diagnostics["failure_reason"])
        self.assertEqual(diagnostics["parser_failures"], [])
        self.assertEqual(len(diagnostics["parser_recoveries"]), 1)
        self.assertIn("TestCaseGeneratorAgent: invalid JSON payload", diagnostics["parser_recoveries"][0])
        self.assertEqual(diagnostics["warnings"], [])

    def test_combined_event_text_allows_parser_to_receive_full_payload(self) -> None:
        event = SimpleNamespace(
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(text='{"test_cases": ['),
                    SimpleNamespace(text='{"id": "TC-001", "title": "Invite user", "steps": [{"step": 1, "action": "Invite", "expected": "Sent"}]}'),
                    SimpleNamespace(text="]}"),
                ]
            )
        )

        parsed, error = parse_test_cases_json_detailed(_combined_event_text(event))

        self.assertIsNone(error)
        self.assertEqual([item["id"] for item in parsed], ["TC-001"])

    def test_rejected_model_suite_is_recovered_with_fallback_coverage(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password."),
            Requirement(
                id="REQ-002",
                text="The system shall lock the account after 5 failed login attempts within 10 minutes.",
            ),
        ]
        partial_model_cases = [
            {
                "id": "TC-MODEL-001",
                "title": "Successful sign in",
                "description": "Verify a user can sign in with valid credentials.",
                "priority": "High",
                "type": "Functional",
                "status": "Ready",
                "preconditions": "A registered user account exists.",
                "steps": [
                    {"step": 1, "action": "Enter valid email and password.", "expected": "Credentials are accepted."},
                    {"step": 2, "action": "Submit the sign-in form.", "expected": "The user is signed in."},
                ],
                "expected_result": "The user lands on the authenticated home page.",
                "automation_status": "To Be Automated",
                "component": "Authentication",
                "tags": ["REQ-001", "scenario:happy-path"],
            }
        ]
        workflow = {
            "test_cases": partial_model_cases,
            "requirement_analysis": fallback_requirement_analysis(requirements),
            "coverage_plan": _fallback_coverage_plan(requirements),
            "review": {
                "approved": False,
                "score": 0,
                "threshold": 90,
                "summary": "Model output missed required coverage.",
                "blocking_issues": ["Requirements without traceable test cases: REQ-002."],
                "suggestions": [],
                "unmet_criteria": ["Every requirement must be covered by at least one tagged test case."],
            },
            "approved": False,
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {"approval_threshold": 90},
            "workflow_diagnostics": {
                "status": "partial",
                "used_fallback": False,
                "failure_reason": "quality_rejection",
                "parser_recoveries": [
                    "TestCaseGeneratorAgent: invalid JSON payload: Unterminated string; recovered 1 complete test_cases entries",
                    "TestCaseRefinerAgent: invalid JSON payload: EOF while parsing; recovered 1 complete test_cases entries",
                ],
                "warnings": [],
            },
        }
        payload = GenerateTestCasesInput(
            requirements=requirements,
            template=TestCaseTemplate(name="default", format="table", fields=["id", "title", "steps", "tags"]),
        )

        settings = type("Settings", (), {"model_name": "test-model"})()
        with patch("app.agents.test_case_agent.get_settings", return_value=settings):
            with patch("app.agents.test_case_agent._run_workflow_sync", return_value=workflow):
                result = generate_test_cases(payload)

        self.assertTrue(result["approved"])
        self.assertGreaterEqual(result["review"]["score"], result["review"]["threshold"])
        self.assertGreater(len(result["test_cases"]), len(partial_model_cases))
        self.assertEqual(result["coverage_metrics"]["requirements_without_tests"], [])
        self.assertEqual(result["workflow_diagnostics"]["status"], "partial")
        self.assertFalse(result["workflow_diagnostics"]["used_fallback"])
        self.assertIsNone(result["workflow_diagnostics"]["failure_reason"])
        self.assertEqual(result["workflow_diagnostics"]["recovery_reason"], "coverage_augmentation")
        self.assertEqual(len(result["workflow_diagnostics"]["parser_recoveries"]), 2)
        self.assertEqual(result["workflow_diagnostics"]["generation_source_counts"]["model_recovered"], len(partial_model_cases))
        self.assertGreater(result["workflow_diagnostics"]["generation_source_counts"]["deterministic_coverage_completion"], 0)
        self.assertFalse(any("recovered usable test-case JSON" in warning for warning in result["workflow_diagnostics"]["warnings"]))
        self.assertTrue(
            any(
                warning.startswith("Recovered partial model output needed deterministic coverage completion")
                and "must-have scenario" in warning
                and "total deterministic coverage case" in warning
                for warning in result["workflow_diagnostics"]["warnings"]
            )
        )
        self.assertEqual(result["workflow_diagnostics"]["completion_source"], "coverage_completion")
        self.assertEqual(
            result["workflow_diagnostics"]["deterministic_total_additions"],
            result["workflow_diagnostics"]["generation_source_counts"]["deterministic_coverage_completion"],
        )
        self.assertEqual(result["iteration_history"][-1]["actor"], "FallbackCoverageRecovery")
        evidence = result["generation_evidence"]
        self.assertEqual(evidence["final_status"], "partial")
        self.assertEqual(evidence["parser_recovery_count"], 2)
        self.assertEqual([item["pass_type"] for item in evidence["passes"]], ["sequential", "deterministic_coverage_completion"])
        self.assertGreater(evidence["deterministic_additions_total"], 0)
        self.assertEqual(evidence["deterministic_total_additions"], evidence["deterministic_additions_total"])
        self.assertEqual(evidence["completion_source"], "coverage_completion")
        self.assertFalse(evidence["passes"][0]["raw_output_summary"]["raw_content_stored"])
        self.assertEqual(evidence["passes"][1]["review_status"], "approved")
        model_cases = [test_case for test_case in result["test_cases"] if test_case.generation_source == "model_recovered"]
        completion_cases = [test_case for test_case in result["test_cases"] if test_case.generation_source == "deterministic_coverage_completion"]
        self.assertEqual([test_case.source_case_id for test_case in model_cases], ["TC-MODEL-001"])
        self.assertTrue(all(test_case.coverage_completion_reason == "coverage_augmentation" for test_case in completion_cases))
        self.assertTrue(all(test_case.generation_pass_id == evidence["passes"][1]["pass_id"] for test_case in completion_cases))

    def test_coverage_completion_adds_exact_missing_scenario_refs(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall allow managers to approve standard and escalated reports.",
            )
        ]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {
                        "id": "REQ-001-SCN-01",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Manager approves a standard report",
                        "objective": "Verify standard approval succeeds.",
                        "priority": "High",
                        "must_have": True,
                    },
                    {
                        "id": "REQ-001-SCN-02",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Manager approves an escalated report",
                        "objective": "Verify escalated approval succeeds.",
                        "priority": "High",
                        "must_have": True,
                    },
                ],
            }
        ]
        partial_model_cases = [
            {
                "id": "TC-MODEL-001",
                "title": "Manager approves escalated report",
                "description": "Verify a manager can approve an escalated report.",
                "priority": "High",
                "type": "Functional",
                "status": "Ready",
                "preconditions": "An escalated report exists in Submitted status.",
                "steps": [
                    {"step": 1, "action": "Open the escalated report as a manager.", "expected": "The approval action is available."},
                    {"step": 2, "action": "Approve the escalated report.", "expected": "The report is approved."},
                ],
                "expected_result": "The escalated report is approved.",
                "automation_status": "To Be Automated",
                "component": "Approvals",
                "linked_requirement_ids": ["REQ-001"],
                "scenario_refs": ["REQ-001-SCN-02"],
                "tags": ["REQ-001", "scenario:happy-path"],
            }
        ]
        workflow = {
            "test_cases": partial_model_cases,
            "requirement_analysis": fallback_requirement_analysis(requirements),
            "coverage_plan": coverage_plan,
            "review": {
                "approved": False,
                "score": 72,
                "threshold": 90,
                "summary": "Model output missed one planned scenario.",
                "blocking_issues": ["Missing must-have planned scenarios: REQ-001-SCN-01."],
                "suggestions": [],
                "unmet_criteria": ["Every must-have scenario needs a corresponding test case."],
            },
            "approved": False,
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {"approval_threshold": 90},
            "workflow_diagnostics": {"status": "partial", "used_fallback": False, "failure_reason": "quality_rejection", "warnings": []},
        }
        payload = GenerateTestCasesInput(
            requirements=requirements,
            template=TestCaseTemplate(name="default", format="table", fields=["id", "title", "steps", "tags"]),
        )

        settings = type("Settings", (), {"model_name": "test-model"})()
        with patch("app.agents.test_case_agent.get_settings", return_value=settings):
            with patch("app.agents.test_case_agent._run_workflow_sync", return_value=workflow):
                result = generate_test_cases(payload)

        completion_cases = [test_case for test_case in result["test_cases"] if test_case.generation_source == "deterministic_coverage_completion"]
        completion_refs = {reference for test_case in completion_cases for reference in test_case.scenario_refs}
        self.assertIn("REQ-001-SCN-01", completion_refs)
        self.assertIn("REQ-001-SCN-02", {reference for test_case in result["test_cases"] for reference in test_case.scenario_refs})
        self.assertEqual(result["coverage_metrics"]["scenario_ref_coverage_mode"], "exact")
        self.assertFalse(result["workflow_diagnostics"]["scenario_ref_coverage_degraded"])

    def test_missing_scenario_refs_are_reported_in_workflow_diagnostics(self) -> None:
        requirements = [Requirement(id="REQ-001", text="The system shall display the dashboard summary for the signed-in user.")]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {
                        "id": "REQ-001-SCN-01",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Dashboard summary is displayed",
                        "objective": "Verify the signed-in user sees the dashboard summary.",
                        "priority": "High",
                        "must_have": True,
                    }
                ],
            }
        ]
        workflow = {
            "test_cases": [
                {
                    "id": "TC-MODEL-001",
                    "title": "Signed-in user sees dashboard summary",
                    "description": "Verify the dashboard summary appears after sign-in.",
                    "priority": "High",
                    "type": "Functional",
                    "status": "Ready",
                    "preconditions": "A user is signed in.",
                    "steps": [
                        {"step": 1, "action": "Sign in with valid credentials.", "expected": "Dashboard loads."},
                        {"step": 2, "action": "View the dashboard summary.", "expected": "Summary widgets are visible."},
                    ],
                    "expected_result": "The signed-in user sees the dashboard summary.",
                    "automation_status": "To Be Automated",
                    "component": "Dashboard",
                    "linked_requirement_ids": ["REQ-001"],
                    "tags": ["REQ-001", "scenario:happy-path"],
                }
            ],
            "requirement_analysis": fallback_requirement_analysis(requirements),
            "coverage_plan": coverage_plan,
            "review": {
                "approved": True,
                "score": 94,
                "threshold": 90,
                "summary": "Approved by model review.",
                "blocking_issues": [],
                "suggestions": [],
                "unmet_criteria": [],
            },
            "approved": True,
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {"approval_threshold": 90},
            "workflow_diagnostics": {"status": "completed", "used_fallback": False, "warnings": []},
        }
        payload = GenerateTestCasesInput(
            requirements=requirements,
            template=TestCaseTemplate(name="default", format="table", fields=["id", "title", "steps", "tags"]),
        )

        settings = type("Settings", (), {"model_name": "test-model"})()
        with patch("app.agents.test_case_agent.get_settings", return_value=settings):
            with patch("app.agents.test_case_agent._run_workflow_sync", return_value=workflow):
                result = generate_test_cases(payload)

        self.assertTrue(result["workflow_diagnostics"]["scenario_ref_coverage_degraded"])
        self.assertEqual(result["workflow_diagnostics"]["scenario_ref_missing_case_count"], 1)
        self.assertEqual(result["coverage_metrics"]["scenario_ref_coverage_mode"], "heuristic")
        self.assertTrue(any("heuristic scenario-type inference" in warning for warning in result["workflow_diagnostics"]["warnings"]))

    def test_coverage_completion_warning_splits_must_have_and_optional_counts(self) -> None:
        requirements = [Requirement(id="REQ-001", text="The system shall validate approval scenarios.")]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {"id": "REQ-001-SCN-01", "scenario_type": "Happy Path", "title": "Primary approval", "must_have": True},
                    {"id": "REQ-001-SCN-02", "scenario_type": "Authorization", "title": "Manager approval", "must_have": True},
                    {"id": "REQ-001-SCN-03", "scenario_type": "Negative", "title": "Reject invalid approval", "must_have": False},
                    {"id": "REQ-001-SCN-04", "scenario_type": "Integration", "title": "Audit capture", "must_have": False},
                ],
            }
        ]
        original_cases = [
            {
                "id": "TC-001",
                "title": "Primary approval",
                "linked_requirement_ids": ["REQ-001"],
                "scenario_refs": ["REQ-001-SCN-01"],
                "tags": ["REQ-001", "scenario:happy-path"],
            }
        ]
        augmented_cases = original_cases + [
            {"id": "TC-002", "linked_requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-02"], "tags": ["REQ-001", "scenario:authorization"]},
            {"id": "TC-003", "linked_requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-03"], "tags": ["REQ-001", "scenario:negative"]},
            {"id": "TC-004", "linked_requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-04"], "tags": ["REQ-001", "scenario:integration"]},
        ]

        counts = _coverage_gap_counts(
            original_test_cases=original_cases,
            augmented_test_cases=augmented_cases,
            requirements=requirements,
            coverage_plan=coverage_plan,
        )
        warning = _coverage_augmentation_warning(
            original_test_cases=original_cases,
            augmented_test_cases=augmented_cases,
            requirements=requirements,
            coverage_plan=coverage_plan,
            diagnostics={},
            deterministic_counts=counts,
        )

        self.assertEqual(counts["missing_must_have_scenario_count"], 1)
        self.assertEqual(counts["missing_optional_scenario_count"], 2)
        self.assertEqual(counts["deterministic_must_have_additions"], 1)
        self.assertEqual(counts["deterministic_optional_additions"], 2)
        self.assertIn("1 must-have scenario", warning)
        self.assertIn("2 optional/planned scenarios", warning)
        self.assertIn("1 must-have deterministic case and 2 optional deterministic cases", warning)
        self.assertIn("3 total deterministic coverage cases", warning)

    def test_coverage_completion_warning_handles_optional_only_additions(self) -> None:
        requirements = [Requirement(id="REQ-001", text="The system shall validate dashboard scenarios.")]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {"id": "REQ-001-SCN-01", "scenario_type": "Happy Path", "title": "Dashboard loads", "must_have": True},
                    {"id": "REQ-001-SCN-02", "scenario_type": "Data Variation", "title": "Alternate widget data", "must_have": False},
                ],
            }
        ]
        original_cases = [
            {
                "id": "TC-001",
                "title": "Dashboard loads",
                "linked_requirement_ids": ["REQ-001"],
                "scenario_refs": ["REQ-001-SCN-01"],
                "tags": ["REQ-001", "scenario:happy-path"],
            }
        ]
        augmented_cases = original_cases + [
            {"id": "TC-002", "linked_requirement_ids": ["REQ-001"], "scenario_refs": ["REQ-001-SCN-02"], "tags": ["REQ-001", "scenario:data-variation"]}
        ]

        counts = _coverage_gap_counts(
            original_test_cases=original_cases,
            augmented_test_cases=augmented_cases,
            requirements=requirements,
            coverage_plan=coverage_plan,
        )
        warning = _coverage_augmentation_warning(
            original_test_cases=original_cases,
            augmented_test_cases=augmented_cases,
            requirements=requirements,
            coverage_plan=coverage_plan,
            diagnostics={},
            deterministic_counts=counts,
        )

        self.assertEqual(counts["missing_must_have_scenario_count"], 0)
        self.assertEqual(counts["missing_optional_scenario_count"], 1)
        self.assertEqual(counts["deterministic_must_have_additions"], 0)
        self.assertEqual(counts["deterministic_optional_additions"], 1)
        self.assertIn("all must-have scenarios were covered", warning)
        self.assertIn("1 optional/planned scenario remained", warning)
        self.assertIn("1 optional deterministic case", warning)
        self.assertNotIn("must-have deterministic case", warning)

    def test_missing_model_credentials_use_deterministic_generation_fallback(self) -> None:
        requirements = [Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")]
        payload = GenerateTestCasesInput(
            requirements=requirements,
            template=TestCaseTemplate(name="default", format="table", fields=["id", "title", "steps", "tags"]),
        )

        with patch("app.agents.test_case_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            with patch("app.agents.test_case_agent._run_workflow_sync") as run_workflow:
                result = generate_test_cases(payload)

        run_workflow.assert_not_called()
        self.assertTrue(result["test_cases"])
        self.assertTrue(result["approved"])
        self.assertTrue(result["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(result["workflow_diagnostics"]["failure_reason"], "missing_model_credentials")
        evidence = result["generation_evidence"]
        self.assertEqual(evidence["final_status"], "fallback")
        self.assertEqual([item["pass_type"] for item in evidence["passes"]], ["deterministic_full_fallback"])
        self.assertEqual(evidence["deterministic_additions_total"], len(result["test_cases"]))
        self.assertEqual(result["workflow_diagnostics"]["completion_source"], "full_fallback")
        self.assertEqual(result["workflow_diagnostics"]["deterministic_total_additions"], len(result["test_cases"]))
        self.assertTrue(any("deterministic draft artifacts" in warning for warning in result["workflow_diagnostics"]["warnings"]))
        self.assertFalse(any("deterministic coverage completion" in warning for warning in result["workflow_diagnostics"]["warnings"]))
        self.assertEqual(evidence["passes"][0]["raw_output_summary"]["model_case_count"], 0)
        self.assertEqual(evidence["passes"][0]["raw_output_summary"]["fallback_case_count"], len(result["test_cases"]))
        self.assertEqual(result["workflow_diagnostics"]["generation_source_counts"], {"deterministic_full_fallback": len(result["test_cases"])})
        self.assertTrue(all(test_case.generation_source == "deterministic_full_fallback" for test_case in result["test_cases"]))
        self.assertTrue(all(test_case.generation_pass_id == evidence["passes"][0]["pass_id"] for test_case in result["test_cases"]))
        self.assertTrue(all(test_case.source_case_id for test_case in result["test_cases"]))


if __name__ == "__main__":
    unittest.main()
