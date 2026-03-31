from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.requirements_agent import _finalize_requirements, _heuristic_extract


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


if __name__ == "__main__":
    unittest.main()