from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.test_case_agent import (
    _compute_grounded_context_metrics,
    _compute_planned_scenario_metrics,
    _fallback_coverage_plan,
    _fallback_raw_test_cases,
    _prepare_workflow_inputs,
)
from app.models import ArtifactSource, EnrichInput, GroundedContext, Requirement, TestCaseTemplate


class GroundedContextMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requirements = [Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password.")]
        self.context = EnrichInput(
            requirements=self.requirements,
            notes="Grounded context test",
            grounded_context=GroundedContext(
                artifact_sources=[
                    ArtifactSource(id="ART-APP-01", source_type="app", label="Application", url="https://example.com/app"),
                    ArtifactSource(id="ART-PROTO-01", source_type="prototype", label="Prototype", url="https://example.com/prototype"),
                ],
                summary="Two artifacts were analyzed.",
            ),
        )

    def test_compute_grounded_context_metrics_counts_source_refs(self) -> None:
        test_cases = [
            {
                "id": "TC-001",
                "title": "Source-backed case",
                "source_refs": ["ART-APP-01"],
            }
        ]

        metrics = _compute_grounded_context_metrics(test_cases, self.context)

        self.assertEqual(metrics["grounded_artifact_count"], 2)
        self.assertEqual(metrics["source_backed_test_cases"], 1)
        self.assertEqual(metrics["artifact_reference_coverage_ratio"], 0.5)
        self.assertEqual(metrics["unreferenced_artifacts"], ["ART-PROTO-01"])

    def test_prepare_workflow_inputs_includes_grounded_context_summary(self) -> None:
        template = TestCaseTemplate(name="default", format="table", fields=["id", "title"])

        _, context_text, _ = _prepare_workflow_inputs(self.requirements, self.context, template)

        self.assertIn("Grounded context summary", context_text)
        self.assertIn("ART-APP-01", context_text)

    def test_fallback_raw_test_cases_include_first_grounded_source_ref(self) -> None:
        raw_test_cases = _fallback_raw_test_cases(self.requirements, self.context)

        self.assertTrue(raw_test_cases)
        self.assertEqual(raw_test_cases[0]["source_refs"], ["ART-APP-01"])

    def test_fallback_raw_test_cases_cover_all_planned_scenarios(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall allow only finance administrators to export Approved expense reports to CSV.",
            )
        ]
        coverage_plan = _fallback_coverage_plan(requirements)

        raw_test_cases = _fallback_raw_test_cases(requirements, None, coverage_plan=coverage_plan)
        scenario_metrics = _compute_planned_scenario_metrics(coverage_plan, raw_test_cases, requirements)

        self.assertEqual(scenario_metrics["missing_scenarios"], [])
        self.assertEqual(
            scenario_metrics["covered_planned_scenarios"],
            scenario_metrics["planned_scenarios_total"],
        )


if __name__ == "__main__":
    unittest.main()
