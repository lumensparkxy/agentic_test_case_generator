"""The browser benchmark credits only requirement-linked failures at the intended check."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from browser_benchmark_fixtures import FIXTURES
from evaluate_browser_handoff import defect_status, failed_checks
from app.contracts.execution import ExecutionRunItem


class BrowserHandoffBenchmarkTests(unittest.TestCase):
    def test_only_a_healthy_linked_case_failing_the_intended_check_detects_the_defect(self):
        defect = FIXTURES["receipt"].defects[0]
        intended_failure = {"status": "failed", "failing_checks": [defect.failing_check]}
        cases = (
            ([], {}, {"other": intended_failure}, "no_executable_case"),
            (["target"], {"target": {"status": "failed"}}, {"target": intended_failure}, "no_healthy_pass"),
            (["target"], {"target": {"status": "passed"}}, {"other": intended_failure}, "runtime_unavailable"),
            (["target"], {"target": {"status": "passed"}}, {"target": {"status": "invalid"}}, "runtime_unavailable"),
            (["target"], {"target": {"status": "passed"}}, {"target": {"status": "failed", "failing_checks": []}}, "runtime_unavailable"),
            (["target"], {"target": {"status": "passed"}}, {"target": {"status": "passed"}}, "survived"),
            (
                ["target"],
                {"target": {"status": "passed"}},
                {"target": {"status": "failed", "failing_checks": ['I click exact button "Save request"']}},
                "wrong_failure",
            ),
            (["target"], {"target": {"status": "passed"}}, {"target": intended_failure}, "detected"),
        )
        for eligible, healthy, broken, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(defect_status(defect, eligible, healthy, broken), expected)

    def test_step_evidence_uses_the_matching_case_and_terminal_attempt(self):
        def test_result(case_id, attempts):
            return {"annotations": [{"type": "caseId", "description": case_id}], "results": attempts}

        def attempt(check):
            return {"status": "failed", "steps": [{"title": f"Then {check}", "error": {"message": "failure"}}]}

        tests = [
            test_result("other", [attempt("unrelated case")]),
            test_result("target", [attempt("previous attempt"), attempt("current attempt")]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps({"suites": [{"suites": [{"specs": [{"tests": tests}]}]}]}))
            item = ExecutionRunItem(id="target", source_test_case_id="TC", title="Case", status="failed", spec_id="target", report_json_path=str(path))
            self.assertEqual(failed_checks(item), ["current attempt"])
            self.assertEqual(failed_checks(item.model_copy(update={"status": "passed"})), [])

    def test_each_mutant_changes_one_document_and_fixtures_keep_manual_coverage(self):
        for name, fixture in FIXTURES.items():
            with self.subTest(fixture=name):
                self.assertEqual({req for req, _ in fixture.controls}, set(fixture.requirements))
                for defect in fixture.defects:
                    pages = fixture.documents(defect)
                    self.assertEqual({route for route in pages if pages[route] != fixture.pages[route]}, {fixture.route})
        self.assertIn(FIXTURES["receipt"].manual_requirement, FIXTURES["receipt"].requirements)
