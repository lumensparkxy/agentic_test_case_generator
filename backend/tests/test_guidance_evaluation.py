from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import evaluate_guidance as evaluation
from app.services.guidance_service import apply_agent_guidance, guidance_scope, build_manifest
from app.contracts.guidance import MemoryGuidance


class GuidanceEvaluationTests(unittest.TestCase):
    def test_held_test_case_entry_point_does_not_inherit_enabled_use_case_guidance(self):
        with patch.dict("os.environ", {"ADK_SKILLS_ENABLED": "true", "ADK_MEMORY_ENABLED": "true", "ADK_GUIDANCE_STAGES": "requirements,use_cases"}):
            held = build_manifest(
                "test_cases",
                "model",
                memories=[MemoryGuidance(id="m", revision=1, scope="project", text="lesson", stages=("use_cases",), source="human", reason="stage")],
            )
            enabled = build_manifest("use_cases", "model")
        self.assertFalse(held.skills_enabled)
        self.assertFalse(held.memory_enabled)
        self.assertEqual(held.skills, ())
        self.assertEqual(held.memories, ())
        self.assertTrue(enabled.skills_enabled)
        self.assertTrue(enabled.memory_enabled)

    def test_test_case_validation_catches_silent_contract_drops(self):
        output = {"test_cases": [{"linked_requirement_ids": ["REQ-001"]}], "generation_evidence": {"final_test_case_count": 2}}
        errors = evaluation.validate_artifact("test_cases", output, [{"id": "REQ-001"}, {"id": "REQ-002"}])
        self.assertIn("delivered_case_count_differs_from_generation_evidence", errors)
        self.assertIn("missing_delivered_requirement_coverage", errors)

    def test_automation_validation_rejects_truncated_or_missing_tests_without_execution(self):
        self.assertIn("missing_test_functions", evaluation.validate_artifact("automation", {"notes": "# No test body"}, [{}]))
        self.assertIn("invalid_python_section_0", evaluation.validate_artifact("automation", {"notes": "def test_example("}, [{}]))
        self.assertEqual(evaluation.validate_artifact("automation", {"notes": "def test_example():\n    raise RuntimeError('never execute')"}, [{}]), [])

    def test_workflow_fallback_cannot_be_counted_as_model_quality(self):
        self.assertTrue(evaluation.contains_fallback({"workflow_diagnostics": {"used_fallback": True}}))
        self.assertTrue(evaluation.contains_fallback({"case_diagnostics": [{"status": "fallback"}]}))
        self.assertFalse(evaluation.contains_fallback({"case_diagnostics": [{"status": "manual"}], "used_fallback": False}))

    def test_checkpoint_receives_each_completed_sample(self):
        counts = []
        evaluation.evaluate(stages=("requirements",), on_result=lambda results: counts.append(len(results)))
        self.assertEqual(counts, list(range(1, 9)))

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
