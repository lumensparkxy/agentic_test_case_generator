from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import _build_grounded_context_from_enrich_input
from app.models import EnrichInput, EnrichResponse, Requirement


class EnrichContractTests(unittest.TestCase):
    def test_build_grounded_context_from_enrich_input_registers_artifacts(self) -> None:
        payload = EnrichInput(
            requirements=[Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")],
            app_link="https://example.test/app",
            prototype_link="https://example.test/prototype",
            diagram_links=["https://example.test/diagram-1"],
            image_links=["https://example.test/image-1", "https://example.test/image-2"],
            notes="Context for local testing",
        )

        grounded_context = _build_grounded_context_from_enrich_input(payload)

        self.assertEqual(len(grounded_context.artifact_sources), 6)
        self.assertTrue(grounded_context.summary)
        self.assertEqual(grounded_context.artifact_sources[0].source_type, "app")
        self.assertEqual(grounded_context.artifact_sources[-1].source_type, "note")

    def test_enrich_response_preserves_input_shape_and_adds_grounded_context(self) -> None:
        payload = EnrichInput(
            requirements=[Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")],
            notes="Context notes",
        )

        response = EnrichResponse(
            **payload.model_dump(exclude={"grounded_context"}),
            grounded_context=_build_grounded_context_from_enrich_input(payload),
        )

        self.assertEqual(len(response.requirements), 1)
        self.assertIsNotNone(response.grounded_context)
        self.assertEqual(response.grounded_context.artifact_sources[0].source_type, "note")


if __name__ == "__main__":
    unittest.main()
