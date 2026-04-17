from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils.genai_response import extract_response_text


class GenAiResponseUtilsTests(unittest.TestCase):
    def test_extract_response_text_joins_text_parts_only(self) -> None:
        response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(text="First line"),
                            SimpleNamespace(function_call={"name": "exit_loop"}),
                            SimpleNamespace(text="Second line"),
                        ]
                    )
                )
            ]
        )

        self.assertEqual(extract_response_text(response), "First line\nSecond line")

    def test_extract_response_text_returns_empty_for_missing_candidates(self) -> None:
        response = SimpleNamespace(candidates=[])

        self.assertEqual(extract_response_text(response), "")


if __name__ == "__main__":
    unittest.main()