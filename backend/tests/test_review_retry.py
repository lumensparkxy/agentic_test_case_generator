from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adk_client import _run_requirement_workflow_sync
from app.agents.test_case_agent import _run_workflow_sync
from app.models import Requirement, WorkflowSettings
from app.utils.workflow_diagnostics import RETRY_REASON_KEY, RETRYABLE_PARSER_FAILURE_KEY


def _diagnostics(**overrides):
    payload = {
        "status": "partial",
        "used_fallback": False,
        "failure_reason": "parser_failure",
        "timed_out": False,
        "stalled": False,
        "max_iterations_reached": False,
        "parser_failures": [],
        "warnings": [],
        "best_iteration": None,
        "attempt_count": 1,
    }
    payload.update(overrides)
    return payload


class RequirementWorkflowRetryTests(unittest.TestCase):
    def test_retries_requirement_parser_failure_and_strips_private_marker(self) -> None:
        calls = []

        async def fake_run(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "requirements": [],
                    "approved": False,
                    "review": {},
                    "iteration_history": [],
                    "coverage_metrics": {},
                    "workflow_settings": {},
                    "workflow_diagnostics": _diagnostics(
                        parser_failures=["ReviewerAgent: invalid JSON payload: Expecting value"],
                        **{
                            RETRYABLE_PARSER_FAILURE_KEY: True,
                            RETRY_REASON_KEY: "ReviewerAgent: invalid JSON payload: Expecting value",
                        },
                    ),
                }
            return {
                "requirements": [{"id": "REQ-001", "text": "The system shall allow users to sign in."}],
                "approved": True,
                "review": {"approved": True, "score": 95, "threshold": 85, "summary": "Approved.", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
                "iteration_history": [],
                "coverage_metrics": {},
                "workflow_settings": {},
                "workflow_diagnostics": _diagnostics(status="completed", failure_reason=None),
            }

        with patch("app.adk_client._run_requirement_workflow_async", new=fake_run):
            result = _run_requirement_workflow_sync(
                document_text="Users can sign in.",
                workflow_settings=WorkflowSettings(retry_attempts=1),
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["workflow_diagnostics"]["attempt_count"], 2)
        self.assertTrue(result["approved"])
        self.assertNotIn(RETRYABLE_PARSER_FAILURE_KEY, result["workflow_diagnostics"])
        self.assertNotIn(RETRY_REASON_KEY, result["workflow_diagnostics"])

    def test_strips_private_retry_marker_when_no_retry_remains(self) -> None:
        async def fake_run(**kwargs):
            return {
                "requirements": [],
                "approved": False,
                "review": {},
                "iteration_history": [],
                "coverage_metrics": {},
                "workflow_settings": {},
                "workflow_diagnostics": _diagnostics(
                    parser_failures=["RefinerAgent: invalid JSON payload: Expecting value"],
                    **{
                        RETRYABLE_PARSER_FAILURE_KEY: True,
                        RETRY_REASON_KEY: "RefinerAgent: invalid JSON payload: Expecting value",
                    },
                ),
            }

        with patch("app.adk_client._run_requirement_workflow_async", new=fake_run):
            result = _run_requirement_workflow_sync(
                document_text="Users can sign in.",
                workflow_settings=WorkflowSettings(retry_attempts=0),
            )

        self.assertEqual(result["workflow_diagnostics"]["attempt_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["parser_failures"], ["RefinerAgent: invalid JSON payload: Expecting value"])
        self.assertNotIn(RETRYABLE_PARSER_FAILURE_KEY, result["workflow_diagnostics"])
        self.assertNotIn(RETRY_REASON_KEY, result["workflow_diagnostics"])


class TestCaseWorkflowRetryTests(unittest.TestCase):
    def test_retries_validation_review_parser_failure_and_strips_private_marker(self) -> None:
        calls = []
        requirements = [Requirement(id="REQ-001", text="The system shall allow users to sign in.")]

        async def fake_run(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "test_cases": [],
                    "requirement_analysis": [],
                    "coverage_plan": [],
                    "approved": False,
                    "review": {},
                    "iteration_history": [],
                    "coverage_metrics": {},
                    "workflow_settings": {},
                    "workflow_diagnostics": _diagnostics(
                        parser_failures=["TestCaseValidatorAgent: invalid JSON payload: Expecting value"],
                        **{
                            RETRYABLE_PARSER_FAILURE_KEY: True,
                            RETRY_REASON_KEY: "TestCaseValidatorAgent: invalid JSON payload: Expecting value",
                        },
                    ),
                }
            return {
                "test_cases": [
                    {
                        "id": "TC-001",
                        "title": "Sign in succeeds",
                        "description": "Validate successful sign-in.",
                        "preconditions": [],
                        "steps": [{"step": 1, "action": "Sign in", "expected": "Dashboard is visible"}],
                        "expected_result": "The user is signed in.",
                        "priority": "High",
                        "type": "Functional",
                        "status": "Draft",
                        "automation_status": "Manual",
                        "tags": ["REQ-001", "scenario:happy-path"],
                        "source_refs": [],
                    }
                ],
                "requirement_analysis": [],
                "coverage_plan": [],
                "approved": True,
                "review": {"approved": True, "score": 95, "threshold": 90, "summary": "Approved.", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
                "iteration_history": [],
                "coverage_metrics": {},
                "workflow_settings": {},
                "workflow_diagnostics": _diagnostics(status="completed", failure_reason=None),
            }

        with patch("app.agents.test_case_agent._run_test_case_workflow_async", new=fake_run):
            result = _run_workflow_sync(
                requirements=requirements,
                context=None,
                requirements_text="REQ-001: The system shall allow users to sign in.",
                context_text="No context.",
                template_text="Default",
                model="test-model",
                human_feedback=None,
                existing_test_cases=None,
                workflow_settings=WorkflowSettings(retry_attempts=1),
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["workflow_diagnostics"]["attempt_count"], 2)
        self.assertTrue(result["approved"])
        self.assertNotIn(RETRYABLE_PARSER_FAILURE_KEY, result["workflow_diagnostics"])
        self.assertNotIn(RETRY_REASON_KEY, result["workflow_diagnostics"])


if __name__ == "__main__":
    unittest.main()
