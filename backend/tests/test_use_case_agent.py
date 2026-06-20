from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import use_case_agent
from app.models import GenerateTestCasesInput, Requirement, TestCaseTemplate


def _requirement(index: int) -> Requirement:
    return Requirement(
        id=f"REQ-{index:03d}",
        text=f"Users can complete workflow step {index} with validation and error handling.",
        review_status="Approved",
    )


def _payload(requirement_count: int) -> GenerateTestCasesInput:
    return GenerateTestCasesInput(
        requirements=[_requirement(index) for index in range(1, requirement_count + 1)],
        template=TestCaseTemplate(
            name="Standard QA",
            format="structured",
            fields=["id", "title", "steps", "expected_result"],
        ),
    )


def _worker_output(shard, **_kwargs):
    requirement_analysis = [
        {
            "requirement_id": requirement.id,
            "requirement_text": requirement.text,
            "business_rules": [
                {
                    "id": f"{requirement.id}-BR-01",
                    "requirement_id": requirement.id,
                    "title": f"Rule for {requirement.id}",
                    "description": requirement.text,
                    "rule_type": "Business",
                }
            ],
            "field_constraints": [],
            "role_permissions": [],
            "state_transitions": [],
            "risk_signals": [],
            "suggested_scenarios": ["Happy Path", "Negative"],
            "dependencies": [],
        }
        for requirement in shard.requirements
    ]
    coverage_plan = [
        {
            "requirement_id": requirement.id,
            "requirement_text": requirement.text,
            "scenarios": [
                {
                    "id": "duplicated-scenario-id",
                    "requirement_id": requirement.id,
                    "scenario_type": "Happy Path",
                    "title": f"Happy path for {requirement.id}",
                    "objective": "Verify the primary flow.",
                    "priority": "High",
                    "must_have": True,
                },
                {
                    "id": "duplicated-scenario-id",
                    "requirement_id": requirement.id,
                    "scenario_type": "Negative",
                    "title": f"Negative path for {requirement.id}",
                    "objective": "Verify rejected invalid input.",
                    "priority": "High",
                    "must_have": True,
                },
            ],
        }
        for requirement in shard.requirements
    ]
    return {
        "requirement_analysis": requirement_analysis,
        "coverage_plan": coverage_plan,
        "workflow_diagnostics": {
            "status": "completed",
            "used_fallback": False,
            "warnings": [],
            "parser_failures": [],
            "parser_recoveries": [],
        },
    }


class UseCaseAgentTests(unittest.TestCase):
    def test_parallel_use_case_generation_merges_in_original_requirement_order(self) -> None:
        payload = _payload(5)

        with (
            patch("app.agents.use_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.use_case_agent._run_single_use_case_shard_workflow_sync", side_effect=_worker_output) as worker_mock,
        ):
            result = use_case_agent.generate_use_cases(payload)

        self.assertEqual(worker_mock.call_count, 3)
        self.assertEqual([item.requirement_id for item in result["requirement_analysis"]], [requirement.id for requirement in payload.requirements])
        self.assertEqual([item.requirement_id for item in result["coverage_plan"]], [requirement.id for requirement in payload.requirements])
        self.assertTrue(result["approved"])
        self.assertEqual(result["workflow_diagnostics"]["shard_count"], 3)
        self.assertEqual(result["workflow_diagnostics"]["worker_count"], 3)
        self.assertEqual(result["workflow_diagnostics"]["failed_shard_count"], 0)
        self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 0)

    def test_duplicate_scenario_ids_are_normalized_after_merge(self) -> None:
        payload = _payload(4)

        with (
            patch("app.agents.use_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.use_case_agent._run_single_use_case_shard_workflow_sync", side_effect=_worker_output),
        ):
            result = use_case_agent.generate_use_cases(payload)

        scenario_ids = [scenario.id for plan in result["coverage_plan"] for scenario in plan.scenarios]
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))
        self.assertEqual([scenario.id for scenario in result["coverage_plan"][0].scenarios[:2]], ["REQ-001-SCN-01", "REQ-001-SCN-02"])
        self.assertEqual([scenario.id for scenario in result["coverage_plan"][1].scenarios[:2]], ["REQ-002-SCN-01", "REQ-002-SCN-02"])
        self.assertTrue(result["workflow_diagnostics"]["merge_warnings"])
        self.assertEqual(result["coverage_metrics"]["duplicate_scenario_ids"], [])

    def test_small_inputs_use_one_sequential_shard(self) -> None:
        payload = _payload(2)

        with (
            patch("app.agents.use_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.use_case_agent._run_single_use_case_shard_workflow_sync", side_effect=_worker_output) as worker_mock,
        ):
            result = use_case_agent.generate_use_cases(payload)

        self.assertEqual(worker_mock.call_count, 1)
        self.assertEqual(result["workflow_diagnostics"]["shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["worker_count"], 1)

    def test_failed_shard_falls_back_without_corrupting_other_shards(self) -> None:
        payload = _payload(5)

        def maybe_fail(shard, **kwargs):
            if shard.index == 2:
                raise RuntimeError("synthetic worker failure")
            return _worker_output(shard, **kwargs)

        with (
            patch("app.agents.use_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.use_case_agent._run_single_use_case_shard_workflow_sync", side_effect=maybe_fail),
        ):
            result = use_case_agent.generate_use_cases(payload)

        self.assertEqual([item.requirement_id for item in result["coverage_plan"]], [requirement.id for requirement in payload.requirements])
        self.assertTrue(result["approved"])
        self.assertTrue(result["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(result["workflow_diagnostics"]["failed_shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["failure_reason"], "shard_fallback")
        self.assertTrue(any("shard-02 failed" in warning for warning in result["workflow_diagnostics"]["warnings"]))


if __name__ == "__main__":
    unittest.main()
