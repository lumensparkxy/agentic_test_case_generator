from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import evaluate_orchestrator


class OrchestratorEvaluationTests(unittest.TestCase):
    def _fixture(self, name: str):
        return evaluate_orchestrator._load_json(REPO_ROOT / "scripts" / "benchmark_orchestrator_inputs" / f"{name}.json")

    def _expectation_path(self, name: str):
        return REPO_ROOT / "scripts" / "benchmark_orchestrator_expectations" / f"{name}.json"

    def test_v2_two_requirement_change_prefers_precise_impact_update(self) -> None:
        fixture_name = "v2_two_requirement_change"
        fixture = self._fixture(fixture_name)

        result = evaluate_orchestrator._build_benchmark_result(
            REPO_ROOT / "scripts" / "benchmark_orchestrator_inputs" / f"{fixture_name}.json",
            self._expectation_path(fixture_name),
            fixture,
        )

        self.assertTrue(result["expectation_result"]["all_met"])
        self.assertEqual(result["primary_action"]["action"], "analyze_impact")
        self.assertEqual(result["status"]["current_stage"], "impact_analysis")
        self.assertTrue(result["resumability"]["primary_action_matches"])
        self.assertEqual(result["metrics"]["changed_items_detected"], 2)
        self.assertEqual(result["metrics"]["impact_update_updated_count"], 2)
        self.assertEqual(result["metrics"]["impact_update_preserved_count"], 8)
        self.assertEqual(result["metrics"]["impact_false_update_recommendations"], 0)
        self.assertEqual(result["metrics"]["full_regenerate_false_update_recommendations"], 8)
        self.assertEqual(result["metrics"]["false_update_recommendations_avoided"], 8)
        self.assertEqual(result["metrics"]["impact_precision"], 1.0)
        self.assertEqual(result["metrics"]["unchanged_preservation_ratio"], 1.0)

    def test_governance_blocks_apply_update_but_leaves_analysis_available(self) -> None:
        fixture_name = "v2_approval_gate"
        fixture = self._fixture(fixture_name)

        result = evaluate_orchestrator._build_benchmark_result(
            REPO_ROOT / "scripts" / "benchmark_orchestrator_inputs" / f"{fixture_name}.json",
            self._expectation_path(fixture_name),
            fixture,
        )

        self.assertTrue(result["expectation_result"]["all_met"])
        self.assertEqual(result["primary_action"]["action"], "apply_update")
        self.assertFalse(result["primary_action"]["enabled"])
        self.assertEqual(result["governance"]["mutation_blocker_codes"], ["missing_approval"])
        self.assertTrue(result["governance"]["analysis_available"])
        self.assertTrue(result["governance"]["analysis_available_while_mutation_blocked"])

    def test_strict_orchestrator_evaluation_passes_all_fixture_expectations(self) -> None:
        input_dir = REPO_ROOT / "scripts" / "benchmark_orchestrator_inputs"
        expectation_dir = REPO_ROOT / "scripts" / "benchmark_orchestrator_expectations"
        payloads = evaluate_orchestrator._load_payloads(input_dir, expectation_dir)
        results = [evaluate_orchestrator._build_benchmark_result(input_path, expectation_path, fixture) for input_path, expectation_path, fixture in payloads]
        overall = evaluate_orchestrator._build_overall_summary(results, strict=True)

        self.assertEqual(overall["benchmark_count"], 3)
        self.assertTrue(overall["all_expectations_met"])
        self.assertEqual(overall["average_impact_precision"], 1.0)
        self.assertEqual(overall["average_unchanged_preservation_ratio"], 1.0)
        self.assertEqual(overall["total_false_update_recommendations_avoided"], 16)
        self.assertEqual(overall["resumability_match_count"], 3)


if __name__ == "__main__":
    unittest.main()
