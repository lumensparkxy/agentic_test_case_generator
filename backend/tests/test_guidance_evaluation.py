from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import evaluate_guidance as evaluation
from app.services.guidance_service import apply_agent_guidance, guidance_scope
from app.contracts.guidance import MemoryGuidance


class GuidanceEvaluationTests(unittest.TestCase):
    def test_four_arms_have_matched_inputs_and_no_fabricated_offline_quality(self):
        with patch.object(evaluation, "run_stage") as run:
            report = evaluation.evaluate()
        run.assert_not_called()
        self.assertEqual(len(report["results"]), 32)
        for stage in evaluation.STAGES:
            rows = [r for r in report["results"] if r["stage"] == stage and r["fixture"] == "auth"]
            self.assertEqual(len({r["input_hash"] for r in rows}), 1)
            self.assertEqual([len(r["guidance"]["memories"]) for r in rows], [0, 0, 1, 1])
            self.assertTrue(all(r["review"]["coverage"] is None and r["estimated_cost_usd"] is None for r in rows))

    def test_role_specific_injection_does_not_cross_stage_scope(self):
        from google.adk.agents import LlmAgent, SequentialAgent

        manifest = evaluation.manifest_for("test_cases", "combined", "model")
        manifest = manifest.model_copy(
            update={
                "memories": (MemoryGuidance(id="m", revision=1, scope="project", text="USE CASE ONLY", stages=("use_cases",), source="human", reason="stage"),)
            }
        )
        planner = LlmAgent(name="CoveragePlannerAgent", model="model", instruction="Plan scenarios.")
        writer = LlmAgent(name="TestCaseGeneratorAgent", model="model", instruction="Write cases.")
        with guidance_scope(manifest):
            apply_agent_guidance(SequentialAgent(name="pipeline", sub_agents=[planner, writer]), "test_cases")
        self.assertIn("USE CASE ONLY", planner.instruction)
        self.assertNotIn("USE CASE ONLY", writer.instruction)
        self.assertIn("execution-ready-tests", writer.instruction)
        self.assertNotIn("execution-ready-tests", planner.instruction)

    def test_review_rubric_requires_independent_adjudication(self):
        with self.assertRaises(ValueError):
            evaluation.validate_review({"reviewer": "", "coverage": 1})
        self.assertIsNone(evaluation.validate_review(None)["unsupported_assumptions"])

    def test_adjudication_rejects_judgment_for_another_output(self):
        report = {"results": [{"id": "fixture/use_cases/combined/1", "output_hash": "actual"}]}
        with self.assertRaises(ValueError):
            evaluation.adjudicate(
                report,
                {
                    "fixture/use_cases/combined/1": {
                        "output_hash": "different",
                        "reviewer": "reviewer",
                        "coverage": 1,
                        "unsupported_assumptions": 0,
                        "repeated_reviewer_corrections": 0,
                    }
                },
            )
