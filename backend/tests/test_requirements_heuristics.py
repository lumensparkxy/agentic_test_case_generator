from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from unittest.mock import patch

from app.agents.requirements_agent import _build_fallback_workflow, _finalize_requirements, _heuristic_extract, extract_requirements
from app.models import Requirement


class RequirementHeuristicExtractionTests(unittest.TestCase):
    def test_heuristic_extract_keeps_password_related_requirements(self) -> None:
        document_text = """# Features
- Allow users to sign in using email and password.
- Allow users to reset their password via email link.
- API_KEY values must never be logged.
"""

        extracted = _heuristic_extract(document_text)
        requirements = _finalize_requirements(extracted)
        requirement_texts = [requirement.text.lower() for requirement in requirements]

        self.assertTrue(any("sign in using email and password" in text for text in requirement_texts))
        self.assertTrue(any("reset their password via email link" in text for text in requirement_texts))
        self.assertFalse(any("api_key" in text for text in requirement_texts))

    def test_heuristic_extract_recognizes_functional_capabilities_section(self) -> None:
        document_text = """## Functional capabilities
- Allow employees to create and save an expense report as a draft with one or more line items.
- The system shall prevent submission when the total claimed amount exceeds the configured policy limit.
- node_modules should not be committed.
"""

        extracted = _heuristic_extract(document_text)
        requirements = _finalize_requirements(extracted)
        requirement_texts = [requirement.text.lower() for requirement in requirements]

        self.assertTrue(any("create and save an expense report as a draft" in text for text in requirement_texts))
        self.assertTrue(any("prevent submission" in text for text in requirement_texts))
        self.assertFalse(any("node_modules" in text for text in requirement_texts))


class RequirementFallbackWorkflowTests(unittest.TestCase):
    def test_fallback_workflow_preserves_threshold_and_marks_diagnostics(self) -> None:
        requirements = [Requirement(id="REQ-001", text="The system shall allow users to sign in.")]

        workflow = _build_fallback_workflow(
            requirements=requirements,
            summary="Fallback summary.",
            document_count=1,
            existing_settings={"approval_threshold": 92, "max_iterations": 4},
            existing_diagnostics={"status": "partial", "warnings": ["Existing warning"]},
        )

        self.assertEqual(workflow["review"]["threshold"], 92)
        self.assertEqual(workflow["workflow_settings"]["approval_threshold"], 92)
        self.assertEqual(workflow["workflow_settings"]["max_iterations"], 4)
        self.assertEqual(workflow["workflow_diagnostics"]["status"], "fallback")
        self.assertTrue(workflow["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(workflow["workflow_diagnostics"]["failure_reason"], "fallback_generated_artifacts")
        self.assertTrue(any("Existing warning" == warning for warning in workflow["workflow_diagnostics"]["warnings"]))

    def test_extract_requirements_uses_heuristic_fallback_without_model_credentials(self) -> None:
        document_text = """
        ## Functional Requirements
        - The system shall allow users to install the Playwright pytest plugin using the command `pip install pytest-playwright`.
        - The system shall support running a single test file such as `test_login.py`.
        """

        with patch("app.agents.requirements_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            with patch("app.agents.requirements_agent.run_requirement_extraction_workflow_sync") as run_workflow:
                workflow = extract_requirements(document_text)

        run_workflow.assert_not_called()
        self.assertGreaterEqual(len(workflow["requirements"]), 2)
        self.assertTrue(workflow["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(workflow["workflow_diagnostics"]["status"], "fallback")


if __name__ == "__main__":
    unittest.main()
