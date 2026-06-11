import importlib.util
import tempfile
import unittest
from pathlib import Path

from docx import Document


class ClientSolutionBriefTests(unittest.TestCase):
    def _load_module(self):
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "build_client_solution_brief.py"
        spec = importlib.util.spec_from_file_location("build_client_solution_brief", script_path)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        return module

    def test_build_docx_runs_without_screenshots(self):
        module = self._load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            module.OUT_DIR = temp_root / "client_submission"
            module.SCREENSHOT_DIR = module.OUT_DIR / "screenshots"
            module.DOCX_PATH = module.OUT_DIR / "client_demo_agentic_testing_solution_brief.docx"

            output_path = module.build_docx()

            self.assertEqual(module.DOCX_PATH, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

            document = Document(output_path)
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            self.assertIn("Agentic Testing Client Demo Brief", text)
            self.assertIn("No screenshots were found", text)
            self.assertIn("client_submission/screenshots", text)


if __name__ == "__main__":
    unittest.main()
