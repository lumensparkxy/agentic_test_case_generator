from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.analysis_agent import fallback_requirement_analysis
from app.agents.test_case_agent import _fallback_coverage_plan, generate_test_cases
from app.models import GenerateTestCasesInput, Requirement, TestCaseTemplate


class TestCaseGenerationRecoveryTests(unittest.TestCase):
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
            "workflow_diagnostics": {"status": "completed", "used_fallback": False, "failure_reason": "quality_rejection"},
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
        self.assertTrue(result["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(result["workflow_diagnostics"]["failure_reason"], "quality_recovery")
        self.assertEqual(result["iteration_history"][-1]["actor"], "FallbackCoverageRecovery")

    def test_missing_model_credentials_use_deterministic_generation_fallback(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")
        ]
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
