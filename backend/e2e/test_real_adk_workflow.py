from __future__ import annotations

import sys
import unittest
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from workflow_runner import RealAdkWorkflowRunner, config_from_env, real_adk_e2e_enabled


@unittest.skipUnless(real_adk_e2e_enabled(), "Set RUN_REAL_ADK_E2E=1 to run the real ADK backend E2E workflow")
class RealAdkWorkflowTests(unittest.TestCase):
    def test_real_adk_workflow_produces_artifacts(self) -> None:
        result = RealAdkWorkflowRunner(config_from_env()).run()
        summary = result.summary

        self.assertTrue(result.summary_path.is_file())
        self.assertTrue(result.output_dir.is_dir())
        self.assertGreater(summary["counts"]["requirements"], 0)
        self.assertGreater(summary["counts"]["requirement_analysis"], 0)
        self.assertGreater(summary["counts"]["coverage_plan"], 0)
        self.assertGreater(summary["counts"]["test_cases"], 0)
        self.assertEqual(summary["automation"]["status"], "generated")
        self.assertIsInstance(summary["execution_preview"], dict)

        for artifact_path in summary["artifact_paths"].values():
            self.assertTrue(Path(artifact_path).exists(), artifact_path)
        for export in summary["exports"].values():
            self.assertGreater(export["bytes"], 0)
            self.assertTrue(Path(export["path"]).exists(), export["path"])


if __name__ == "__main__":
    unittest.main()
