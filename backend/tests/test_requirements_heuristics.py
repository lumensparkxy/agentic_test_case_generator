from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.requirements_agent import _build_fallback_workflow, _convert_to_requirements, _finalize_requirements, _heuristic_extract
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

    def test_convert_to_requirements_preserves_context_metadata(self) -> None:
        requirements = _convert_to_requirements([
            {
                "id": "REQ-LOGIN",
                "text": "The system shall allow users to sign in using email and password.",
                "source_path": "Login.docx > Authentication > Happy path",
                "source_section": "Happy path",
                "source_excerpt": "Users sign in with email and password.",
                "source_hierarchy": ["Login.docx", "Authentication"],
                "quality_flags": ["needs acceptance criteria"],
            }
        ])

        self.assertEqual(requirements[0].id, "REQ-001")
        self.assertEqual(requirements[0].source_path, "Login.docx > Authentication > Happy path")
        self.assertEqual(requirements[0].source_hierarchy, ["Login.docx", "Authentication"])
        self.assertEqual(requirements[0].quality_flags, ["needs acceptance criteria"])


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


if __name__ == "__main__":
    unittest.main()