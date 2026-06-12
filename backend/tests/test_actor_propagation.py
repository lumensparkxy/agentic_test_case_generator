from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.requirements_agent import extract_requirements, refine_requirements
from app.agents.test_case_agent import generate_test_cases, refine_test_cases
from app.models import GenerateTestCasesInput, RefineTestCasesInput, Requirement, TestCase, TestCaseTemplate, TestStep


class ActorPropagationTests(unittest.TestCase):
    def test_extract_requirements_passes_actor_to_requirement_workflow(self) -> None:
        with patch("app.agents.requirements_agent.get_settings") as get_settings:
            get_settings.return_value.model_name = "test-model"
            with patch("app.agents.requirements_agent.run_requirement_extraction_workflow_sync", return_value={"requirements": []}) as run_workflow:
                extract_requirements(
                    "requirement text",
                    actor_user_id="firebase-user-1",
                    request_id="req-1",
                    workflow_run_id="run-1",
                    operation="requirements.parse",
                )

        self.assertEqual(run_workflow.call_args.kwargs["actor_user_id"], "firebase-user-1")
        self.assertEqual(run_workflow.call_args.kwargs["request_id"], "req-1")
        self.assertEqual(run_workflow.call_args.kwargs["workflow_run_id"], "run-1")
        self.assertEqual(run_workflow.call_args.kwargs["operation"], "requirements.parse")

    def test_refine_requirements_passes_actor_to_requirement_workflow(self) -> None:
        with patch("app.agents.requirements_agent.get_settings") as get_settings:
            get_settings.return_value.model_name = "test-model"
            with patch("app.agents.requirements_agent.run_requirement_refinement_workflow_sync", return_value={"requirements": []}) as run_workflow:
                refine_requirements(
                    [{"id": "REQ-1", "text": "Original"}],
                    "change it",
                    actor_user_id="firebase-user-2",
                    request_id="req-2",
                    workflow_run_id="run-2",
                    operation="requirements.refine",
                )

        self.assertEqual(run_workflow.call_args.kwargs["actor_user_id"], "firebase-user-2")
        self.assertEqual(run_workflow.call_args.kwargs["request_id"], "req-2")
        self.assertEqual(run_workflow.call_args.kwargs["workflow_run_id"], "run-2")
        self.assertEqual(run_workflow.call_args.kwargs["operation"], "requirements.refine")

    def test_generate_test_cases_passes_actor_to_test_case_workflow(self) -> None:
        payload = GenerateTestCasesInput(
            requirements=[Requirement(id="REQ-1", text="The system shall do X")],
            template=TestCaseTemplate(name="default", format="table", fields=["id", "title"]),
        )

        with patch("app.agents.test_case_agent.get_settings") as get_settings:
            get_settings.return_value.model_name = "test-model"
            with patch("app.agents.test_case_agent._run_workflow_sync", return_value={"test_cases": []}) as run_workflow:
                generate_test_cases(
                    payload,
                    actor_user_id="firebase-user-3",
                    request_id="req-3",
                    workflow_run_id="run-3",
                    operation="testcases.generate",
                )

        self.assertEqual(run_workflow.call_args.kwargs["actor_user_id"], "firebase-user-3")
        self.assertEqual(run_workflow.call_args.kwargs["request_id"], "req-3")
        self.assertEqual(run_workflow.call_args.kwargs["workflow_run_id"], "run-3")
        self.assertEqual(run_workflow.call_args.kwargs["operation"], "testcases.generate")

    def test_refine_test_cases_passes_actor_to_test_case_workflow(self) -> None:
        payload = RefineTestCasesInput(
            requirements=[Requirement(id="REQ-1", text="The system shall do X")],
            test_cases=[
                TestCase(
                    id="TC-1",
                    title="Sample test",
                    steps=[TestStep(step=1, action="Act", expected="Observe")],
                )
            ],
            template=TestCaseTemplate(name="default", format="table", fields=["id", "title"]),
            feedback="please refine",
        )

        with patch("app.agents.test_case_agent.get_settings") as get_settings:
            get_settings.return_value.model_name = "test-model"
            with patch("app.agents.test_case_agent._run_workflow_sync", return_value={"test_cases": []}) as run_workflow:
                refine_test_cases(
                    payload,
                    actor_user_id="firebase-user-4",
                    request_id="req-4",
                    workflow_run_id="run-4",
                    operation="testcases.refine",
                )

        self.assertEqual(run_workflow.call_args.kwargs["actor_user_id"], "firebase-user-4")
        self.assertEqual(run_workflow.call_args.kwargs["request_id"], "req-4")
        self.assertEqual(run_workflow.call_args.kwargs["workflow_run_id"], "run-4")
        self.assertEqual(run_workflow.call_args.kwargs["operation"], "testcases.refine")


if __name__ == "__main__":
    unittest.main()
