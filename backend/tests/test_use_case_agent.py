from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import use_case_agent
from app.agents.scenario_assessment import assessment_input, CRITERIA
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
    def test_single_distinct_behavior_can_pass_without_category_companions(self) -> None:
        req = Requirement(id="REQ-001", text="Non-creators cannot cancel a booking; its status remains Confirmed.")
        for category in ("Happy Path", "Negative", "Authorization", "Boundary"):
            with self.subTest(category=category):
                output = _worker_output(use_case_agent._UseCaseShard(1, [req]))
                output["coverage_plan"][0]["scenarios"] = [
                    {
                        "id": "REQ-001-SCN-01",
                        "requirement_id": req.id,
                        "scenario_type": category,
                        "title": "Non-creator cannot cancel",
                        "objective": "A non-creator attempts cancellation; the booking remains Confirmed.",
                        "must_have": True,
                        "priority": "High",
                    }
                ]
                entry = assessment_input(output["coverage_plan"], [req], "")[0]
                output["semantic_rows"] = [
                    {
                        "requirement_id": req.id,
                        "scenario_id": "REQ-001-SCN-01",
                        "content_hash": entry["content_hash"],
                        **{criterion: {"passed": True, "reason": "Explicit trigger and outcome in the source."} for criterion in CRITERIA},
                    }
                ]
                result = use_case_agent._build_use_case_response(output, [req])
                self.assertTrue(result["review"]["structural_checks"]["approved"])
                self.assertTrue(result["approved"])
                self.assertEqual(len(result["coverage_plan"][0].scenarios), 1)
                output["semantic_rows"][0]["distinct_behavior"] = {"passed": False, "reason": "Duplicate behavior."}
                self.assertFalse(use_case_agent._build_use_case_response(output, [req])["approved"])
                output["semantic_rows"] = []
                self.assertFalse(use_case_agent._build_use_case_response(output, [req])["approved"])

    def test_structural_review_still_rejects_missing_or_invalid_scenarios(self) -> None:
        req = _requirement(1)
        output = _worker_output(use_case_agent._UseCaseShard(1, [req]))
        base = output["coverage_plan"][0]["scenarios"][0]
        base = {**base, "id": "REQ-001-SCN-01", "requirement_id": req.id}
        for scenarios in (
            [],
            [{**base, "must_have": False}],
            [{**base, "scenario_type": "Invented"}],
            [{**base, "scenario_type": ""}],
            [{**base, "id": " "}],
            [{**base, "requirement_id": "OTHER"}],
            [base, base],
        ):
            with self.subTest(scenarios=scenarios):
                review = use_case_agent._heuristic_use_case_review(
                    output["requirement_analysis"], [{"requirement_id": req.id, "scenarios": scenarios}], [req], 90
                )
                self.assertFalse(review["approved"])
                self.assertTrue(review["blocking_issues"])

    def test_missing_model_groups_are_reported_as_fallback_before_normalization(self) -> None:
        for field in ("coverage_plan", "requirement_analysis", "empty_scenarios"):

            def incomplete_worker(shard, **kwargs):
                output = _worker_output(shard, **kwargs)
                if field == "empty_scenarios":
                    output["coverage_plan"][0]["scenarios"] = []
                else:
                    output[field] = output[field][:1]
                return output

            with (
                self.subTest(field=field),
                patch.object(use_case_agent, "_get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
                patch.object(use_case_agent, "_run_single_use_case_shard_workflow_sync", side_effect=incomplete_worker),
            ):
                result = use_case_agent.generate_use_cases(_payload(2))
                self.assertEqual(len(result["coverage_plan"]), 2)
                self.assertTrue(result["workflow_diagnostics"]["used_fallback"])
                self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 1)

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
        self.assertFalse(result["approved"])
        self.assertNotEqual(result["review"]["semantic_assessment"]["status"], "passed")
        self.assertEqual(result["workflow_diagnostics"]["shard_count"], 3)
        self.assertEqual(result["workflow_diagnostics"]["worker_count"], 3)
        self.assertEqual(result["workflow_diagnostics"]["failed_shard_count"], 0)
        self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 0)
        self.assertTrue(all(len(group.scenarios) == 2 for group in result["coverage_plan"]))

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
        self.assertTrue(all(len(group.scenarios) == 2 for group in result["coverage_plan"]))

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
        self.assertFalse(result["approved"])
        self.assertNotEqual(result["review"]["semantic_assessment"]["status"], "passed")
        self.assertTrue(result["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(result["workflow_diagnostics"]["failed_shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["failure_reason"], "shard_fallback")
        self.assertTrue(any("shard-02 failed" in warning for warning in result["workflow_diagnostics"]["warnings"]))
        for group in result["coverage_plan"]:
            if group.requirement_id in {"REQ-001", "REQ-002", "REQ-005"}:
                self.assertEqual(len(group.scenarios), 2)


if __name__ == "__main__":
    unittest.main()
