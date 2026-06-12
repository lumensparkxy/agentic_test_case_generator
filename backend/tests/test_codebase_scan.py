import tempfile
import unittest
from pathlib import Path

from scripts import scan_codebase


class CodebaseScanTests(unittest.TestCase):
    def test_source_paths_excludes_ignored_generated_outputs(self):
        paths = [
            ".execution_artifacts/exec_1/trace.zip",
            ".agents/skills/acquire-codebase-knowledge/scripts/scan.py",
            "client_submission/screenshots/home.png",
            "frontend/dist/assets/index.js",
            "frontend/playwright-report/index.html",
            "frontend/test-results/export/error-context.md",
            "frontend/node_modules/vite/index.js",
            "backend/execution_runtime/node_modules/playwright/index.js",
            "backend/execution_runtime/artifacts/run/report.json",
            "scripts/__pycache__/scan_codebase.cpython-314.pyc",
            "backend/app/main.py",
            "frontend/src/App.jsx",
            "frontend/src/api/generated/api-contracts.js",
        ]

        self.assertEqual(
            scan_codebase.source_paths(paths),
            [
                "backend/app/main.py",
                "frontend/src/App.jsx",
                "frontend/src/api/generated/api-contracts.js",
            ],
        )

    def test_build_report_uses_source_paths_for_metrics_and_largest_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_file = root / "backend" / "app" / "main.py"
            ignored_file = root / "frontend" / "dist" / "assets" / "index.js"
            source_file.parent.mkdir(parents=True)
            ignored_file.parent.mkdir(parents=True)
            source_file.write_text("print('source')\n", encoding="utf-8")
            ignored_file.write_text("console.log('generated');\n", encoding="utf-8")

            report = scan_codebase.build_report(
                root,
                [
                    "backend/app/main.py",
                    "frontend/dist/assets/index.js",
                ],
            )

        self.assertIn("Files scanned: 1", report)
        self.assertIn("Files excluded after `git ls-files`: 1", report)
        self.assertIn("backend/app/main.py", report)
        self.assertNotIn("frontend/dist/assets/index.js |", report)


if __name__ == "__main__":
    unittest.main()
