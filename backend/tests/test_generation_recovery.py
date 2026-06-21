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
    _new_workflow_diagnostics,
    _record_parser_recovery,
    generate_test_cases,
)
from app.agents.test_case_coverage import _fallback_coverage_plan
from app.models import GenerateTestCasesInput, Requirement, TestCaseTemplate
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
        self.assertFalse(any("recovered usable test-case JSON" in warning for warning in result["workflow_diagnostics"]["warnings"]))
        self.assertTrue(
            any(
                warning.startswith("Recovered partial model output left ")
                and "requirement(s)" in warning
                and "must-have scenario(s)" in warning
                and "added " in warning
                for warning in result["workflow_diagnostics"]["warnings"]
            )
        )
        self.assertEqual(result["iteration_history"][-1]["actor"], "FallbackCoverageRecovery")

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


if __name__ == "__main__":
    unittest.main()
